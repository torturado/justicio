"""Loads of BOE consolidated legislation into the vector index."""

import logging as lg
from collections.abc import Callable, Iterable, Set
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from langchain_text_splitters import TextSplitter
from lxml import etree
from qdrant_client import models

from src.etls.boe_consolidada.parser import (
    SOURCE_NAME,
    SOURCE_TYPE,
    build_chunks,
    catalogue_item_from_document,
    document_is_in_force,
)
from src.etls.boe_consolidada.scrapper import BOEConsolidadaScrapper
from src.vector_store import VectorIndex, match


@dataclass
class ConsolidatedStats:
    loaded: int = 0
    deleted: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


@dataclass
class ConsolidatedLoader:
    index: VectorIndex
    text_splitter: TextSplitter
    today: date
    scrapper: BOEConsolidadaScrapper = field(default_factory=BOEConsolidadaScrapper)

    def load_all(self) -> ConsolidatedStats:
        """Initial load of the catalogue, skipping the norms already indexed."""
        indexed = self.index.identificadores(_consolidated_filter())
        return self._process_catalogue(self.scrapper.iter_catalogue(), skip_identifiers=indexed)

    def load_since(self, date_start: date) -> ConsolidatedStats:
        return self._process_catalogue(self.scrapper.iter_catalogue(date_from=date_start))

    def update(self) -> ConsolidatedStats:
        """Reload the norms updated since the latest indexed update, and those with a due fecha_revision.

        Starting from the latest indexed fecha_actualizacion catches up after missed runs.
        """
        latest_update = self.index.latest_date("fecha_actualizacion", _consolidated_filter())
        date_from = latest_update or self.today - timedelta(days=1)
        lg.getLogger(self.update.__name__).info("Loading consolidated norms updated since %s", date_from)
        updated_items = list(self.scrapper.iter_catalogue(date_from=date_from))
        revision_filter = _consolidated_filter(
            models.FieldCondition(
                key="fecha_revision", range=models.DatetimeRange(lte=f"{self.today.isoformat()}T23:59:59Z")
            )
        )
        revision_ids = self.index.identificadores(revision_filter)
        stats = self._process_catalogue(updated_items)
        updated_ids = {item.get("identificador") for item in updated_items}
        for identificador in sorted(revision_ids - updated_ids):
            self._record(stats, identificador, self.load_document, identificador)
        return stats

    def load_document(self, identificador: str) -> str:
        document_root = self.scrapper.download_document(identificador)
        item = catalogue_item_from_document(document_root)
        if not item.get("identificador"):
            item["identificador"] = identificador
        return self._load(item, document_root)

    def _process_catalogue(
        self, items: Iterable[dict[str, Any]], skip_identifiers: Set[str] = frozenset()
    ) -> ConsolidatedStats:
        stats = ConsolidatedStats()
        seen: set[str] = set()
        for item in items:
            identificador = item.get("identificador", "")
            if not identificador or identificador in seen:
                continue
            seen.add(identificador)
            if item.get("vigencia_agotada") != "S" and identificador in skip_identifiers:
                stats.skipped += 1
                continue
            self._record(stats, identificador, self._load_item, item)
        return stats

    def _record(self, stats: ConsolidatedStats, identificador: str, load: Callable[[Any], str], argument: Any) -> None:
        """Run a load and count its result; a failing norm is logged and recorded, not fatal."""
        try:
            result = load(argument)
        except Exception:
            lg.getLogger(self._record.__name__).exception("Failed to load consolidated norm [%s]", identificador)
            stats.failed.append(identificador)
            return
        if result == "deleted":
            stats.deleted += 1
        else:
            stats.loaded += 1

    def _load_item(self, item: dict[str, Any]) -> str:
        identificador = item["identificador"]
        if item.get("vigencia_agotada") == "S":
            self.index.delete_document(SOURCE_NAME, identificador)
            lg.getLogger(self._load_item.__name__).info("Deleted expired norm [%s]", identificador)
            return "deleted"
        return self._load(item, self.scrapper.download_document(identificador))

    def _load(self, item: dict[str, Any], document_root: etree._Element) -> str:
        logger = lg.getLogger(self._load.__name__)
        identificador = item["identificador"]
        # The consolidated text replaces the originally published text, which shares its identifier
        if not document_is_in_force(document_root):
            self.index.delete_document(SOURCE_NAME, identificador)
            logger.info("Deleted norm no longer in force [%s]", identificador)
            return "deleted"
        chunks = build_chunks(item, document_root, self.today, self.text_splitter)
        self.index.replace_document(SOURCE_NAME, identificador, chunks)
        logger.info("Indexed consolidated norm [%s] with %s chunks", identificador, len(chunks))
        return "loaded"


def _consolidated_filter(*conditions: models.Condition) -> models.Filter:
    return models.Filter(must=[match("source_name", SOURCE_NAME), match("source_type", SOURCE_TYPE), *conditions])
