from collections.abc import Sequence
from typing import Protocol

LOCAL_BATCH_SIZE = 8


class Embedder(Protocol):
    """Embedding interface used by the vector index."""

    dimensions: int

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> list[list[float]]: ...

    def embed_query(self, query: str) -> list[float]: ...


def document_text(title: str, text: str) -> str:
    """Text given to the models for a chunk: its context header (norm, article...) followed by its text."""
    if not title or title.casefold() == "none":
        return text
    return f"{title}\n{text}"


class SentenceTransformerEmbedder:
    """Embeds documents and queries with a local sentence-transformers model.

    Not thread-safe: the service calls it from its single inference thread.
    """

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        dimensions = self._model.get_embedding_dimension()
        if dimensions is None:
            raise RuntimeError(f"Could not determine the embedding dimensions for [{model_name}].")
        self.dimensions: int = dimensions
        self._query_prompt_name = "query" if "query" in self._model.prompts else None

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> list[list[float]]:
        """Embed (title, text) pairs, keeping article context alongside its text."""
        return self._encode([document_text(title, text) for title, text in documents])

    def embed_query(self, query: str) -> list[float]:
        return self.embed_queries([query])[0]

    def embed_queries(self, queries: Sequence[str]) -> list[list[float]]:
        """Embed multiple queries, applying a model's query prompt when it defines one."""
        return self._encode(queries, prompt_name=self._query_prompt_name)

    def _encode(self, texts: Sequence[str], prompt_name: str | None = None) -> list[list[float]]:
        if not texts:
            return []
        options = {"prompt_name": prompt_name} if prompt_name else {}
        vectors = self._model.encode(
            list(texts),
            batch_size=LOCAL_BATCH_SIZE,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 100,
            **options,
        )
        return vectors.tolist()
