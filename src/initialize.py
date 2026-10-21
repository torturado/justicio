import collections
import logging as lg
import os

import yaml
from google import genai
from google.genai import types
from qdrant_client import QdrantClient
from tavily import TavilyClient

from src.embeddings import SentenceTransformerEmbedder
from src.vector_store import VectorStore


def initialize_logging():
    logger = lg.getLogger()
    logger.info("Initializing logging")
    logger.handlers = []
    handler = lg.StreamHandler()
    formatter = lg.Formatter("[%(asctime)s] [%(process)d] [%(levelname)s] [%(name)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(lg.INFO)
    logger.info("Initialized logging")
    lg.getLogger("uvicorn.error").handlers = logger.handlers


def initialize_app():
    """Initializes the application"""
    logger = lg.getLogger(initialize_app.__name__)
    logger.info("Initializing application")
    config_loader = load_config()
    genai_client = init_genai_client()
    embedder = SentenceTransformerEmbedder(config_loader["embeddings_model_name"])
    if embedder.dimensions != config_loader["embeddings_dimensions"]:
        raise RuntimeError(
            f"Embedding model [{config_loader['embeddings_model_name']}] returns {embedder.dimensions} dimensions, "
            f"but config expects {config_loader['embeddings_dimensions']}."
        )
    vector_store = _init_vector_stores(config_loader, embedder)
    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    tavily_client = TavilyClient(api_key=tavily_api_key) if tavily_api_key else None
    logger.info("Initialized application")
    init_objects = collections.namedtuple(
        "init_objects", ["config_loader", "vector_store", "embedder", "genai_client", "tavily_client"]
    )
    return init_objects(config_loader, vector_store, embedder, genai_client, tavily_client)


def load_config():
    yaml_config_path = os.path.join(os.environ["APP_PATH"], "config", "config.yaml")
    with open(yaml_config_path, "r") as stream:
        config_loader = yaml.safe_load(stream)
    return config_loader


def init_genai_client():
    logger = lg.getLogger(init_genai_client.__name__)
    logger.info("Initializing Gemini client")
    client = genai.Client(
        api_key=os.environ["GEMINI_API_KEY"],
        # Retries rate limits (429) and server errors with exponential backoff
        http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=8, initial_delay=2, max_delay=60)),
    )
    logger.info("Initialized Gemini client")
    return client


def init_qdrant_client():
    return QdrantClient(
        url=os.environ["QDRANT_API_URL"],
        api_key=os.environ.get("QDRANT_API_KEY"),
        prefer_grpc=True,
        # BM25 sparse vectors are computed by the Qdrant server
        cloud_inference=True,
        timeout=60,
    )


def _init_vector_stores(config_loader, embedder):
    logger = lg.getLogger(_init_vector_stores.__name__)
    logger.info("Initializing vector stores")
    qdrant_client = init_qdrant_client()
    vector_stores = {}
    for collection_name in config_loader["collections"]:
        vector_store = VectorStore(qdrant_client, collection_name, embedder, config_loader["embeddings_dimensions"])
        vector_store.ensure_collection()
        vector_stores[collection_name] = vector_store
        logger.info("Initialized vector store for collection [%s]", collection_name)
    return vector_stores
