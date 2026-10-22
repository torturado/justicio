import json
import logging as lg
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Optional

import requests
import typer
from qdrant_client import QdrantClient, models

from evaluation.embeddings.gemini import GeminiEmbedder
from evaluation.embeddings.questions import QUERIES
from src import vector_store as vector_store_module
from src.embeddings import SentenceTransformerEmbedder
from src.initialize import create_embedder, create_genai_client, create_qdrant_client
from src.settings import Settings, load_settings
from src.vector_store import Chunk, VectorIndex, hybrid_query

type Rows = list[dict[str, str]]
type Payload = dict[str, Any]
type Rank = int | None

DATASET_ID = "dariolopez/justicio-rag-embedding-qa"
DATASET_URL = "https://datasets-server.huggingface.co/rows"
PAGE_LENGTH = 100
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
DATASET_CACHE = CACHE_DIR / "dataset.json"
RESULT_LIMIT = 10
GEMINI_EMBEDDING_MODEL = "gemini-embedding-2"
GEMINI_EMBEDDING_DIMENSIONS = 1536

app = typer.Typer(add_completion=False, no_args_is_help=True)


type EvalEmbedder = GeminiEmbedder | SentenceTransformerEmbedder


def _make_dataset_embedder(spec: str, dimensions: Optional[int], settings: Settings) -> tuple[EvalEmbedder, int]:
    if spec == "gemini":
        if not os.environ.get("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is required when using the gemini embedder.")
        resolved_dimensions = dimensions or GEMINI_EMBEDDING_DIMENSIONS
        embedder = GeminiEmbedder(create_genai_client(settings), GEMINI_EMBEDDING_MODEL, resolved_dimensions)
        return embedder, resolved_dimensions
    if spec.startswith("st:") and spec[3:]:
        if dimensions is not None:
            raise typer.BadParameter("--dimensions applies only to the gemini embedder.")
        embedder = SentenceTransformerEmbedder(spec[3:])
        return embedder, embedder.dimensions
    raise typer.BadParameter("--embedder must be 'gemini' or 'st:<hugging-face-model-id>'.")


def _load_dataset() -> Rows:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if DATASET_CACHE.exists():
        return json.loads(DATASET_CACHE.read_text(encoding="utf-8"))
    rows, offset, total_rows = [], 0, None
    with requests.Session() as session:
        while total_rows is None or offset < total_rows:
            params = dict(dataset=DATASET_ID, config="default", split="train", offset=offset, length=PAGE_LENGTH)
            response = session.get(DATASET_URL, params=params, timeout=(10, 120))
            response.raise_for_status()
            page = response.json()
            total_rows = int(page.get("num_rows_total", 0)) if total_rows is None else total_rows
            page_rows = page.get("rows", [])
            if not page_rows:
                break
            for item in page_rows:
                try:
                    question, context, answer = (item["row"][key] for key in ("question", "context", "answer"))
                except (KeyError, TypeError) as error:
                    raise RuntimeError(f"Dataset row {offset + len(rows)} is malformed.") from error
                if not all(isinstance(value, str) and value.strip() for value in (question, context, answer)):
                    raise RuntimeError(f"Dataset row {offset + len(rows)} has a missing text field.")
                rows.append({"question": question, "context": context, "answer": answer})
            offset += len(page_rows)
            if len(page_rows) < PAGE_LENGTH:
                break
    if not rows:
        raise RuntimeError("The datasets-server API returned no rows.")
    if len(rows) != total_rows:
        raise RuntimeError(f"Downloaded {len(rows)} rows, but the API reports {total_rows} total rows.")
    temporary_path = DATASET_CACHE.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(DATASET_CACHE)
    return rows


def _rank(payloads: Sequence[Payload], relevant_index: int) -> Rank:
    return next(
        (rank for rank, payload in enumerate(payloads, 1) if payload["context_index"] == relevant_index),
        None,
    )


def _evaluate_dataset(
    client: QdrantClient,
    index: VectorIndex,
    model_embedder: EvalEmbedder,
    settings: Settings,
    slug: str,
    model_name: str,
    dimensions: int,
    collection_name: str,
    rows: Rows,
) -> None:
    corpus = list(dict.fromkeys(row["context"] for row in rows))
    context_indices = {context: i for i, context in enumerate(corpus)}
    questions = [row["question"] for row in rows]
    relevant_indices = [context_indices[row["context"]] for row in rows]
    if not corpus:
        raise RuntimeError("The dataset contains no unique contexts to index.")
    chunks = [Chunk(title="none", text=context, metadata={"context_index": i}) for i, context in enumerate(corpus)]
    print(f"Indexing {len(corpus)} unique contexts from {len(rows)} QA rows into {collection_name}.")
    index.replace_document("EVAL", "dataset-corpus", chunks)
    query_method = getattr(model_embedder, "embed_queries", None)
    query_vectors = query_method(questions) if query_method else [model_embedder.embed_query(q) for q in questions]
    if len(query_vectors) != len(questions):
        raise RuntimeError(f"Generated {len(query_vectors)} query vectors for {len(questions)} questions.")

    ranks: dict[str, list[Rank]] = {mode: [] for mode in ("dense", "bm25", "hybrid")}
    for question, query_vector, relevant_index in zip(questions, query_vectors, relevant_indices, strict=True):
        bm25_query = models.Document(
            text=question, model=vector_store_module.BM25_MODEL, options=vector_store_module.BM25_OPTIONS
        )
        searches = (
            ("dense", query_vector, vector_store_module.DENSE_VECTOR),
            ("bm25", bm25_query, vector_store_module.SPARSE_VECTOR),
        )
        for mode, query, vector_name in searches:
            result = client.query_points(
                collection_name, query=query, using=vector_name, limit=RESULT_LIMIT, with_payload=True
            )
            ranks[mode].append(_rank([point.payload for point in result.points], relevant_index))
        hybrid = client.query_points(
            collection_name, **hybrid_query(question, query_vector, RESULT_LIMIT, settings.prefetch_limit)
        )
        ranks["hybrid"].append(_rank([point.payload for point in hybrid.points], relevant_index))

    metrics: dict[str, dict[str, float]] = {}
    for mode, values in ranks.items():
        count = len(values)
        metrics[mode] = {f"recall@{k}": sum(r is not None and r <= k for r in values) / count for k in (1, 5, 10)}
        metrics[mode]["mrr@10"] = sum(1 / r for r in values if r is not None and r <= 10) / count
    print(f"\nDataset: {DATASET_ID} ({len(rows)} questions; {len(corpus)} unique contexts)")
    print(f"Embedder: {model_name} ({dimensions} dimensions)")
    print("mode       Recall@1  Recall@5  Recall@10  MRR@10")
    for mode, values in metrics.items():
        print("{:<10} {:>8.3f}  {:>8.3f}  {:>9.3f}  {:>6.3f}".format(mode, *values.values()))
    result_path = CACHE_DIR / f"results_{slug}_{dimensions}.json"
    with result_path.open("w", encoding="utf-8") as stream:
        json.dump(
            {
                "dataset": DATASET_ID,
                "embedder": model_name,
                "dimensions": dimensions,
                "collection": collection_name,
                "question_count": len(rows),
                "unique_context_count": len(corpus),
                "metrics": metrics,
                "ranks": ranks,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Results written to {result_path}.")


@app.command()
def dataset(
    embedder: str = typer.Option(..., "--embedder", help="'gemini' or 'st:<hugging-face-model-id>'."),
    dimensions: Optional[int] = typer.Option(None, "--dimensions", min=1),
    keep_collection: bool = typer.Option(False, "--keep-collection"),
) -> None:
    """Compare dense, BM25, and hybrid retrieval on the public QA dataset."""
    try:
        settings = load_settings()
        model_embedder, dimensions_value = _make_dataset_embedder(embedder, dimensions, settings)
        model_name = GEMINI_EMBEDDING_MODEL if embedder == "gemini" else embedder[3:]
        rows = _load_dataset()
        slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", embedder.lower())).strip("-")
        collection_name = f"eval_{slug}_{dimensions_value}"
        client = create_qdrant_client()
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        index = VectorIndex(client, collection_name, model_embedder)
        index.ensure_collection()
        try:
            _evaluate_dataset(
                client, index, model_embedder, settings, slug, model_name, dimensions_value, collection_name, rows
            )
        finally:
            if not keep_collection and client.collection_exists(collection_name):
                client.delete_collection(collection_name)
    except (RuntimeError, OSError, requests.RequestException, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


@app.command()
def index(collection: Optional[str] = typer.Option(None, "--collection", help="Existing Qdrant collection.")) -> None:
    """Check whether each configured BOE query retrieves its expected document."""
    try:
        settings = load_settings()
        collection = collection or settings.qdrant_collection
        embedder = create_embedder(settings)
        client = create_qdrant_client()
        if not client.collection_exists(collection):
            raise RuntimeError(f"Qdrant collection [{collection}] does not exist.")
        hits, top_k = 0, settings.top_k_results
        print(f"Checking {len(QUERIES)} questions against [{collection}] (hit@{top_k}).")
        for number, (expected_id, question) in enumerate(QUERIES, start=1):
            query_vector = embedder.embed_query(question)
            results = client.query_points(
                collection, **hybrid_query(question, query_vector, top_k, settings.prefetch_limit)
            ).points
            rank = next(
                (r for r, point in enumerate(results, 1) if point.payload["identificador"] == expected_id), None
            )
            hits += rank is not None
            status = f"rank={rank}" if rank is not None else "miss"
            print(f"{number:02}/{len(QUERIES):02} | {expected_id} | {status} | {question}")
        print(f"Hits: {hits}/{len(QUERIES)} @ {top_k}")
    except (RuntimeError, OSError, ValueError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error


if __name__ == "__main__":
    lg.basicConfig(level=lg.INFO)
    app()
