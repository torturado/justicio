import asyncio
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, TypeVar

from qdrant_client import models

from src.embeddings import Embedder, document_text
from src.legal_query import build_legal_query_filters
from src.reranking import Reranker
from src.settings import Settings
from src.vector_store import SearchHit, VectorSearch, point_id

# The best hybrid results kept among the selected sources even when the reranker scores them lower. A known
# relevant article ranked third in hybrid search was dropped by the reranker.
HYBRID_HEAD_SIZE = 3
# Adjacent chunks of the same article or document added on each side of a selected chunk
NEIGHBOR_DISTANCE = 1

_SOURCE_TYPE_LABELS = {
    "Legislación consolidada": "texto consolidado",
    "Boletin": "disposición publicada en boletín",
}
_URL_FIELDS = ("url", "url_html", "url_html_consolidada", "url_eli", "url_boletin", "url_pdf")

T = TypeVar("T")


@dataclass(frozen=True)
class RetrievedSource:
    id: str
    payload: Mapping[str, Any]
    hybrid_score: float
    reranker_score: float | None
    # Chunk text with its adjacent chunks, given to the LLM
    text: str

    @property
    def url(self) -> str | None:
        for key in _URL_FIELDS:
            value = self.payload.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def title(self) -> str:
        """Title and provenance shown to the LLM."""
        payload = self.payload
        source_type = payload.get("source_type")
        source = ", ".join(
            part for part in (payload.get("source_name"), _SOURCE_TYPE_LABELS.get(source_type, source_type)) if part
        )
        details = [
            ("Fuente", source),
            ("Identificador", payload.get("identificador")),
            ("Número oficial", payload.get("numero_oficial")),
            ("Apartado", payload.get("bloque_titulo")),
            ("Versión vigente desde", payload.get("fecha_vigencia_version")),
            ("Actualización", payload.get("fecha_actualizacion")),
            ("Publicación", payload.get("fecha_publicacion") if source_type == "Boletin" else None),
            ("URL", self.url),
        ]
        provenance = "; ".join(f"{label}: {value}" for label, value in details if value)
        title = payload.get("chunk_title") or payload.get("titulo") or "Fuente oficial"
        return f"{title} ({provenance})" if provenance else title


def model_text(payload: Mapping[str, Any]) -> str:
    return document_text(payload.get("chunk_title") or "", payload.get("text") or "")


def select_hits(
    hits: Sequence[SearchHit], reranker_scores: Sequence[float], top_k: int, head_size: int = HYBRID_HEAD_SIZE
) -> list[tuple[SearchHit, float]]:
    """The top_k hits by reranker score, keeping the head_size best hybrid hits.

    A protected hit outside the reranked top_k replaces the lowest-scored unprotected selected hit. The
    result is sorted by reranker score.
    """
    ranked = sorted(zip(hits, reranker_scores, strict=True), key=lambda item: item[1], reverse=True)
    selected = ranked[:top_k]
    score_by_id = {hit.id: score for hit, score in ranked}
    protected = hits[: min(head_size, top_k)]
    protected_ids = {hit.id for hit in protected}
    selected_ids = {hit.id for hit, _ in selected}
    for hit in protected:
        if hit.id in selected_ids:
            continue
        replacement = next(
            (index for index in range(len(selected) - 1, -1, -1) if selected[index][0].id not in protected_ids),
            None,
        )
        if replacement is None:
            continue
        selected_ids.discard(selected[replacement][0].id)
        selected[replacement] = (hit, score_by_id[hit.id])
        selected_ids.add(hit.id)
    selected.sort(key=lambda item: item[1], reverse=True)
    return selected


def neighbor_ids(payload: Mapping[str, Any], distance: int = NEIGHBOR_DISTANCE) -> list[str]:
    index = payload.get("chunk_index")
    if not isinstance(index, int):
        return []
    return [
        point_id(payload["source_name"], payload["identificador"], index + offset)
        for offset in range(-distance, distance + 1)
        if offset and index + offset >= 0
    ]


def expanded_text(
    payload: Mapping[str, Any],
    neighbors: Mapping[str, Mapping[str, Any]],
    excluded_ids: set[str],
    distance: int = NEIGHBOR_DISTANCE,
) -> str:
    """The chunk text joined with its adjacent chunks of the same block (article) and document.

    Excluded chunks (other selected sources) are left out so that the context does not repeat them.
    """
    index = payload.get("chunk_index")
    if not isinstance(index, int):
        return payload.get("text") or ""
    parts = []
    for offset in range(-distance, distance + 1):
        if offset == 0:
            parts.append(payload.get("text") or "")
            continue
        neighbor_id = point_id(payload["source_name"], payload["identificador"], index + offset)
        neighbor = neighbors.get(neighbor_id)
        if neighbor is None or neighbor_id in excluded_ids or neighbor.get("bloque_id") != payload.get("bloque_id"):
            continue
        parts.append(neighbor.get("text") or "")
    return "\n".join(part for part in parts if part)


class Retriever:
    """Hybrid search, cross-encoder reranking and context expansion.

    Model inference runs on the given executor, which should have a single thread: PyTorch already uses
    every core for one call, and the event loop and the default executor stay free.
    """

    def __init__(
        self,
        settings: Settings,
        embedder: Embedder,
        reranker: Reranker,
        search: VectorSearch,
        executor: Executor,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._reranker = reranker
        self._search = search
        self._executor = executor

    async def _infer(self, function: Callable[..., T], *args: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, function, *args)

    async def search(self, query: str, sources: Sequence[str]) -> list[SearchHit]:
        """Hybrid candidates from the given sources.

        Filters for the norms and article named in the question are tried first; each one falls back to
        the next, broader one when it returns nothing.
        """
        query_vector = await self._infer(self._embedder.embed_query, query)
        sources_condition = models.FieldCondition(key="source_name", match=models.MatchAny(any=list(sources)))
        for legal_filter in [*build_legal_query_filters(query), None]:
            conditions: list[models.Condition] = (
                [sources_condition] if legal_filter is None else [sources_condition, legal_filter]
            )
            hits = await self._search.search(
                query,
                query_vector,
                self._settings.reranker_candidates,
                self._settings.prefetch_limit,
                models.Filter(must=conditions),
            )
            if hits:
                return hits
        return []

    async def rerank(self, query: str, hits: Sequence[SearchHit]) -> list[tuple[SearchHit, float]]:
        """All the hits sorted by reranker score."""
        scores = await self._infer(self._reranker.score, query, [model_text(hit.payload) for hit in hits])
        return sorted(zip(hits, scores, strict=True), key=lambda item: item[1], reverse=True)

    async def retrieve(self, query: str, sources: Sequence[str]) -> list[RetrievedSource]:
        hits = await self.search(query, sources)
        if not hits:
            return []
        scores = await self._infer(self._reranker.score, query, [model_text(hit.payload) for hit in hits])
        selected = select_hits(hits, scores, self._settings.top_k_results)
        selected_ids = {hit.id for hit, _ in selected}
        neighbor_point_ids = {neighbor for hit, _ in selected for neighbor in neighbor_ids(hit.payload)}
        neighbors = await self._search.retrieve(sorted(neighbor_point_ids - selected_ids))
        return [
            RetrievedSource(
                id=hit.id,
                payload=hit.payload,
                hybrid_score=hit.score,
                reranker_score=score,
                text=expanded_text(hit.payload, neighbors, selected_ids),
            )
            for hit, score in selected
        ]
