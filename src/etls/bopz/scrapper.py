import logging as lg
import re
from datetime import date, datetime
from typing import TypedDict
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from src.etls.bopz.metadata import BOPZMetadataDocument
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace

BASE_URL = "https://bop.dpz.es/BOPZ/"
SEARCH_URL = urljoin(BASE_URL, "portalBuscarEdictos.do")
EDICT_URL = urljoin(BASE_URL, "obtenerContenidoEdicto.do")

# Fields of the edict search form. The publisher fields restrict the search to the Ayuntamiento de Zaragoza.
SEARCH_FORM = {
    "numPag": "",
    "idProcedente": "8610",
    "idPortador": "",
    "hProcedente": " AYUNTAMIENTO DE ZARAGOZA",
    "hPortador": "",
    "idPagadora": "8610",
    "hPagadora": " AYUNTAMIENTO DE ZARAGOZA",
    "primeraVez": "N",
    "ficheroDoc": "N",
    "numRegistroInf": "",
    "numRegistroSup": "",
    "esPortalSN": "S",
    "numBoletinInf": "",
    "numRegistroNumInf": "",
    "numRegistroAnyoInf": "",
    "numRegistroNumSup": "",
    "numRegistroAnyoSup": "",
    "numBoletinAuxInf": "",
    "anyoBoletinInf": "",
    "numBoletinSup": "",
    "anyoBoletinSup": "",
    "fechaPubInf": "",
    "fechaPubSup": "",
    "procedente": " AYUNTAMIENTO DE ZARAGOZA",
    "tematica": "",
    "titulo": "",
    "contenido": "",
}


class ListingMetadata(TypedDict, total=False):
    numero_registro: str
    departamento: str
    materia: list[str]
    fecha_publicacion: str


def _parse_html(response: requests.Response, parser: str) -> BeautifulSoup:
    response.raise_for_status()
    # The BOPZ declares its charset (ISO-8859-1) only in the Content-Type header, so the page is decoded by requests:
    # parsing the raw bytes guesses a wrong encoding for the accented letters
    return BeautifulSoup(response.text, parser)


def _extract_span_text(row: Tag, regex: str) -> str | None:
    """Text that follows the label span of a listing row whose text matches the regex, or None if it is missing."""
    span = row.find("span", string=lambda text: text is not None and re.search(regex, text) is not None)
    value = span.next_sibling if span is not None else None
    if not isinstance(value, NavigableString):
        return None
    return normalize_whitespace(value) or None


def bulletin_pdf_url(row: Tag) -> str | None:
    """URL of the bulletin PDF attached to a listing row, or None if the edict was not published in a bulletin."""
    anchor = row.find("a", class_="adjunto", href=True)
    if anchor is None:
        return None
    href = anchor["href"]
    if "UploadServlet?ruta=Boletines" not in href or not href.endswith(".pdf"):
        return None
    return urljoin(BASE_URL, href)


def parse_listing(soup: BeautifulSoup) -> list[tuple[Tag, str]]:
    """Rows of a search result page of the edicts published in a bulletin, with the URL of their PDF."""
    rows = []
    for row in soup.find_all("div", class_="row listadoEdictos"):
        if url_pdf := bulletin_pdf_url(row):
            rows.append((row, url_pdf))
    return rows


def has_page(soup: BeautifulSoup, page: int) -> bool:
    """Whether the pager of a search result page links a page (numbered from 0), e.g. setPagina(1) for the second
    one. Results fit in one page (20 edicts) when there is no pager."""
    pager = soup.find("ul", class_="pagination")
    return pager is not None and pager.find("a", onclick=re.compile(rf"setPagina\({page}\)")) is not None


def edict_id(row: Tag) -> str | None:
    """idEdicto of a listing row: the first quoted argument of its onclick handler, e.g. abrirEdicto('729066')."""
    onclick_div = row.find("div", onclick=True)
    match = re.search(r"'([^']+)'", onclick_div["onclick"]) if onclick_div is not None else None
    return match.group(1) if match else None


def extract_metadata(row: Tag) -> ListingMetadata:
    """Metadata shown in a listing row."""
    metadata = ListingMetadata()
    # Registry number label, e.g. "Nº. Reg:" or "N.º Reg:"
    if numero_registro := _extract_span_text(row, r"N.{0,2} ?Reg:"):
        metadata["numero_registro"] = numero_registro
    if departamento := _extract_span_text(row, r"Publicador:"):
        metadata["departamento"] = departamento
    if materia := _extract_span_text(row, r"Materia"):
        metadata["materia"] = [materia]
    if fecha_publicacion := _extract_span_text(row, r"Fecha Pub:"):
        metadata["fecha_publicacion"] = datetime.strptime(fecha_publicacion, "%d/%m/%Y").date().isoformat()
    return metadata


def parse_edict(soup: BeautifulSoup) -> tuple[str, str | None]:
    """Text and summary of an edict page, e.g. https://bop.dpz.es/BOPZ/obtenerContenidoEdicto.do?idEdicto=729066."""
    panel = soup.find("div", class_="medium-12 panel")
    text = panel.get_text(strip=True, separator="\n") if panel is not None else ""
    if not text:
        raise ScrapperError("The BOPZ edict page has no text")
    # The page title is "Edicto: <summary>"
    page_title = normalize_whitespace(soup.title.get_text()) if soup.title is not None else ""
    summary = re.sub(r"^Edicto:\s*", "", page_title)
    return text, (summary if summary and summary != "Edicto" else None)


def build_title(summary: str | None, metadata: ListingMetadata) -> str | None:
    """Title embedded with the text: the summary of the edict followed by its publisher and subjects."""
    context = ", ".join(part for part in (metadata.get("departamento"), *metadata.get("materia", [])) if part)
    if summary and context:
        return f"{summary} ({context})"
    return summary or context or None


class BOPZScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOPZMetadataDocument]:
        """Download the edicts of the Ayuntamiento de Zaragoza published on a day."""
        logger = lg.getLogger(self.download_day.__name__)
        logger.info("Downloading BOPZ content for day %s", day)
        documents = []
        for row, url_pdf in self._search_day(day):
            try:
                documents.append(self.download_document(row, url_pdf))
            except Exception:
                # The PDF identifies the edict even when the row has no edict id
                self._record_failure(url_pdf)
        logger.info("Downloaded %s BOPZ documents for day %s", len(documents), day)
        return documents

    def download_document(self, row: Tag, url_pdf: str) -> BOPZMetadataDocument:
        """Download the text of the edict of a listing row and combine it with the metadata of the row."""
        identificador = edict_id(row)
        if identificador is None:
            raise ScrapperError("The BOPZ listing row has no edict id")
        url = f"{EDICT_URL}?idEdicto={identificador}"
        lg.getLogger(self.download_document.__name__).info("Scrapping document: %s", url)
        text, summary = parse_edict(_parse_html(self._session.get(url), "lxml"))
        metadata = extract_metadata(row)
        return BOPZMetadataDocument(
            text=text,
            identificador=identificador,
            titulo=build_title(summary, metadata),
            url_pdf=url_pdf,
            url_html=url,
            **metadata,
        )

    def _search_day(self, day: date) -> list[tuple[Tag, str]]:
        rows = self._search(day, day)
        lg.getLogger(self._search_day.__name__).info("Found %s BOPZ edicts published on %s", len(rows), day)
        return rows

    def _search(self, date_start: date, date_end: date) -> list[tuple[Tag, str]]:
        """Listing rows of the edicts published from date_start to date_end, both included, from every result page."""
        form = {
            **SEARCH_FORM,
            "fechaPubInf": date_start.strftime("%d/%m/%Y"),
            "fechaPubSup": date_end.strftime("%d/%m/%Y"),
        }
        rows: list[tuple[Tag, str]] = []
        page = 0
        while True:
            soup = _parse_html(self._session.post(SEARCH_URL, data={**form, "numPag": str(page)}), "html.parser")
            rows += parse_listing(soup)
            page += 1
            if not has_page(soup, page):
                return rows
