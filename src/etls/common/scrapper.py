import logging as lg
from abc import ABC, abstractmethod
from datetime import date, timedelta

import requests

from src.etls.common.http import create_session
from src.etls.common.metadata import MetadataDocument


class BaseScrapper(ABC):
    """Downloads the documents of a bulletin.

    A failure in one document or day is logged and recorded in ``failures`` instead of aborting the whole run,
    so that the caller can report it.
    """

    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or create_session()
        self.failures: list[str] = []

    def download_days(self, date_start: date, date_end: date) -> list[MetadataDocument]:
        """Download all the documents published from date_start to date_end, both included."""
        logger = lg.getLogger(self.download_days.__name__)
        logger.info("Downloading %s content from day %s to %s", type(self).__name__, date_start, date_end)
        docs: list[MetadataDocument] = []
        day = date_start
        while day <= date_end:
            try:
                docs += self.download_day(day)
            except Exception:
                self._record_failure(f"day {day.isoformat()}")
            day += timedelta(days=1)
        logger.info("Downloaded %s documents from day %s to %s", len(docs), date_start, date_end)
        return docs

    @abstractmethod
    def download_day(self, day: date) -> list[MetadataDocument]:
        """Download all the documents published on a day.

        Raise if the day's summary cannot be read. Failures of individual documents must be recorded with
        ``_record_failure`` and skipped.
        """

    def _record_failure(self, item: str) -> None:
        """Log the exception being handled and record the item (URL or day) that failed."""
        lg.getLogger(type(self).__name__).exception("Failed to download %s", item)
        self.failures.append(item)
