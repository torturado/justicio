"""Filters that narrow a search when a question names specific legal provisions."""

import re

from qdrant_client import models

_LAW_NUMBER = re.compile(
    r"\b(?:ley(?:\s+org[aá]nica)?|l\.?\s*o\.?|real\s+decreto(?:[-\s](?:ley|legislativo))?|"
    r"decreto(?:[-\s]ley)?|r\.?\s*d\.?\s*l?\.?|orden)\s*"
    r"(?:n[úu]m(?:ero)?\.?\s*)?(?P<number>\d{1,4}\s*/\s*\d{4})\b",
    re.IGNORECASE,
)
_ARTICLE_NUMBER = re.compile(r"\b(?:art[íi]culo|art\.?)\s*(?P<number>\d+[a-z]?(?:\.\d+)?)\b", re.IGNORECASE)


def build_legal_query_filters(query: str) -> list[models.Filter]:
    """Filters to try, from the most specific, before an unfiltered search.

    When the question states official norm numbers, the first filter restricts the search to those norms
    and, if a single norm and an article are named, to that article; the next one keeps only the norms.
    Article subsections remain within their parent BOE article block, so ``34.1`` targets the indexed
    ``Artículo 34`` block. Without a norm number the search is not restricted, to avoid matching an article
    number from the wrong norm. The caller falls back to the next filter when one returns no results, e.g.
    when a norm is not indexed or the question names a regional norm.
    """
    official_numbers = list(
        dict.fromkeys(re.sub(r"\s+", "", law_match.group("number")) for law_match in _LAW_NUMBER.finditer(query))
    )
    if not official_numbers:
        return []

    norms_condition = models.FieldCondition(key="numero_oficial", match=models.MatchAny(any=official_numbers))
    norms_filter = models.Filter(must=[norms_condition])
    article_match = _ARTICLE_NUMBER.search(query)
    if len(official_numbers) > 1 or article_match is None:
        return [norms_filter]

    article_number = article_match.group("number").split(".", maxsplit=1)[0]
    article_condition = models.FieldCondition(
        key="bloque_titulo", match=models.MatchText(text=f"Artículo {article_number}")
    )
    return [models.Filter(must=[norms_condition, article_condition]), norms_filter]
