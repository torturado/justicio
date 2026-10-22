import asyncio
import logging as lg
from collections.abc import AsyncIterator
from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass

from tavily import TavilyClient

from src.initialize import create_async_qdrant_client, create_embedder, create_genai_client, create_tavily_client
from src.reranking import CrossEncoderReranker, OpenVINOReranker, Reranker, openvino_model_dir
from src.service.answering import Answerer
from src.service.retrieval import Retriever, model_text
from src.settings import Settings, load_settings
from src.vector_store import VectorSearch

# Chunks used to calibrate the int8 quantization of the OpenVINO reranker
CALIBRATION_SIZE = 128


@dataclass(frozen=True)
class Services:
    settings: Settings
    search: VectorSearch
    retriever: Retriever
    answerer: Answerer
    tavily: TavilyClient | None


async def create_reranker(settings: Settings, search: VectorSearch, executor: Executor) -> Reranker:
    if settings.reranker_backend == "torch":
        return CrossEncoderReranker(
            settings.reranker_model_name,
            device=settings.reranker_device,
            max_length=settings.reranker_max_length,
            batch_size=settings.reranker_batch_size,
        )
    model_dir = openvino_model_dir(settings.reranker_model_name)
    if not (model_dir / OpenVINOReranker.MODEL_FILE).exists():
        lg.getLogger(create_reranker.__name__).info(
            "Exporting %s to OpenVINO int8 in %s. This happens once and takes a few minutes.",
            settings.reranker_model_name,
            model_dir,
        )
        payloads = await search.sample(CALIBRATION_SIZE)
        if not payloads:
            raise RuntimeError("The collection has no chunks to calibrate the OpenVINO reranker. Load documents first.")
        # Each chunk is paired with the title of another one, which reads like a short question about a norm
        pairs = [
            (payloads[(index + 1) % len(payloads)].get("chunk_title") or "", model_text(payload))
            for index, payload in enumerate(payloads)
        ]
        await asyncio.get_running_loop().run_in_executor(
            executor,
            OpenVINOReranker.export,
            settings.reranker_model_name,
            model_dir,
            pairs,
            settings.reranker_max_length,
        )
    return OpenVINOReranker(model_dir, max_length=settings.reranker_max_length, batch_size=settings.reranker_batch_size)


@asynccontextmanager
async def create_services(settings: Settings | None = None) -> AsyncIterator[Services]:
    """Connect to Qdrant and Gemini and load the models, so that the first request does not pay for it."""
    settings = settings or load_settings()
    qdrant_client = create_async_qdrant_client()
    # A single thread for model inference (see Retriever)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inference")
    try:
        search = VectorSearch(qdrant_client, settings.qdrant_collection, settings.embeddings_dimensions)
        await search.check_collection()
        reranker = await create_reranker(settings, search, executor)
        retriever = Retriever(settings, create_embedder(settings), reranker, search, executor)
        yield Services(
            settings=settings,
            search=search,
            retriever=retriever,
            answerer=Answerer(create_genai_client(settings), settings),
            tavily=create_tavily_client(),
        )
    finally:
        executor.shutdown(cancel_futures=True)
        await qdrant_client.close()
