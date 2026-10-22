"""Example scrapper of a fictitious bulletin. See README.md to add a source."""

import logging as lg
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.etls.common.http import get_soup
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace
from src.etls.template.metadata import TemplateMetadataDocument

BASE_URL = "https://boletin.example.org/"


def parse_summary(soup: BeautifulSoup, url: str) -> list[str]:
    """URLs of the documents listed in the summary of a day."""
    return [urljoin(url, link["href"]) for link in soup.select("ul.sumario a[href]")]


def parse_document(soup: BeautifulSoup, url: str, day: date) -> TemplateMetadataDocument:
    """Text and metadata of a document page."""
    body = soup.find("div", class_="texto")
    title = soup.find("h1")
    if body is None or title is None:
        raise ScrapperError(f"The document page {url} has no title or text")
    # One line per paragraph helps the text splitter keep paragraphs together
    paragraphs = (normalize_whitespace(p.get_text()) for p in body.find_all("p"))
    text = "\n".join(paragraph for paragraph in paragraphs if paragraph)
    if not text:
        raise ScrapperError(f"The document page {url} has no text")
    section = soup.find("p", class_="seccion")
    return TemplateMetadataDocument(
        text=text,
        # Stable within the source: documents already indexed with it are skipped
        identificador=url.rstrip("/").rsplit("/", 1)[-1],
        # The title is embedded with each chunk, so it must describe the document
        titulo=normalize_whitespace(title.get_text()),
        seccion=normalize_whitespace(section.get_text()) if section is not None else None,
        fecha_publicacion=day.isoformat(),
        url_html=url,
    )


class TemplateScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[TemplateMetadataDocument]:
        """Download the documents published on a day.

        A failure to read the summary propagates, so that BaseScrapper.download_days records the day. A failure of a
        document is recorded and the remaining documents are downloaded.
        """
        logger = lg.getLogger(self.download_day.__name__)
        summary_url = urljoin(BASE_URL, f"{day:%Y/%m/%d}/")
        documents = []
        for url in parse_summary(get_soup(self._session, summary_url), summary_url):
            try:
                documents.append(self.download_document(url, day))
            except Exception:
                self._record_failure(url)
        logger.info("Downloaded %s TEMPLATE documents for day %s", len(documents), day)
        return documents

    def download_document(self, url: str, day: date) -> TemplateMetadataDocument:
        return parse_document(get_soup(self._session, url), url, day)
