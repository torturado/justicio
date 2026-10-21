import logging as lg
import typing as tp
from datetime import date

import requests
from lxml import etree

from src.etls.boe_consolidada.defs import API_BASE_URL, CATALOGUE_PAGE_SIZE, REQUEST_TIMEOUT
from src.etls.utils import create_retry_session


class BOEConsolidadaScrapper:
    def __init__(self, session: tp.Optional[requests.Session] = None):
        self._session = session or create_retry_session(retries=5, backoff_factor=1)
        self._headers = {"Accept": "application/json"}

    def iter_catalogue(
        self, date_from: tp.Optional[date] = None, date_to: tp.Optional[date] = None
    ) -> tp.Iterator[tp.Dict[str, tp.Any]]:
        logger = lg.getLogger(self.iter_catalogue.__name__)
        offset = 0
        params: tp.Dict[str, tp.Any] = {"limit": CATALOGUE_PAGE_SIZE}
        if date_from:
            params["from"] = date_from.strftime("%Y%m%d")
        if date_to:
            params["to"] = date_to.strftime("%Y%m%d")

        while True:
            params["offset"] = offset
            response = self._session.get(
                API_BASE_URL,
                params=params,
                headers=self._headers,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json().get("data", {})
            items = data if isinstance(data, list) else []
            logger.info("Fetched consolidated catalogue page at offset %s (%s norms)", offset, len(items))
            yield from items
            if len(items) < CATALOGUE_PAGE_SIZE:
                return
            offset += len(items)

    def download_document(self, identificador: str) -> etree._Element:
        url = f"{API_BASE_URL}/id/{identificador}"
        response = self._session.get(
            url,
            headers={"Accept": "application/xml"},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return etree.fromstring(response.content)
