import asyncio
import typing as tp

from google import genai
from google.genai import types

# Inputs sent in each embed_content request
BATCH_SIZE = 100
LOCAL_BATCH_SIZE = 8


class Embedder(tp.Protocol):
    """Embedding interface shared by the application and vector store."""

    dimensions: int

    def embed_documents(self, documents: tp.Sequence[tp.Tuple[str, str]]) -> tp.List[tp.List[float]]: ...

    def embed_query(self, query: str) -> tp.List[float]: ...

    async def aembed_query(self, query: str) -> tp.List[float]: ...


class SentenceTransformerEmbedder:
    """Embeds documents and queries with a local sentence-transformers model."""

    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Local embeddings require sentence-transformers. Install the application requirements."
            ) from error

        self._model = SentenceTransformer(model_name)
        dimensions = self._model.get_embedding_dimension()
        if dimensions is None:
            raise RuntimeError(f"Could not determine the embedding dimensions for [{model_name}].")
        self.dimensions = dimensions
        self._query_prompt_name = "query" if "query" in self._model.prompts else None

    def embed_documents(self, documents: tp.Sequence[tp.Tuple[str, str]]) -> tp.List[tp.List[float]]:
        """Embed (title, text) pairs, keeping article context alongside its text."""
        texts = [_document_text(title, text) for title, text in documents]
        return self._encode(texts)

    def embed_query(self, query: str) -> tp.List[float]:
        return self.embed_queries([query])[0]

    def embed_queries(self, queries: tp.Sequence[str]) -> tp.List[tp.List[float]]:
        """Embed multiple queries, applying a model's query prompt when it defines one."""
        if not queries:
            return []
        options = {
            "batch_size": LOCAL_BATCH_SIZE,
            "convert_to_numpy": True,
            "show_progress_bar": len(queries) > 100,
        }
        if self._query_prompt_name:
            options["prompt_name"] = self._query_prompt_name
        return self._model.encode(list(queries), **options).tolist()

    async def aembed_query(self, query: str) -> tp.List[float]:
        """Run local inference off the async service event loop."""
        return await asyncio.to_thread(self.embed_query, query)

    def _encode(self, texts: tp.Sequence[str]) -> tp.List[tp.List[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=LOCAL_BATCH_SIZE,
            convert_to_numpy=True,
            show_progress_bar=len(texts) > 100,
        )
        return vectors.tolist()


class GeminiEmbedder:
    """Embeds documents and queries with the Gemini API.

    gemini-embedding-2 has no task_type parameter: the retrieval task is expressed with the prompt formats
    recommended by Google (https://ai.google.dev/gemini-api/docs/embeddings).
    """

    def __init__(self, client: genai.Client, model_name: str, dimensions: int):
        self._client = client
        self._model_name = model_name
        self.dimensions = dimensions
        self._config = types.EmbedContentConfig(output_dimensionality=dimensions)

    def embed_documents(self, documents: tp.Sequence[tp.Tuple[str, str]]) -> tp.List[tp.List[float]]:
        """Embed (title, text) pairs."""
        texts = [f"title: {title or 'none'} | text: {text}" for title, text in documents]
        vectors = []
        for start in range(0, len(texts), BATCH_SIZE):
            response = self._client.models.embed_content(
                model=self._model_name, contents=_to_contents(texts[start : start + BATCH_SIZE]), config=self._config
            )
            vectors += [embedding.values for embedding in response.embeddings]
        return vectors

    def embed_query(self, query: str) -> tp.List[float]:
        response = self._client.models.embed_content(
            model=self._model_name, contents=_to_contents([_query_text(query)]), config=self._config
        )
        return response.embeddings[0].values

    async def aembed_query(self, query: str) -> tp.List[float]:
        response = await self._client.aio.models.embed_content(
            model=self._model_name, contents=_to_contents([_query_text(query)]), config=self._config
        )
        return response.embeddings[0].values


def _query_text(query: str) -> str:
    return f"task: search result | query: {query}"


def _document_text(title: str, text: str) -> str:
    if not title or title.casefold() == "none":
        return text
    return f"{title}\n{text}"


def _to_contents(texts: tp.Sequence[str]) -> tp.List[types.Content]:
    # Each input must be its own Content: a plain list of strings is embedded as a single aggregated vector
    return [types.Content(parts=[types.Part.from_text(text=text)]) for text in texts]
