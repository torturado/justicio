"""Helpers for narrowing searches when a question names a specific legal provision."""

import re
import typing as tp

from qdrant_client import models

_LAW_NUMBER = re.compile(
    r"\b(?:ley(?:\s+org[aá]nica)?|l\.?\s*o\.?|real\s+decreto(?:[-\s](?:ley|legislativo))?|"
    r"decreto(?:[-\s]ley)?|r\.?\s*d\.?\s*l?\.?|orden)\s*"
    r"(?:n[úu]m(?:ero)?\.?\s*)?(?P<number>\d{1,3}\s*/\s*\d{4})\b",
    re.IGNORECASE,
)
_ARTICLE_NUMBER = re.compile(r"\b(?:art[íi]culo|art\.?)\s*(?P<number>\d+[a-z]?(?:\.\d+)?)\b", re.IGNORECASE)


def build_legal_query_filter(query: str) -> tp.Optional[models.Filter]:
    """Filter to a named law and/or article when the question states its official number.

    Article subsections remain within their parent BOE article block, so ``34.1`` targets
    the indexed ``Artículo 34`` block. When the law number is absent, semantic retrieval
    remains unrestricted to avoid matching an article number from the wrong law.
    """
    law_match = _LAW_NUMBER.search(query)
    if law_match is None:
        return None

    official_number = re.sub(r"\s+", "", law_match.group("number"))
    conditions = [
        models.FieldCondition(
            key="metadata.numero_oficial",
            match=models.MatchValue(value=official_number),
        )
    ]

    article_match = _ARTICLE_NUMBER.search(query)
    if article_match is not None:
        article_number = article_match.group("number").split(".", maxsplit=1)[0]
        conditions.append(
            models.FieldCondition(
                key="metadata.bloque_titulo",
                match=models.MatchText(text=f"Artículo {article_number}"),
            )
        )

    return models.Filter(must=conditions)
