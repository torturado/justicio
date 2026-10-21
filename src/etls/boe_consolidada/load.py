import logging as lg
import typing as tp
from datetime import date, datetime, timedelta

import typer
from qdrant_client import models

from src.email.send_email import send_email
from src.etls.boe_consolidada.defs import COLLECTION_NAME
from src.etls.boe_consolidada.parser import (
    build_chunks,
    catalogue_item_from_document,
    document_is_in_force,
)
from src.etls.boe_consolidada.scrapper import BOEConsolidadaScrapper
from src.etls.utils import catch_exceptions
from src.initialize import initialize_app
from src.vector_store import VectorStore

app = typer.Typer()


def _process_item(
    item: tp.Dict[str, tp.Any],
    scrapper: BOEConsolidadaScrapper,
    vector_store: VectorStore,
    today: date,
) -> str:
    logger = lg.getLogger(_process_item.__name__)
    identificador = item.get("identificador", "")
    if item.get("vigencia_agotada") == "S":
        vector_store.delete_document(identificador)
        logger.info("Deleted expired norm [%s]", identificador)
        return "deleted"

    document_root = scrapper.download_document(identificador)
    return _process_document(item, document_root, vector_store, today)


def _process_document(item: tp.Dict[str, tp.Any], document_root, vector_store: VectorStore, today: date) -> str:
    logger = lg.getLogger(_process_document.__name__)
    identificador = item.get("identificador", "")
    if not document_is_in_force(document_root):
        vector_store.delete_document(identificador)
        logger.info("Deleted norm no longer in force [%s]", identificador)
        return "deleted"

    chunks = build_chunks(item, document_root, today)
    vector_store.replace_document(identificador, chunks)
    logger.info("Indexed consolidated norm [%s] with %s chunks", identificador, len(chunks))
    return "loaded"


def _process_catalogue(
    items: tp.Iterable[tp.Dict[str, tp.Any]],
    scrapper: BOEConsolidadaScrapper,
    vector_store: VectorStore,
    today: date,
    skip_identifiers: tp.AbstractSet[str] = frozenset(),
) -> tp.Dict[str, int]:
    logger = lg.getLogger(_process_catalogue.__name__)
    stats = {"loaded": 0, "deleted": 0, "skipped": 0, "failed": 0}
    seen: tp.Set[str] = set()
    for item in items:
        identificador = item.get("identificador", "")
        if not identificador or identificador in seen:
            continue
        seen.add(identificador)
        try:
            if item.get("vigencia_agotada") != "S" and identificador in skip_identifiers:
                stats["skipped"] += 1
                continue
            result = _process_item(item, scrapper, vector_store, today)
            stats[result] += 1
        except Exception:
            stats["failed"] += 1
            logger.exception("Failed to process consolidated norm [%s]; continuing", identificador)
    return stats


def _process_identifier(
    identificador: str,
    scrapper: BOEConsolidadaScrapper,
    vector_store: VectorStore,
    today: date,
) -> str:
    document_root = scrapper.download_document(identificador)
    item = catalogue_item_from_document(document_root)
    if not item.get("identificador"):
        item["identificador"] = identificador
    return _process_document(item, document_root, vector_store, today)


def _run_all(init_objects, today: tp.Optional[date] = None) -> tp.Dict[str, int]:
    day = today or date.today()
    scrapper = BOEConsolidadaScrapper()
    vector_store = init_objects.vector_store[COLLECTION_NAME]
    existing_consolidated = vector_store.identificadores(
        models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.source_type",
                    match=models.MatchValue(value="Legislación consolidada"),
                )
            ]
        )
    )
    return _process_catalogue(
        scrapper.iter_catalogue(),
        scrapper,
        vector_store,
        day,
        skip_identifiers=existing_consolidated,
    )


def _run_since(init_objects, date_start: date, today: tp.Optional[date] = None) -> tp.Dict[str, int]:
    day = today or date.today()
    scrapper = BOEConsolidadaScrapper()
    return _process_catalogue(
        scrapper.iter_catalogue(date_from=date_start),
        scrapper,
        init_objects.vector_store[COLLECTION_NAME],
        day,
    )


def _run_today(init_objects, today: tp.Optional[date] = None) -> tp.Dict[str, int]:
    day = today or date.today()
    vector_store = init_objects.vector_store[COLLECTION_NAME]
    scrapper = BOEConsolidadaScrapper()
    updated_items = list(scrapper.iter_catalogue(date_from=day - timedelta(days=1)))
    updated_ids = {item.get("identificador") for item in updated_items}
    revision_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.fecha_revision",
                range=models.DatetimeRange(lte=f"{day.isoformat()}T23:59:59Z"),
            )
        ]
    )
    revision_ids = vector_store.identificadores(revision_filter)
    stats = _process_catalogue(updated_items, scrapper, vector_store, day)

    for identificador in sorted(revision_ids - updated_ids):
        try:
            result = _process_identifier(identificador, scrapper, vector_store, day)
            stats[result] += 1
        except Exception:
            stats["failed"] += 1
            lg.getLogger(_run_today.__name__).exception(
                "Failed to reload consolidated norm [%s] due to fecha_revision; continuing", identificador
            )
    return stats


@app.command(name="all")
def all_documents():
    init_objects = initialize_app()
    stats = _run_all(init_objects)
    lg.getLogger(all_documents.__name__).info("Consolidated catalogue load completed: %s", stats)


@app.command()
def since(date_start: str):
    init_objects = initialize_app()
    start = datetime.strptime(date_start, "%Y/%m/%d").date()
    stats = _run_since(init_objects, start)
    lg.getLogger(since.__name__).info("Consolidated since load completed: %s", stats)


@app.command()
def document(identificador: str):
    init_objects = initialize_app()
    scrapper = BOEConsolidadaScrapper()
    result = _process_identifier(identificador, scrapper, init_objects.vector_store[COLLECTION_NAME], date.today())
    lg.getLogger(document.__name__).info("Consolidated norm [%s]: %s", identificador, result)


@app.command()
@catch_exceptions(cancel_on_failure=True)
def today(init_objects=None):
    if init_objects is None:
        init_objects = initialize_app()
    day = date.today()
    stats = _run_today(init_objects, day)
    subject = "[BOE consolidada] Daily ETL executed"
    content = f"""
    Daily consolidated legislation ETL executed
    - Date: {day}
    - Norms loaded: {stats['loaded']}
    - Norms deleted: {stats['deleted']}
    - Norms skipped: {stats['skipped']}
    - Norms failed: {stats['failed']}
    """
    send_email(init_objects.config_loader, subject, content)


if __name__ == "__main__":
    app()
