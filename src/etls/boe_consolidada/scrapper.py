import logging as lg
from collections.abc import Iterator
from datetime import date
from typing import Any

import requests
from lxml import etree

from src.etls.common.http import create_session

API_BASE_URL = "https://www.boe.es/datosabiertos/api/legislacion-consolidada"
CATALOGUE_PAGE_SIZE = 10000
REQUEST_TIMEOUT_SECONDS = 180


class BOEConsolidadaScrapper:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or create_session(timeout=REQUEST_TIMEOUT_SECONDS)

    def iter_catalogue(self, date_from: date | None = None, date_to: date | None = None) -> Iterator[dict[str, Any]]:
        """Catalogue items of the consolidated norms, optionally only those updated within a date range."""
        logger = lg.getLogger(self.iter_catalogue.__name__)
        offset = 0
        params: dict[str, Any] = {"limit": CATALOGUE_PAGE_SIZE}
        if date_from:
            params["from"] = date_from.strftime("%Y%m%d")
        if date_to:
            params["to"] = date_to.strftime("%Y%m%d")

        while True:
            params["offset"] = offset
            response = self._session.get(API_BASE_URL, params=params, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json().get("data", {})
            items = data if isinstance(data, list) else []
            logger.info("Fetched consolidated catalogue page at offset %s (%s norms)", offset, len(items))
            yield from items
            if len(items) < CATALOGUE_PAGE_SIZE:
                return
            offset += len(items)

    def download_document(self, identificador: str) -> etree._Element:
        response = self._session.get(f"{API_BASE_URL}/id/{identificador}", headers={"Accept": "application/xml"})
        response.raise_for_status()
        return etree.fromstring(response.content)
