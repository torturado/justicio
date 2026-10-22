"""HTTP API. Every endpoint except /healthcheck requires the X-API-Key header (JUSTICIO_API_KEY).

The API is meant to be called by the Justicio frontend, which forwards the end user's IP in ``client_ip`` for
rate limiting.
"""

import asyncio
import ipaddress
import logging as lg
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from google.genai import errors as genai_errors
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress
from qdrant_client import models

from src.initialize import initialize_logging, required_env
from src.service.answering import GroundingValidation, LanguageModelError, PromptSource
from src.service.limits import AdmissionLimiter, RateLimiter
from src.service.retrieval import RetrievedSource
from src.service.services import Services, create_services
from src.service.telemetry import TRACER, anonymize_ip, initialize_tracing, send_feedback, span_attributes

MAX_QUERY_LENGTH = 2000
FEEDBACK_TIMEOUT_SECONDS = 10


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    sources: list[str] | None = Field(
        default=None, min_length=1, description="Sources to search (see config.yaml); the default sources if omitted"
    )
    original_query: str | None = Field(default=None, max_length=MAX_QUERY_LENGTH)
    client_ip: IPvAnyAddress | None = Field(
        default=None, description="End user's IP, used for rate limiting and stored anonymized in traces"
    )


class WebQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_LENGTH)
    client_ip: IPvAnyAddress | None = None


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    span_id: str = Field(pattern=r"^\d{1,20}$")
    trace_id: str = Field(pattern=r"^\d{1,40}$")
    user_score: Literal[-1, 1]


class SourceResponse(BaseModel):
    number: int
    title: str | None
    text: str
    source_name: str | None
    source_type: str | None
    identifier: str | None
    official_number: str | None
    article: str | None
    version_date: str | None
    updated_at: str | None
    published_at: str | None
    url: str | None
    hybrid_score: float
    # Cross-encoder logit; neither score is a confidence probability
    reranker_score: float | None


class AnswerResponse(BaseModel):
    scoring_id: str
    answer: str
    sources: list[SourceResponse]
    grounding_validation: GroundingValidation
    span_id: str | None
    trace_id: str | None


class WebSource(BaseModel):
    number: int
    title: str
    url: str
    text: str


class WebAnswerResponse(BaseModel):
    scoring_id: str
    answer: str
    sources: list[WebSource]
    grounding_validation: GroundingValidation


class AppState:
    def __init__(self, services: Services, api_key: str, feedback_enabled: bool) -> None:
        self.services = services
        self.api_key = api_key
        self.feedback_enabled = feedback_enabled
        self.admission = AdmissionLimiter(services.settings.max_concurrent_answers)
        self.rate_limiter = RateLimiter(services.settings.rate_limit_per_minute)


def get_state(request: Request) -> AppState:
    return request.app.state.justicio


StateDep = Annotated[AppState, Depends(get_state)]
_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(state: StateDep, api_key: Annotated[str | None, Security(_API_KEY_HEADER)]) -> None:
    if api_key is None or not secrets.compare_digest(api_key.encode(), state.api_key.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


router = APIRouter(dependencies=[Depends(require_api_key)])


def _sources(requested: list[str] | None, state: AppState) -> list[str]:
    settings = state.services.settings
    sources = list(dict.fromkeys(requested or settings.default_sources))
    unknown = [source for source in sources if source not in settings.sources]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown sources: {', '.join(unknown)}")
    return sources


@asynccontextmanager
async def _protected(
    state: AppState, client_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None
) -> AsyncIterator[None]:
    """Per-user rate limit (when the frontend sends the user's IP) and admission control."""
    if client_ip is not None:
        state.rate_limiter.check(str(client_ip))
    async with state.admission.slot():
        yield


def _source_response(number: int, source: RetrievedSource) -> SourceResponse:
    payload = source.payload
    return SourceResponse(
        number=number,
        title=payload.get("chunk_title") or payload.get("titulo"),
        text=source.text,
        source_name=payload.get("source_name"),
        source_type=payload.get("source_type"),
        identifier=payload.get("identificador"),
        official_number=payload.get("numero_oficial"),
        article=payload.get("bloque_titulo"),
        version_date=payload.get("fecha_vigencia_version"),
        updated_at=payload.get("fecha_actualizacion"),
        published_at=payload.get("fecha_publicacion"),
        url=source.url,
        hybrid_score=source.hybrid_score,
        reranker_score=source.reranker_score,
    )


async def _no_sources_answer(state: AppState, sources: Sequence[str]) -> str:
    indexed = await state.services.search.count(
        models.Filter(must=[models.FieldCondition(key="source_name", match=models.MatchAny(any=list(sources)))])
    )
    if indexed == 0:
        return f"Las fuentes consultadas ({_scope(state, sources)}) no tienen documentos indexados. No puedo responder con respaldo documental."
    return "No he encontrado fragmentos pertinentes en las fuentes indexadas para responder con respaldo documental."


def _scope(state: AppState, sources: Sequence[str]) -> str:
    return "; ".join(state.services.settings.sources[source] for source in sources)


def _web_search(state: AppState, query: str) -> list[WebSource]:
    if state.services.tavily is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="BOE web search is not configured")
    response = state.services.tavily.search(
        query=query,
        search_depth="advanced",
        include_domains=["boe.es"],
        max_results=10,
        topic="general",
        include_raw_content=False,
        include_answer=False,
    )
    return [
        WebSource(number=number, title=result["title"], url=result["url"], text=result["content"])
        for number, result in enumerate(response["results"], start=1)
    ]


@router.post("/semantic_search", response_model=list[SourceResponse])
async def semantic_search(request: QuestionRequest, state: StateDep) -> list[SourceResponse]:
    sources = _sources(request.sources, state)
    async with _protected(state, request.client_ip):
        retrieved = await state.services.retriever.retrieve(request.query, sources)
    return [_source_response(number, source) for number, source in enumerate(retrieved, start=1)]


@router.post("/semantic_search_tavily", response_model=list[WebSource])
async def semantic_search_tavily(request: WebQuestionRequest, state: StateDep) -> list[WebSource]:
    async with _protected(state, request.client_ip):
        return await asyncio.to_thread(_web_search, state, request.query)


@router.post("/qa", response_model=AnswerResponse)
async def qa(request: QuestionRequest, state: StateDep) -> AnswerResponse:
    logger = lg.getLogger(qa.__name__)
    sources = _sources(request.sources, state)
    attributes = {
        "db.sources": ",".join(sources),
        "service.ip": anonymize_ip(request.client_ip),
        "llm.original_query": request.original_query,
    }
    async with _protected(state, request.client_ip):
        with TRACER.start_as_current_span("RAG Justicio") as span, span_attributes(attributes):
            retrieved = await state.services.retriever.retrieve(request.query, sources)
            if not retrieved:
                return AnswerResponse(
                    scoring_id=str(uuid.uuid4()),
                    answer=await _no_sources_answer(state, sources),
                    sources=[],
                    grounding_validation=GroundingValidation(status="not_run"),
                    span_id=None,
                    trace_id=None,
                )
            answer, validation = await state.services.answerer.answer(
                request.query,
                [PromptSource(title=source.title, text=source.text) for source in retrieved],
                scope=_scope(state, sources),
            )
            span_context = span.get_span_context()
    logger.info("Answered from %s sources of %s; grounding %s", len(retrieved), sources, validation.status)
    return AnswerResponse(
        scoring_id=str(uuid.uuid4()),
        answer=answer,
        sources=[_source_response(number, source) for number, source in enumerate(retrieved, start=1)],
        grounding_validation=validation,
        span_id=str(span_context.span_id) if span_context.is_valid else None,
        trace_id=str(span_context.trace_id) if span_context.is_valid else None,
    )


@router.post("/qa_tavily", response_model=WebAnswerResponse)
async def qa_tavily(request: WebQuestionRequest, state: StateDep) -> WebAnswerResponse:
    async with _protected(state, request.client_ip):
        web_sources = await asyncio.to_thread(_web_search, state, request.query)
        answer, validation = await state.services.answerer.answer(
            request.query,
            [PromptSource(title=f"{source.title} ({source.url})", text=source.text) for source in web_sources],
        )
    return WebAnswerResponse(
        scoring_id=str(uuid.uuid4()), answer=answer, sources=web_sources, grounding_validation=validation
    )


@router.post("/qa_feedback")
async def qa_feedback(request: FeedbackRequest, state: StateDep) -> dict[str, str]:
    if not state.feedback_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Feedback requires Langtrace")
    try:
        await asyncio.wait_for(
            asyncio.to_thread(send_feedback, request.span_id, request.trace_id, request.user_score),
            timeout=FEEDBACK_TIMEOUT_SECONDS,
        )
    except TimeoutError as error:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, detail="Feedback was not recorded in time") from error
    return {"feedback": "OK"}


async def _language_model_error(request: Request, error: Exception) -> JSONResponse:
    lg.getLogger(_language_model_error.__name__).error("Language model failure: %s", error)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY, content={"detail": "The language model did not return an answer"}
    )


ServicesFactory = Callable[[], AbstractAsyncContextManager[Services]]


def create_app(services_factory: ServicesFactory = create_services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_logging()
        api_key = required_env("JUSTICIO_API_KEY")
        feedback_enabled = initialize_tracing()
        async with services_factory() as services:
            app.state.justicio = AppState(services, api_key, feedback_enabled)
            yield

    app = FastAPI(title="Justicio", lifespan=lifespan)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        lg.getLogger("justicio.access").info(
            "%s %s %s in %.0f ms",
            request.method,
            request.url.path,
            response.status_code,
            1000 * (time.perf_counter() - start),
        )
        return response

    @app.get("/healthcheck")
    async def healthcheck() -> dict[str, str]:
        return {"status": "OK"}

    app.include_router(router)
    app.add_exception_handler(LanguageModelError, _language_model_error)
    app.add_exception_handler(genai_errors.APIError, _language_model_error)
    return app


APP = create_app()
