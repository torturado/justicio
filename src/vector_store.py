"""Qdrant collection with the chunks of every source, searched with dense and BM25 vectors.

Each point payload holds the chunk (``text``, ``chunk_title``, ``chunk_index``) and the metadata of its
document. A document is identified by ``source_name`` and ``identificador``.
"""

import logging as lg
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient, QdrantClient, models

from src.embeddings import Embedder

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
# BM25 vectors are computed by the Qdrant server (requires a client created with cloud_inference=True)
BM25_MODEL = "qdrant/bm25"
BM25_OPTIONS = {"language": "spanish"}
PAYLOAD_INDEXES: dict[str, models.PayloadSchemaType | models.TextIndexParams] = {
    "source_name": models.PayloadSchemaType.KEYWORD,
    "source_type": models.PayloadSchemaType.KEYWORD,
    "identificador": models.PayloadSchemaType.KEYWORD,
    "numero_oficial": models.PayloadSchemaType.KEYWORD,
    "bloque_titulo": models.TextIndexParams(
        type=models.TextIndexType.TEXT,
        tokenizer=models.TokenizerType.WORD,
        lowercase=True,
    ),
    # Date (YYYY-MM-DD) from which the indexed text of a consolidated norm is outdated and must be reloaded
    "fecha_revision": models.PayloadSchemaType.DATETIME,
    # Watermarks of the incremental loads
    "fecha_publicacion": models.PayloadSchemaType.DATETIME,
    "fecha_actualizacion": models.PayloadSchemaType.DATETIME,
}

UPSERT_BATCH_SIZE = 64


class Chunk(BaseModel):
    """A piece of a document ready to be indexed."""

    # Context header (document title, section, article...). It is embedded together with the text.
    title: str
    text: str
    # Metadata of the document (and of the article or block the chunk belongs to)
    metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchHit:
    id: str
    payload: dict[str, Any]
    score: float


def point_id(source_name: str, identificador: str, chunk_index: int) -> str:
    """Deterministic point ID, so that reloading a document overwrites its points."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_name}/{identificador}/{chunk_index}"))


def match(key: str, value: str) -> models.FieldCondition:
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def document_conditions(source_name: str, identificador: str) -> list[models.Condition]:
    return [match("source_name", source_name), match("identificador", identificador)]


def _check_schema(collection: models.CollectionInfo, collection_name: str, dimensions: int) -> None:
    params = collection.config.params
    dense = params.vectors.get(DENSE_VECTOR) if isinstance(params.vectors, dict) else None
    if dense is None or dense.size != dimensions or SPARSE_VECTOR not in (params.sparse_vectors or {}):
        raise RuntimeError(
            f"Collection [{collection_name}] does not have the expected vectors "
            f"('{DENSE_VECTOR}' with {dimensions} dimensions and '{SPARSE_VECTOR}'). "
            "Delete it and load the documents again."
        )


def hybrid_query(
    query: str,
    query_vector: list[float],
    limit: int,
    prefetch_limit: int,
    query_filter: models.Filter | None = None,
) -> dict[str, Any]:
    """query_points arguments of a hybrid search: dense and BM25 candidates fused with Reciprocal Rank Fusion.

    RRF scores only express the rank, they are not similarities.
    """
    return {
        "prefetch": [
            models.Prefetch(query=query_vector, using=DENSE_VECTOR, limit=prefetch_limit, filter=query_filter),
            models.Prefetch(
                query=models.Document(text=query, model=BM25_MODEL, options=BM25_OPTIONS),
                using=SPARSE_VECTOR,
                limit=prefetch_limit,
                filter=query_filter,
            ),
        ],
        "query": models.FusionQuery(fusion=models.Fusion.RRF),
        "query_filter": query_filter,
        "limit": limit,
        "with_payload": True,
    }


class VectorIndex:
    """Write access used by the ETL: creates the collection and replaces documents."""

    def __init__(self, client: QdrantClient, collection_name: str, embedder: Embedder):
        self._client = client
        self.collection_name = collection_name
        self._embedder = embedder

    def ensure_collection(self) -> None:
        """Create the collection if it is missing, or check that the existing one has the expected schema."""
        if not self._client.collection_exists(self.collection_name):
            self._client.create_collection(
                self.collection_name,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self._embedder.dimensions, distance=models.Distance.COSINE, on_disk=True
                    )
                },
                sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)},
                # Searches use int8 vectors kept in RAM; the original vectors stay on disk
                quantization_config=models.ScalarQuantization(
                    scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
                ),
                on_disk_payload=True,
            )
            lg.getLogger(__name__).info("Created collection [%s]", self.collection_name)
        collection = self._client.get_collection(self.collection_name)
        _check_schema(collection, self.collection_name, self._embedder.dimensions)
        payload_schema = collection.payload_schema or {}
        for field, schema in PAYLOAD_INDEXES.items():
            if field not in payload_schema:
                self._client.create_payload_index(self.collection_name, field, field_schema=schema)

    def has_document(self, source_name: str, identificador: str) -> bool:
        count_filter = models.Filter(must=document_conditions(source_name, identificador))
        return self._client.count(self.collection_name, count_filter=count_filter, exact=True).count > 0

    def delete_document(self, source_name: str, identificador: str) -> None:
        self._delete(models.Filter(must=document_conditions(source_name, identificador)))

    def replace_document(self, source_name: str, identificador: str, chunks: Sequence[Chunk]) -> None:
        """Index the chunks of a document, replacing any version previously indexed.

        New chunks overwrite the points of the previous version in place and the leftover points are deleted
        afterwards, so the document stays searchable during the replacement.
        """
        vectors = self._embedder.embed_documents([(chunk.title, chunk.text) for chunk in chunks])
        points = [
            models.PointStruct(
                id=point_id(source_name, identificador, index),
                vector={
                    DENSE_VECTOR: vector,
                    SPARSE_VECTOR: models.Document(
                        text=f"{chunk.title}\n{chunk.text}", model=BM25_MODEL, options=BM25_OPTIONS
                    ),
                },
                payload={
                    **chunk.metadata,
                    "source_name": source_name,
                    "identificador": identificador,
                    "chunk_title": chunk.title,
                    "chunk_index": index,
                    "text": chunk.text,
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            self._client.upsert(self.collection_name, points=points[start : start + UPSERT_BATCH_SIZE])
        leftover = models.FieldCondition(key="chunk_index", range=models.Range(gte=len(points)))
        self._delete(models.Filter(must=[*document_conditions(source_name, identificador), leftover]))

    def identificadores(self, scroll_filter: models.Filter) -> set[str]:
        """Identifiers of the documents with at least one chunk matching the filter."""
        identificadores: set[str] = set()
        offset = None
        while True:
            points, offset = self._client.scroll(
                self.collection_name,
                scroll_filter=scroll_filter,
                limit=1000,
                offset=offset,
                with_payload=["identificador"],
            )
            identificadores.update(point.payload["identificador"] for point in points)
            if offset is None:
                return identificadores

    def latest_date(self, field: str, scroll_filter: models.Filter) -> date | None:
        """Latest value of an indexed date field among the chunks matching the filter."""
        points, _ = self._client.scroll(
            self.collection_name,
            scroll_filter=scroll_filter,
            limit=1,
            order_by=models.OrderBy(key=field, direction=models.Direction.DESC),
            with_payload=[field],
        )
        if not points:
            return None
        return date.fromisoformat(str(points[0].payload[field])[:10])

    def _delete(self, points_filter: models.Filter) -> None:
        self._client.delete(self.collection_name, points_selector=models.FilterSelector(filter=points_filter))


class VectorSearch:
    """Read access used by the service; works with a read-only API key."""

    def __init__(self, client: AsyncQdrantClient, collection_name: str, dimensions: int):
        self._client = client
        self.collection_name = collection_name
        self._dimensions = dimensions

    async def check_collection(self) -> None:
        if not await self._client.collection_exists(self.collection_name):
            raise RuntimeError(
                f"Collection [{self.collection_name}] does not exist. Load documents with the ETL first "
                "(python -m src.etls.cli --help)."
            )
        collection = await self._client.get_collection(self.collection_name)
        _check_schema(collection, self.collection_name, self._dimensions)

    async def search(
        self,
        query: str,
        query_vector: list[float],
        limit: int,
        prefetch_limit: int,
        query_filter: models.Filter | None = None,
    ) -> list[SearchHit]:
        response = await self._client.query_points(
            self.collection_name, **hybrid_query(query, query_vector, limit, prefetch_limit, query_filter)
        )
        return [
            SearchHit(id=str(point.id), payload=point.payload or {}, score=point.score) for point in response.points
        ]

    async def retrieve(self, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Payloads of the existing points among the given IDs."""
        if not ids:
            return {}
        points = await self._client.retrieve(self.collection_name, ids=list(ids), with_payload=True)
        return {str(point.id): point.payload or {} for point in points}

    async def sample(self, limit: int) -> list[dict[str, Any]]:
        """Payloads of some chunks (in point ID order, which is effectively random)."""
        points, _ = await self._client.scroll(self.collection_name, limit=limit, with_payload=["text", "chunk_title"])
        return [point.payload or {} for point in points]

    async def count(self, count_filter: models.Filter | None = None) -> int:
        return (await self._client.count(self.collection_name, count_filter=count_filter, exact=False)).count
