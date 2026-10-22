from qdrant_client import models

from src.legal_query import build_legal_query_filters


def _conditions(query_filter: models.Filter) -> list[tuple[str, object]]:
    result = []
    for condition in query_filter.must:
        if isinstance(condition.match, models.MatchAny):
            result.append((condition.key, condition.match.any))
        else:
            result.append((condition.key, condition.match.text))
    return result


def test_question_without_norm_number_is_not_filtered():
    assert build_legal_query_filters("¿Qué derechos tienen los interesados en un procedimiento?") == []


def test_named_article_of_a_norm_falls_back_to_the_whole_norm():
    filters = build_legal_query_filters("¿Qué dice el artículo 3 de la Ley 12/2023?")

    assert [_conditions(query_filter) for query_filter in filters] == [
        [("numero_oficial", ["12/2023"]), ("bloque_titulo", "Artículo 3")],
        [("numero_oficial", ["12/2023"])],
    ]


def test_article_subsection_targets_its_article_block():
    filters = build_legal_query_filters("artículo 34.1 de la Ley 58/2003")

    assert _conditions(filters[0])[1] == ("bloque_titulo", "Artículo 34")


def test_comparison_of_two_norms_keeps_both_and_ignores_the_ambiguous_article():
    filters = build_legal_query_filters("Diferencias entre el artículo 21 de la Ley 39/2015 y la Ley 40/2015")

    assert [_conditions(query_filter) for query_filter in filters] == [[("numero_oficial", ["39/2015", "40/2015"])]]


def test_real_decreto_with_four_digit_number_is_recognized():
    filters = build_legal_query_filters("¿Qué regula el Real Decreto 1065/2007?")

    assert _conditions(filters[0]) == [("numero_oficial", ["1065/2007"])]
