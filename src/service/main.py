import asyncio
import ipaddress
import json
import logging as lg
import os
import re
import time
import typing as tp
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from google.genai import types
from langtrace_python_sdk import SendUserFeedback, langtrace
from langtrace_python_sdk.utils.with_root_span import with_langtrace_root_span
from pydantic import BaseModel

from src.initialize import initialize_app, initialize_logging
from src.legal_query import build_legal_query_filter
from src.reranking import CrossEncoderReranker
from src.utils import inject_additional_attributes, timeit
from src.vector_store import VectorStore

langtrace.init(api_key=os.environ.get("LANGTRACE_API_KEY"))
initialize_logging()

APP = FastAPI()

INIT_OBJECTS = initialize_app()
RERANKER = CrossEncoderReranker(
    INIT_OBJECTS.config_loader.get("reranker_model_name", "BAAI/bge-reranker-v2-m3"),
    device=INIT_OBJECTS.config_loader.get("reranker_device", "cpu"),
    max_length=INIT_OBJECTS.config_loader.get("reranker_max_length", 512),
    batch_size=INIT_OBJECTS.config_loader.get("reranker_batch_size", 8),
)

DEFAULT_INPUT_QUERY = (
    "¿Es de aplicación la ley de garantía integral de la libertad sexual a niños (varones) menores de edad "
    "víctimas de violencias sexuales o solo a niñas y mujeres?"
)
DEFAULT_COLLECTION_NAME = "justicio"
HYBRID_HEAD_SIZE = 3
_CITATION_GROUP = re.compile(r"\[([1-9]\d*(?:\s*,\s*[1-9]\d*)*)\]")
_GROUNDING_AUDIT_INSTRUCTION = (
    "Audita una respuesta jurídica usando exclusivamente las fuentes citadas que se adjuntan. "
    "Comprueba cada afirmación factual o jurídica: cada una debe llevar una cita y el fragmento "
    "indicado debe respaldar esa afirmación concreta. No basta con que trate del mismo tema. "
    "Marca como no respaldadas las afirmaciones sin cita, las que extrapolen el texto y las que "
    "mezclen requisitos de procedimientos distintos. No uses conocimiento externo. Devuelve "
    "supported=true únicamente si todas las afirmaciones están respaldadas y no hay citas inválidas. "
    "Devuelve listas vacías cuando no haya elementos que enumerar."
)
_GROUNDING_ABSTENTION = (
    "No puedo ofrecer una respuesta fiable: la comprobación no pudo confirmar que todas las "
    "afirmaciones estén respaldadas por las fuentes citadas."
)


class GroundingAudit(BaseModel):
    """Structured result of the claim-to-citation verification pass."""

    supported: bool
    unsupported_claims: tp.List[str]
    invalid_citations: tp.List[int]


def _get_vector_store(collection_name: str) -> VectorStore:
    if collection_name not in INIT_OBJECTS.vector_store:
        raise HTTPException(status_code=404, detail=f"Unknown collection: {collection_name}")
    return INIT_OBJECTS.vector_store[collection_name]


async def retrieve(
    input_query: str, collection_name: str, *, rerank: bool = True
) -> tp.List[tp.Tuple[tp.Dict[str, tp.Any], float]]:
    """Rerank hybrid candidates while retaining the three highest hybrid results."""
    vector_store = _get_vector_store(collection_name)
    query_vector = await INIT_OBJECTS.embedder.aembed_query(input_query)
    candidate_limit = INIT_OBJECTS.config_loader.get("reranker_candidates", 40)
    # The Qdrant client is synchronous: search in a worker thread to keep the event loop free
    candidates = await asyncio.to_thread(
        vector_store.search,
        input_query,
        query_vector,
        candidate_limit,
        INIT_OBJECTS.config_loader["prefetch_limit"],
        build_legal_query_filter(input_query),
    )
    if not rerank:
        return candidates
    ranked = await asyncio.to_thread(
        RERANKER.rerank,
        input_query,
        candidates,
        top_k=None,
    )
    top_k = INIT_OBJECTS.config_loader["top_k_results"]
    selected = ranked[:top_k]
    selected_ids = {id(payload) for payload, _ in selected}
    # A known relevant article ranked third in hybrid search was dropped by the reranker.
    protected_ids = {id(payload) for payload, _ in candidates[: min(HYBRID_HEAD_SIZE, top_k)]}
    reranker_scores = {id(payload): score for payload, score in ranked}
    for payload, _ in candidates[: min(HYBRID_HEAD_SIZE, top_k)]:
        payload_id = id(payload)
        if payload_id in selected_ids or not selected:
            continue
        replacement_index = next(
            (index for index in range(len(selected) - 1, -1, -1) if id(selected[index][0]) not in protected_ids),
            None,
        )
        if replacement_index is None:
            continue
        selected[replacement_index] = (payload, reranker_scores[payload_id])
        selected_ids.add(payload_id)
    selected.sort(key=lambda candidate: candidate[1], reverse=True)
    hybrid_scores = {id(payload): score for payload, score in candidates}
    return [
        (
            {**payload, "metadata": {**(payload.get("metadata") or {}), "reranker_score": reranker_score}},
            hybrid_scores[id(payload)],
        )
        for payload, reranker_score in selected
    ]


def _format_sources(sources: tp.List[tp.Tuple[str, str]]) -> str:
    return "\n\n".join(f"[{number}] {title}\n{text}" for number, (title, text) in enumerate(sources, start=1))


def _source_url(metadata: tp.Mapping[str, tp.Any]) -> tp.Optional[str]:
    for key in ("url", "url_html", "url_html_consolidada", "url_eli", "url_boletin", "url_pdf"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _source_title(payload: tp.Mapping[str, tp.Any]) -> str:
    metadata = payload.get("metadata") or {}
    details = [
        ("Identificador", metadata.get("identificador")),
        ("Norma", metadata.get("numero_oficial")),
        ("Apartado", metadata.get("bloque_titulo")),
        ("Versión", metadata.get("fecha_vigencia_version")),
        ("Actualización", metadata.get("fecha_actualizacion")),
        ("URL", _source_url(metadata)),
    ]
    provenance = "; ".join(f"{label}: {value}" for label, value in details if value)
    title = metadata.get("chunk_title") or metadata.get("titulo") or metadata.get("source_name") or "Fuente oficial"
    return f"{title} ({provenance})" if provenance else title


def _source_details(docs: tp.List[tp.Tuple[tp.Dict[str, tp.Any], float]]) -> tp.List[tp.Dict[str, tp.Any]]:
    result = []
    for number, (payload, score) in enumerate(docs, start=1):
        metadata = payload.get("metadata") or {}
        result.append(
            {
                "number": number,
                "title": metadata.get("chunk_title") or metadata.get("titulo"),
                "source_name": metadata.get("source_name"),
                "boe_id": metadata.get("identificador"),
                "official_number": metadata.get("numero_oficial"),
                "article": metadata.get("bloque_titulo"),
                "version_date": metadata.get("fecha_vigencia_version"),
                "updated_at": metadata.get("fecha_actualizacion"),
                "published_at": metadata.get("fecha_publicacion"),
                "url": _source_url(metadata),
                "hybrid_score": score,
                "reranker_score": metadata.get("reranker_score"),
            }
        )
    return result


@with_langtrace_root_span()
async def call_llm_api(
    span_id, trace_id, input_query: str, sources: tp.List[tp.Tuple[str, str]], source_scope: str | None = None
):
    """Answer the question with the LLM, grounded on the (title, text) sources."""
    config_loader = INIT_OBJECTS.config_loader
    system_instruction = [
        config_loader["prompt_system"],
        config_loader["prompt_system_context"],
    ]
    if source_scope:
        system_instruction.append(f"Ámbito de la colección consultada: {source_scope}.")
    system_instruction.append(f"A continuación se proporciona el contexto:\n\n{_format_sources(sources)}")
    response = await INIT_OBJECTS.genai_client.aio.models.generate_content(
        model=config_loader["llm_model_name"],
        contents=input_query,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            seed=config_loader["seed"],
            max_output_tokens=config_loader["max_output_tokens"],
        ),
    )
    return response, span_id, trace_id


@with_langtrace_root_span("Citation grounding audit")
async def _call_grounding_audit(
    span_id,
    trace_id,
    input_query: str,
    answer: str,
    cited_sources: tp.List[tp.Dict[str, tp.Any]],
) -> tp.Tuple[GroundingAudit, tp.Any, tp.Any]:
    response = await INIT_OBJECTS.genai_client.aio.models.generate_content(
        model=INIT_OBJECTS.config_loader["llm_model_name"],
        contents=json.dumps(
            {"question": input_query, "answer": answer, "cited_sources": cited_sources}, ensure_ascii=False
        ),
        config=types.GenerateContentConfig(
            system_instruction=[_GROUNDING_AUDIT_INSTRUCTION],
            response_mime_type="application/json",
            response_schema=GroundingAudit,
            seed=INIT_OBJECTS.config_loader["seed"],
            max_output_tokens=1024,
        ),
    )
    if isinstance(response.parsed, GroundingAudit):
        audit = response.parsed
    elif isinstance(response.parsed, dict):
        audit = GroundingAudit.model_validate(response.parsed)
    elif response.text:
        audit = GroundingAudit.model_validate_json(response.text)
    else:
        raise ValueError("Gemini returned no structured citation audit")
    return audit, span_id, trace_id


def _citation_numbers(answer: str) -> tp.List[int]:
    return [int(number) for group in _CITATION_GROUP.findall(answer) for number in re.split(r"\s*,\s*", group)]


async def _verify_answer_grounding(
    input_query: str, answer: str, sources: tp.List[tp.Tuple[str, str]]
) -> tp.Dict[str, tp.Any]:
    citation_numbers = _citation_numbers(answer)
    known_numbers = set(range(1, len(sources) + 1))
    invalid_citations = sorted(set(citation_numbers) - known_numbers)
    if invalid_citations:
        return {
            "status": "failed",
            "unsupported_claims": [],
            "invalid_citations": invalid_citations,
        }
    if not citation_numbers:
        return {"status": "failed", "unsupported_claims": ["La respuesta no contiene citas."], "invalid_citations": []}

    unique_citations = sorted(set(citation_numbers))
    cited_sources = [
        {"number": number, "title": sources[number - 1][0], "text": sources[number - 1][1]}
        for number in unique_citations
    ]
    audit, _, _ = await _call_grounding_audit(
        input_query=input_query,
        answer=answer,
        cited_sources=cited_sources,
    )
    valid = audit.supported and not audit.unsupported_claims and not audit.invalid_citations
    return {
        "status": "passed" if valid else "failed",
        "unsupported_claims": audit.unsupported_claims,
        "invalid_citations": sorted(set(audit.invalid_citations)),
    }


async def _verify_or_abstain(
    input_query: str, answer: str, sources: tp.List[tp.Tuple[str, str]]
) -> tp.Tuple[str, tp.Dict[str, tp.Any]]:
    try:
        grounding_validation = await _verify_answer_grounding(input_query, answer, sources)
    except Exception:
        lg.getLogger(_verify_or_abstain.__name__).exception("Citation grounding audit failed")
        return _GROUNDING_ABSTENTION, {
            "status": "unavailable",
            "unsupported_claims": [],
            "invalid_citations": [],
        }
    if grounding_validation["status"] != "passed":
        return _GROUNDING_ABSTENTION, grounding_validation
    return answer, grounding_validation


def _answer_text(response: types.GenerateContentResponse) -> str:
    logger = lg.getLogger(_answer_text.__name__)
    logger.info(response.usage_metadata)
    if not response.text:
        finish_reason = response.candidates[0].finish_reason if response.candidates else None
        logger.error("The LLM returned no answer (finish reason: %s)", finish_reason)
        raise HTTPException(status_code=502, detail="The language model returned no answer")
    logger.info(response.text)
    return response.text


def _tavily_search(input_query: str) -> tp.Dict[str, tp.Any]:
    if INIT_OBJECTS.tavily_client is None:
        raise HTTPException(status_code=503, detail="BOE web search is not configured")
    return INIT_OBJECTS.tavily_client.search(
        query=input_query,
        search_depth="advanced",
        include_domains=["https://www.boe.es/"],
        max_results=10,
        topic="general",
        include_raw_content=False,
        include_answer=False,
    )


@APP.get("/healthcheck")
@timeit
async def healthcheck():
    """Asynchronous Health Check"""
    # TODO: healthcheck with embeddings db api and llm api
    return {"status": "OK"}


@APP.get("/semantic_search")
@timeit
async def semantic_search(input_query: str = DEFAULT_INPUT_QUERY, collection_name: str = DEFAULT_COLLECTION_NAME):
    logger = lg.getLogger(semantic_search.__name__)
    logger.info(input_query)
    docs = await retrieve(input_query, collection_name)
    logger.info(docs)
    return docs


@APP.get("/semantic_search_tavily")
@timeit
async def semantic_search_tavily(input_query: str = DEFAULT_INPUT_QUERY):
    logger = lg.getLogger(semantic_search_tavily.__name__)
    logger.info(input_query)
    docs = await asyncio.to_thread(_tavily_search, input_query)
    logger.info(docs)
    return docs


async def a_request_get(url):
    """Requests for sync/async load tests"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        return response.text


@APP.get("/qa_feedback")
@with_langtrace_root_span("Feedback")
@timeit
async def qa_feedback(span_id: str, trace_id: str, user_score: int):
    data = {"spanId": span_id, "traceId": trace_id, "userScore": user_score, "userId": None}
    SendUserFeedback().evaluate(data=data)
    return {"feedback": "OK"}


@APP.get("/qa")
@with_langtrace_root_span("RAG Justicio")
@timeit
async def qa(
    input_query: str = DEFAULT_INPUT_QUERY,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    input_original_query: str | None = None,
    ip_request_client: ipaddress.IPv4Address | None = None,
):
    logger = lg.getLogger(qa.__name__)
    logger.info(input_query)

    # Getting context from embedding database (Qdrant)
    docs = await retrieve(input_query, collection_name)
    if not docs:
        vector_store = _get_vector_store(collection_name)
        point_count = await asyncio.to_thread(vector_store.count)
        scope = INIT_OBJECTS.config_loader.get("collection_scopes", {}).get(collection_name, collection_name)
        answer = (
            f"La colección consultada ({scope}) no tiene documentos indexados. No puedo responder con respaldo documental."
            if point_count == 0
            else "No he encontrado fragmentos pertinentes en las fuentes indexadas para responder con respaldo documental."
        )
        return {
            "scoring_id": str(uuid.uuid4()),
            "context": [],
            "sources": [],
            "answer": answer,
            "grounding_validation": {
                "status": "not_run",
                "unsupported_claims": [],
                "invalid_citations": [],
            },
            "span_id": None,
            "trace_id": None,
        }

    # Generate response using a LLM (Gemini)
    sources = [(_source_title(payload), payload["page_content"]) for payload, _ in docs]
    source_scope = INIT_OBJECTS.config_loader.get("collection_scopes", {}).get(collection_name, collection_name)
    additional_attributes = {
        "db.collection.name": collection_name,
        "service.ip": ip_request_client,
        "llm.original_query": input_original_query,
    }
    response, span_id, trace_id = await inject_additional_attributes(
        lambda: call_llm_api(input_query=input_query, sources=sources, source_scope=source_scope), additional_attributes
    )
    answer, grounding_validation = await _verify_or_abstain(input_query, _answer_text(response), sources)

    response_payload = dict(
        scoring_id=str(uuid.uuid4()),
        context=docs,
        sources=_source_details(docs),
        answer=answer,
        grounding_validation=grounding_validation,
        span_id=str(span_id),
        trace_id=str(trace_id),
    )
    return response_payload


@APP.get("/qa_tavily")
@timeit
async def qa_tavily(input_query: str = DEFAULT_INPUT_QUERY):
    logger = lg.getLogger(qa_tavily.__name__)
    logger.info(input_query)

    # Getting context from internet browser (Tavily)
    docs = await asyncio.to_thread(_tavily_search, input_query)

    # Generate response using a LLM (Gemini)
    sources = [(f"{doc['title']} ({doc['url']})", doc["content"]) for doc in docs["results"]]
    response, _, _ = await call_llm_api(input_query=input_query, sources=sources)
    answer, grounding_validation = await _verify_or_abstain(input_query, _answer_text(response), sources)

    response_payload = dict(
        scoring_id=str(uuid.uuid4()),
        context=docs,
        answer=answer,
        grounding_validation=grounding_validation,
    )
    return response_payload


@APP.get("/sleep")
@timeit
async def sleep():
    time.sleep(5)
    return {"status": "OK"}


@APP.get("/asleep")
@timeit
async def asleep():
    await asyncio.sleep(5)
    return {"status": "OK"}
