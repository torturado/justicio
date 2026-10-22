"""ETL command line. Run ``python -m src.etls.cli --help``.

Every run emails a summary (see ``src.email.send_email``) and exits with status 1 if anything failed, so that
the scheduler (cron) can detect it.
"""

import logging as lg
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Annotated

import typer
from langchain_text_splitters import TextSplitter
from qdrant_client import models

from src.email.send_email import send_email
from src.etls.boe_consolidada.load import ConsolidatedLoader, ConsolidatedStats
from src.etls.common.etl import create_text_splitter, index_documents
from src.etls.sources import BULLETIN_SCRAPPERS
from src.initialize import create_embedder, create_qdrant_client, initialize_logging
from src.settings import load_settings
from src.vector_store import VectorIndex, match

# Most days a bulletin update goes back to catch up after missed runs
CATCH_UP_MAX_DAYS = 30
DateArgument = Annotated[datetime, typer.Argument(formats=["%Y/%m/%d"])]

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Load official documents into the vector index.")
bulletin_app = typer.Typer(no_args_is_help=True, help="Daily bulletins: " + ", ".join(BULLETIN_SCRAPPERS))
consolidated_app = typer.Typer(no_args_is_help=True, help="BOE consolidated legislation.")
app.add_typer(bulletin_app, name="bulletin")
app.add_typer(consolidated_app, name="consolidated")


@dataclass
class EtlContext:
    index: VectorIndex
    text_splitter: TextSplitter
    today: date


@dataclass
class JobReport:
    name: str
    summary: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"{self.name}: {'FAILED' if self.failures else 'OK'}", *(f"- {line}" for line in self.summary)]
        lines += [f"- Failure: {failure}" for failure in self.failures]
        return "\n".join(lines)


Job = tuple[str, Callable[[EtlContext], JobReport]]


def _create_context() -> EtlContext:
    settings = load_settings()
    unconfigured = BULLETIN_SCRAPPERS.keys() - settings.sources.keys()
    if unconfigured:
        raise RuntimeError(f"Sources without a scope in config.yaml: {sorted(unconfigured)}")
    index = VectorIndex(create_qdrant_client(), settings.qdrant_collection, create_embedder(settings))
    index.ensure_collection()
    return EtlContext(index=index, text_splitter=create_text_splitter(settings.chunk_size), today=date.today())


def _run(jobs: list[Job]) -> None:
    """Run the jobs in order, isolating their failures, then notify and set the exit status."""
    initialize_logging()
    logger = lg.getLogger(_run.__name__)
    reports: list[JobReport] = []
    try:
        context = _create_context()
    except Exception as error:
        logger.exception("Could not initialize the ETL")
        reports.append(JobReport("Initialization", failures=[f"{type(error).__name__}: {error}"]))
    else:
        for name, job in jobs:
            try:
                reports.append(job(context))
            except Exception as error:
                logger.exception("ETL job %s failed", name)
                reports.append(JobReport(name, failures=[f"{type(error).__name__}: {error}"]))

    failed = any(report.failures for report in reports)
    report_text = "\n\n".join(report.render() for report in reports)
    logger.info("ETL report:\n%s", report_text)
    try:
        send_email(
            f"[Justicio ETL] {'Failed' if failed else 'Succeeded'}: {', '.join(name for name, _ in jobs)}", report_text
        )
    except Exception:
        logger.exception("Could not send the ETL report")
    if failed:
        raise typer.Exit(code=1)


def _source(name: str) -> str:
    source = name.upper()
    if source not in BULLETIN_SCRAPPERS:
        raise typer.BadParameter(f"Unknown source {name}. Available: {', '.join(BULLETIN_SCRAPPERS)}")
    return source


def _load_bulletin(context: EtlContext, source: str, date_start: date, date_end: date) -> JobReport:
    scrapper = BULLETIN_SCRAPPERS[source]()
    docs = scrapper.download_days(date_start, date_end)
    stats = index_documents(docs, context.index, context.text_splitter)
    return JobReport(
        name=f"{source} {date_start.isoformat()} to {date_end.isoformat()}",
        summary=[
            f"Documents downloaded: {len(docs)}",
            f"Documents indexed: {stats.indexed}",
            f"Documents already indexed: {stats.skipped}",
        ],
        failures=[f"download of {item}" for item in scrapper.failures]
        + [f"indexing of {identificador}" for identificador in stats.failed],
    )


def _update_bulletin(context: EtlContext, source: str) -> JobReport:
    """Load from the latest indexed publication day (included, in case it was partially loaded) to today."""
    latest = context.index.latest_date(
        "fecha_publicacion", models.Filter(must=[match("source_name", source), match("source_type", "Boletin")])
    )
    date_start = context.today if latest is None else max(latest, context.today - timedelta(days=CATCH_UP_MAX_DAYS))
    return _load_bulletin(context, source, date_start, context.today)


def _consolidated_report(name: str, stats: ConsolidatedStats) -> JobReport:
    return JobReport(
        name=name,
        summary=[f"Norms loaded: {stats.loaded}", f"Norms deleted: {stats.deleted}", f"Norms skipped: {stats.skipped}"],
        failures=[f"norm {identificador}" for identificador in stats.failed],
    )


def _consolidated_job(name: str, load: Callable[[ConsolidatedLoader], ConsolidatedStats]) -> Job:
    def job(context: EtlContext) -> JobReport:
        loader = ConsolidatedLoader(index=context.index, text_splitter=context.text_splitter, today=context.today)
        return _consolidated_report(name, load(loader))

    return name, job


@bulletin_app.command("update")
def bulletin_update(source: str) -> None:
    """Load a bulletin from its latest indexed publication day to today."""
    source = _source(source)
    _run([(f"{source} update", lambda context: _update_bulletin(context, source))])


@bulletin_app.command("dates")
def bulletin_dates(
    source: str,
    date_start: DateArgument,
    date_end: DateArgument,
) -> None:
    """Load a bulletin for a date range (YYYY/MM/DD, both included)."""
    source = _source(source)
    _run([(f"{source} dates", lambda context: _load_bulletin(context, source, date_start.date(), date_end.date()))])


@consolidated_app.command("all")
def consolidated_all() -> None:
    """Initial load of the whole catalogue of consolidated norms in force."""
    _run([_consolidated_job("BOE consolidated catalogue", ConsolidatedLoader.load_all)])


@consolidated_app.command("since")
def consolidated_since(date_start: DateArgument) -> None:
    """Load the consolidated norms updated since a date (YYYY/MM/DD)."""
    name = f"BOE consolidated since {date_start.date().isoformat()}"
    _run([_consolidated_job(name, lambda loader: loader.load_since(date_start.date()))])


@consolidated_app.command("document")
def consolidated_document(identificador: str) -> None:
    """Load one consolidated norm, e.g. BOE-A-2015-10565."""

    def job(context: EtlContext) -> JobReport:
        loader = ConsolidatedLoader(index=context.index, text_splitter=context.text_splitter, today=context.today)
        return JobReport(
            name=f"BOE consolidated {identificador}", summary=[f"Norm {loader.load_document(identificador)}"]
        )

    _run([(f"BOE consolidated {identificador}", job)])


@consolidated_app.command("update")
def consolidated_update() -> None:
    """Load the norms updated since the latest indexed update and those due for revision."""
    _run([_consolidated_job("BOE consolidated update", ConsolidatedLoader.update)])


@app.command()
def daily() -> None:
    """Update every bulletin, then the consolidated legislation. Schedule it once a day."""
    jobs: list[Job] = [
        (f"{source} update", lambda context, source=source: _update_bulletin(context, source))
        for source in BULLETIN_SCRAPPERS
    ]
    _run([*jobs, _consolidated_job("BOE consolidated update", ConsolidatedLoader.update)])


if __name__ == "__main__":
    app()
