from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from src.service.answering import GroundingValidation
from src.service.main import create_app
from src.service.retrieval import RetrievedSource
from src.service.services import Services
from src.settings import load_settings

API_KEY = "test-api-key"
HEADERS = {"X-API-Key": API_KEY}
SOURCE = RetrievedSource(
    id="p1",
    payload={
        "chunk_title": "Ley 39/2015 > Artículo 21",
        "source_name": "BOE",
        "source_type": "Legislación consolidada",
        "identificador": "BOE-A-2015-10565",
        "bloque_titulo": "Artículo 21",
        "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565#a21",
    },
    hybrid_score=0.5,
    reranker_score=2.0,
    text="La Administración está obligada a dictar resolución expresa.",
)


class FakeRetriever:
    def __init__(self, sources):
        self._sources = sources

    async def retrieve(self, query, sources):
        return self._sources


class FakeAnswerer:
    async def answer(self, query, sources, scope=None):
        return "Debe resolver de forma expresa [1].", GroundingValidation(status="passed")


class FakeSearch:
    async def count(self, count_filter=None):
        return 0


def _client(monkeypatch, sources=(SOURCE,), **settings_update):
    monkeypatch.setenv("JUSTICIO_API_KEY", API_KEY)
    monkeypatch.delenv("LANGTRACE_API_KEY", raising=False)
    settings = load_settings().model_copy(update=settings_update)

    @asynccontextmanager
    async def services_factory():
        yield Services(
            settings=settings,
            search=FakeSearch(),
            retriever=FakeRetriever(list(sources)),
            answerer=FakeAnswerer(),
            tavily=None,
        )

    return TestClient(create_app(services_factory))


def test_healthcheck_is_public(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/healthcheck").json() == {"status": "OK"}


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_endpoints_require_the_api_key(monkeypatch, headers):
    with _client(monkeypatch) as client:
        assert client.post("/qa", json={"query": "¿Plazo?"}, headers=headers).status_code == 401


def test_qa_answers_with_numbered_sources(monkeypatch):
    with _client(monkeypatch) as client:
        response = client.post(
            "/qa", json={"query": "¿Debe resolver la Administración?", "client_ip": "2001:db8::1"}, headers=HEADERS
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Debe resolver de forma expresa [1]."
    assert body["grounding_validation"]["status"] == "passed"
    assert body["sources"][0]["number"] == 1
    assert body["sources"][0]["identifier"] == "BOE-A-2015-10565"
    assert body["sources"][0]["text"] == SOURCE.text


def test_qa_requires_a_bounded_question_and_known_sources(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/qa", headers=HEADERS).status_code == 405
        assert client.post("/qa", json={}, headers=HEADERS).status_code == 422
        assert client.post("/qa", json={"query": "x" * 2001}, headers=HEADERS).status_code == 422
        assert client.post("/qa", json={"query": "¿Plazo?", "sources": ["XYZ"]}, headers=HEADERS).status_code == 422


def test_qa_reports_sources_without_indexed_documents(monkeypatch):
    with _client(monkeypatch, sources=()) as client:
        body = client.post("/qa", json={"query": "¿Plazo?", "sources": ["BOPZ"]}, headers=HEADERS).json()

    assert body["grounding_validation"]["status"] == "not_run"
    assert "no tienen documentos indexados" in body["answer"]


def test_qa_rate_limits_each_user_ip(monkeypatch):
    with _client(monkeypatch, rate_limit_per_minute=2) as client:
        statuses = [
            client.post("/qa", json={"query": "¿Plazo?", "client_ip": "203.0.113.7"}, headers=HEADERS).status_code
            for _ in range(3)
        ]

    assert statuses == [200, 200, 429]


def test_feedback_requires_tracing_and_a_valid_score(monkeypatch):
    with _client(monkeypatch) as client:
        feedback = {"span_id": "123", "trace_id": "456", "user_score": 1}
        assert client.post("/qa_feedback", json=feedback, headers=HEADERS).status_code == 503
        assert client.post("/qa_feedback", json={**feedback, "user_score": 5}, headers=HEADERS).status_code == 422
