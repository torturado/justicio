from collections.abc import Sequence

from google import genai
from google.genai import types

# Inputs sent in each embed_content request
BATCH_SIZE = 100


class GeminiEmbedder:
    """Embeds documents and queries with the Gemini API, to compare it with the local model.

    gemini-embedding-2 has no task_type parameter: the retrieval task is expressed with the prompt formats
    recommended by Google (https://ai.google.dev/gemini-api/docs/embeddings).
    """

    def __init__(self, client: genai.Client, model_name: str, dimensions: int):
        self._client = client
        self._model_name = model_name
        self.dimensions = dimensions
        self._config = types.EmbedContentConfig(output_dimensionality=dimensions)

    def embed_documents(self, documents: Sequence[tuple[str, str]]) -> list[list[float]]:
        """Embed (title, text) pairs."""
        texts = [f"title: {title or 'none'} | text: {text}" for title, text in documents]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            response = self._client.models.embed_content(
                model=self._model_name, contents=_to_contents(texts[start : start + BATCH_SIZE]), config=self._config
            )
            vectors += [embedding.values for embedding in response.embeddings]
        return vectors

    def embed_query(self, query: str) -> list[float]:
        response = self._client.models.embed_content(
            model=self._model_name,
            contents=_to_contents([f"task: search result | query: {query}"]),
            config=self._config,
        )
        return response.embeddings[0].values


def _to_contents(texts: Sequence[str]) -> list[types.Content]:
    # Each input must be its own Content: a plain list of strings is embedded as a single aggregated vector
    return [types.Content(parts=[types.Part.from_text(text=text)]) for text in texts]
