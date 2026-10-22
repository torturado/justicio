"""Factories for the clients and models shared by the service, the ETL and the evaluation."""

import logging as lg
import os

from google import genai
from google.genai import types
from qdrant_client import AsyncQdrantClient, QdrantClient
from tavily import TavilyClient

from src.embeddings import SentenceTransformerEmbedder
from src.settings import Settings

QDRANT_TIMEOUT_SECONDS = 60


def initialize_logging() -> None:
    """Configure the root logger. Call it once from each entrypoint."""
    lg.basicConfig(
        format="[%(asctime)s] [%(process)d] [%(levelname)s] [%(name)s] %(message)s",
        level=lg.INFO,
        force=True,
    )


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"The environment variable {name} is required.")
    return value


def create_embedder(settings: Settings) -> SentenceTransformerEmbedder:
    embedder = SentenceTransformerEmbedder(settings.embeddings_model_name)
    if embedder.dimensions != settings.embeddings_dimensions:
        raise RuntimeError(
            f"Embedding model [{settings.embeddings_model_name}] returns {embedder.dimensions} dimensions, "
            f"but config expects {settings.embeddings_dimensions}."
        )
    return embedder


def _qdrant_options() -> dict:
    return {
        "url": required_env("QDRANT_API_URL"),
        "api_key": os.environ.get("QDRANT_API_KEY") or None,
        "prefer_grpc": True,
        # BM25 sparse vectors are computed by the Qdrant server
        "cloud_inference": True,
        "timeout": QDRANT_TIMEOUT_SECONDS,
    }


def create_qdrant_client() -> QdrantClient:
    """Client for the ETL, which creates the collection and writes to it."""
    return QdrantClient(**_qdrant_options())


def create_async_qdrant_client() -> AsyncQdrantClient:
    """Client for the service, which only reads; QDRANT_API_KEY can be a read-only key."""
    return AsyncQdrantClient(**_qdrant_options())


def create_genai_client(settings: Settings) -> genai.Client:
    return genai.Client(
        api_key=required_env("GEMINI_API_KEY"),
        http_options=types.HttpOptions(
            timeout=settings.llm_timeout_seconds * 1000,
            # Retries rate limits (429) and server errors with a short exponential backoff
            retry_options=types.HttpRetryOptions(attempts=settings.llm_retry_attempts, initial_delay=1, max_delay=8),
        ),
    )


def create_tavily_client() -> TavilyClient | None:
    api_key = os.environ.get("TAVILY_API_KEY")
    return TavilyClient(api_key=api_key) if api_key else None
