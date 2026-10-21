import logging as lg
import typing as tp
import uuid

from pydantic import BaseModel
from qdrant_client import QdrantClient, models

from src.embeddings import Embedder

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"
# BM25 vectors are computed by the Qdrant server (requires a client created with cloud_inference=True)
BM25_MODEL = "qdrant/bm25"
BM25_OPTIONS = {"language": "spanish"}
_PAYLOAD_INDEXES = {
    "metadata.identificador": models.PayloadSchemaType.KEYWORD,
    "metadata.fecha_revision": models.PayloadSchemaType.DATETIME,
    "metadata.numero_oficial": models.PayloadSchemaType.KEYWORD,
    "metadata.bloque_titulo": models.TextIndexParams(
        type=models.TextIndexType.TEXT,
        tokenizer=models.TokenizerType.WORD,
        lowercase=True,
    ),
}

UPSERT_BATCH_SIZE = 64


class Chunk(BaseModel):
    """A piece of a document ready to be indexed."""

    # Context header (document title, section, article...). It is embedded together with the text.
    title: str
    text: str
    # Document metadata. Must include "identificador", which groups all the chunks of a document.
    metadata: tp.Dict[str, tp.Any]


class VectorStore:
    """Hybrid (dense + BM25) index of document chunks stored in a Qdrant collection.

    Points keep the payload layout {"page_content": str, "metadata": dict}.
    """

    def __init__(self, client: QdrantClient, collection_name: str, embedder: Embedder, dimensions: int):
        self._client = client
        self.collection_name = collection_name
        self._embedder = embedder
        self._dimensions = dimensions

    def ensure_collection(self) -> None:
        """Create the collection if it is missing, or check that the existing one has the expected schema."""
        logger = lg.getLogger(self.ensure_collection.__name__)
        if not self._client.collection_exists(self.collection_name):
            self._create_collection()
            logger.info("Created collection [%s]", self.collection_name)
            return
        collection = self._client.get_collection(self.collection_name)
        params = collection.config.params
        dense = params.vectors.get(DENSE_VECTOR) if isinstance(params.vectors, dict) else None
        if dense is None or dense.size != self._dimensions or SPARSE_VECTOR not in (params.sparse_vectors or {}):
            raise RuntimeError(
                f"Collection [{self.collection_name}] does not have the expected vectors "
                f"('{DENSE_VECTOR}' with {self._dimensions} dimensions and '{SPARSE_VECTOR}'). "
                "Delete it and load the documents again."
            )
        payload_schema = collection.payload_schema or {}
        for field, schema in _PAYLOAD_INDEXES.items():
            if field not in payload_schema:
                self._client.create_payload_index(self.collection_name, field, field_schema=schema)

    def has_document(self, identificador: str) -> bool:
        return (
            self._client.count(self.collection_name, count_filter=_document_filter(identificador), exact=False).count
            > 0
        )

    def count(self) -> int:
        """Number of indexed chunks in this collection."""
        return self._client.count(self.collection_name, exact=False).count

    def delete_document(self, identificador: str) -> None:
        self._client.delete(
            self.collection_name, points_selector=models.FilterSelector(filter=_document_filter(identificador))
        )

    def replace_document(self, identificador: str, chunks: tp.Sequence[Chunk]) -> None:
        """Index the chunks of a document, removing any chunk previously indexed for it."""
        vectors = self._embedder.embed_documents([(chunk.title, chunk.text) for chunk in chunks])
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.collection_name}/{identificador}/{index}")),
                vector={
                    DENSE_VECTOR: vector,
                    SPARSE_VECTOR: models.Document(
                        text=f"{chunk.title}\n{chunk.text}", model=BM25_MODEL, options=BM25_OPTIONS
                    ),
                },
                payload={
                    "page_content": chunk.text,
                    "metadata": {**chunk.metadata, "identificador": identificador, "chunk_title": chunk.title},
                },
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors))
        ]
        # Embeddings are computed before deleting so that a failed embedding request keeps the previous version
        self.delete_document(identificador)
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            self._client.upsert(self.collection_name, points=points[start : start + UPSERT_BATCH_SIZE])

    def identificadores(self, scroll_filter: models.Filter) -> tp.Set[str]:
        """Identifiers of the documents with at least one chunk matching the filter."""
        identificadores = set()
        offset = None
        while True:
            points, offset = self._client.scroll(
                self.collection_name,
                scroll_filter=scroll_filter,
                limit=1000,
                offset=offset,
                with_payload=["metadata.identificador"],
            )
            identificadores.update(point.payload["metadata"]["identificador"] for point in points)
            if offset is None:
                return identificadores

    def search(
        self,
        query: str,
        query_vector: tp.List[float],
        limit: int,
        prefetch_limit: int,
        query_filter: tp.Optional[models.Filter] = None,
    ) -> tp.List[tp.Tuple[tp.Dict[str, tp.Any], float]]:
        """Hybrid search: dense and BM25 candidates fused with Reciprocal Rank Fusion.

        :return: (payload, score) pairs. RRF scores only express the rank, they are not similarities.
        """
        response = self._client.query_points(
            self.collection_name,
            prefetch=[
                models.Prefetch(query=query_vector, using=DENSE_VECTOR, limit=prefetch_limit, filter=query_filter),
                models.Prefetch(
                    query=models.Document(text=query, model=BM25_MODEL, options=BM25_OPTIONS),
                    using=SPARSE_VECTOR,
                    limit=prefetch_limit,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        )
        return [(point.payload, point.score) for point in response.points]

    def _create_collection(self) -> None:
        self._client.create_collection(
            self.collection_name,
            vectors_config={
                DENSE_VECTOR: models.VectorParams(size=self._dimensions, distance=models.Distance.COSINE, on_disk=True)
            },
            sparse_vectors_config={SPARSE_VECTOR: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            # Searches use int8 vectors kept in RAM; the original vectors stay on disk
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(type=models.ScalarType.INT8, always_ram=True)
            ),
            on_disk_payload=True,
        )
        self._client.create_payload_index(
            self.collection_name, "metadata.identificador", field_schema=models.PayloadSchemaType.KEYWORD
        )
        # Date (YYYY-MM-DD) from which the indexed text of a consolidated norm is outdated and must be reloaded
        self._client.create_payload_index(
            self.collection_name, "metadata.fecha_revision", field_schema=models.PayloadSchemaType.DATETIME
        )
        self._client.create_payload_index(
            self.collection_name,
            "metadata.numero_oficial",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        self._client.create_payload_index(
            self.collection_name,
            "metadata.bloque_titulo",
            field_schema=_PAYLOAD_INDEXES["metadata.bloque_titulo"],
        )


def _document_filter(identificador: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="metadata.identificador", match=models.MatchValue(value=identificador))]
    )
