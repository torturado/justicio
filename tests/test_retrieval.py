import asyncio
from concurrent.futures import ThreadPoolExecutor

from qdrant_client import models

from src.service.retrieval import RetrievedSource, Retriever, expanded_text, neighbor_ids, select_hits
from src.settings import load_settings
from src.vector_store import SearchHit, point_id


def _hit(name, score=0.0, **payload):
    return SearchHit(id=name, payload=payload, score=score)


def test_selection_keeps_the_best_hybrid_hits_that_the_reranker_ranks_low():
    hits = [_hit(f"h{index}") for index in range(6)]
    reranker_scores = [5.0, 4.0, -9.0, 3.0, 2.0, 1.0]

    selected = select_hits(hits, reranker_scores, top_k=3, head_size=3)

    assert [hit.id for hit, _ in selected] == ["h0", "h1", "h2"]
    assert [score for _, score in selected] == [5.0, 4.0, -9.0]


def test_selection_without_protection_is_the_reranked_top_k():
    hits = [_hit(f"h{index}") for index in range(4)]

    selected = select_hits(hits, [1.0, 4.0, 3.0, 2.0], top_k=2, head_size=0)

    assert [hit.id for hit, _ in selected] == ["h1", "h2"]


def _chunk(index, bloque_id="a1", text=None):
    return {
        "source_name": "BOE",
        "identificador": "BOE-A-2015-10565",
        "chunk_index": index,
        "bloque_id": bloque_id,
        "text": text or f"parte {index}",
    }


def test_context_joins_adjacent_chunks_of_the_same_article_in_order():
    neighbors = {point_id("BOE", "BOE-A-2015-10565", index): _chunk(index) for index in (4, 6)}

    assert expanded_text(_chunk(5), neighbors, excluded_ids=set()) == "parte 4\nparte 5\nparte 6"


def test_context_leaves_out_other_articles_and_chunks_selected_as_sources():
    neighbors = {
        point_id("BOE", "BOE-A-2015-10565", 4): _chunk(4, bloque_id="a0"),
        point_id("BOE", "BOE-A-2015-10565", 6): _chunk(6),
    }

    text = expanded_text(_chunk(5), neighbors, excluded_ids={point_id("BOE", "BOE-A-2015-10565", 6)})

    assert text == "parte 5"


def test_first_chunk_has_no_previous_neighbor():
    assert neighbor_ids(_chunk(0)) == [point_id("BOE", "BOE-A-2015-10565", 1)]


def test_source_title_shows_the_provenance_given_to_the_model():
    source = RetrievedSource(
        id="p",
        payload={
            "chunk_title": "Ley 39/2015 > Artículo 21",
            "source_name": "BOE",
            "source_type": "Legislación consolidada",
            "numero_oficial": "39/2015",
            "fecha_vigencia_version": "2016-10-02",
            "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565#a21",
        },
        hybrid_score=0.5,
        reranker_score=1.0,
        text="",
    )

    assert source.title == (
        "Ley 39/2015 > Artículo 21 (Fuente: BOE, texto consolidado; Número oficial: 39/2015; "
        "Versión vigente desde: 2016-10-02; URL: https://www.boe.es/buscar/act.php?id=BOE-A-2015-10565#a21)"
    )


class FakeEmbedder:
    dimensions = 3

    def embed_documents(self, documents):
        return [[0.0, 0.0, 1.0] for _ in documents]

    def embed_query(self, query):
        return [0.0, 0.0, 1.0]


class FakeSearch:
    """Returns hits only for searches without a legal filter."""

    def __init__(self):
        self.filters = []

    async def search(self, query, query_vector, limit, prefetch_limit, query_filter=None):
        self.filters.append(query_filter)
        return [] if len(query_filter.must) > 1 else [_hit("h0", score=0.5, **_chunk(0))]


def test_search_falls_back_to_unfiltered_search_when_the_named_norm_is_not_indexed():
    search = FakeSearch()
    with ThreadPoolExecutor(max_workers=1) as executor:
        retriever = Retriever(load_settings(), FakeEmbedder(), reranker=None, search=search, executor=executor)
        hits = asyncio.run(retriever.search("¿Qué dice el artículo 3 de la Ley 3/2024 de Madrid?", ["BOCM"]))

    assert [hit.id for hit in hits] == ["h0"]
    assert len(search.filters) == 3
    sources_condition = search.filters[-1].must[0]
    assert sources_condition.key == "source_name"
    assert sources_condition.match == models.MatchAny(any=["BOCM"])
