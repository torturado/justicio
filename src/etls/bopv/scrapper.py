import ast
import logging as lg
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.etls.bopv.metadata import BOPVMetadataDocument
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace

# Calendar of the bulletins of a month (MMYYYY), e.g. https://www.euskadi.eus/bopv2/datos/102026.shtml
CALENDAR_URL = "https://www.euskadi.eus/bopv2/datos/{month}.shtml"
# Directory of the summaries and documents of a month
MONTH_URL = "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/{year}/{month:02d}/"
SECTIONS = ("DISPOSICIONES GENERALES", "OTRAS DISPOSICIONES")

_SUMMARY_FILE = re.compile(r"[\w-]+\.shtml")
# Norm type that starts the title of a disposition, e.g. "ORDEN de 24 de septiembre..." or "DECRETO 163/2026, de..."
_RANGO = re.compile(r"[A-ZÁÉÍÓÚÜÑ]{2,}(?= (?:de |\d))")
# Paragraphs and headings of the text of a document, e.g. p.BOPVDetalle, h6.BOPVClave or p.BOPVFirmaNombre
_TEXT_CLASS = re.compile(r"^BOPV")


def _js_array(html: str, name: str) -> list:
    """Value of the JavaScript array literal assigned to a variable, e.g. `var name = ['a', ['b']];`.

    The literal is parsed as data and never executed: anything but literals (e.g. a function call) is rejected.
    """
    match = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*(\[.*?\])\s*;", html, re.DOTALL)
    if match is None:
        raise ScrapperError(f"The page has no JavaScript array {name}")
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError) as error:
        raise ScrapperError(f"The JavaScript array {name} is not a literal") from error
    if not isinstance(value, list):
        raise ScrapperError(f"The JavaScript variable {name} is not an array")
    return value


def parse_calendar(html: str) -> dict[str, list[str]]:
    """Summary files of each publication day (YYYYMMDD) in the calendar page of a month.

    The page defines them in JavaScript, e.g. `var diasHabilitados = ['20261001','20261002'];` and
    `var enlaces = [['s26_0188.shtml'],['s26_0189.shtml']];`. A day can have more than one bulletin.
    """
    days = _js_array(html, "diasHabilitados")
    links = _js_array(html, "enlaces")
    if len(days) != len(links):
        raise ScrapperError("The BOPV calendar has a different number of days and summaries")
    calendar = {}
    for day, day_links in zip(days, links, strict=True):
        files = day_links if isinstance(day_links, list) else [day_links]
        if not isinstance(day, str) or not all(
            isinstance(file, str) and _SUMMARY_FILE.fullmatch(file) for file in files
        ):
            raise ScrapperError(f"Unexpected day or summary in the BOPV calendar: {day!r}, {day_links!r}")
        calendar[day] = files
    return calendar


def _text(element: Tag | None) -> str:
    return normalize_whitespace(element.get_text()) if element is not None else ""


def parse_summary(content: bytes, url: str) -> list[str]:
    """URLs of the documents of the indexed sections in the summary of a bulletin at ``url``.

    The heading of a section is only filled in before its first document, e.g.
    https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/s26_0194.shtml
    """
    soup = BeautifulSoup(content, "html.parser")
    elements = soup.find_all(["h4", "div"], class_=["BOPVSumarioSeccion", "txtBloque"])
    if not any("txtBloque" in element["class"] for element in elements):
        raise ScrapperError("The BOPV summary has no documents")
    urls = []
    section = None
    for element in elements:
        if "BOPVSumarioSeccion" in element["class"]:
            section = _text(element).upper() or section
        elif section in SECTIONS:
            link = element.select_one("p.BOPVSumarioTitulo a[href]")
            if link is None:
                raise ScrapperError("A document of the BOPV summary has no link")
            urls.append(urljoin(url, link["href"]))
    return urls


def parse_document(content: bytes, url: str, summary_url: str, day: date) -> BOPVMetadataDocument:
    """Text and metadata of a document from its HTML page at ``url``, published on ``day`` in the bulletin at
    ``summary_url``, e.g. https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2024/02/2400757a.shtml
    """
    soup = BeautifulSoup(content, "html.parser")
    body = soup.find("div", class_="colCentralinterior")
    if body is None:
        raise ScrapperError("The BOPV document page has no content")
    titulo = _text(body.find("p", class_="BOPVTitulo"))
    seccion = _text(body.find("h4", class_="BOPVSeccion"))
    lines = [text for element in body.find_all(["p", "h6"], class_=_TEXT_CLASS) if (text := _text(element))]
    if not titulo or not seccion or not lines:
        raise ScrapperError("The BOPV document page has no title, section or text")
    rango = _RANGO.match(titulo)
    pdf_link = body.select_one("li.formatoPdf a[href]")
    return BOPVMetadataDocument(
        text="\n".join(lines),
        # e.g. "2024/02/2400757a"
        identificador="/".join(url.split(".")[-2].split("/")[-3:]),
        titulo=titulo,
        departamento=_text(body.find("h5", class_="BOPVOrganismo")) or None,
        rango=rango.group(0) if rango else None,
        seccion=seccion,
        fecha_publicacion=day.isoformat(),
        url_pdf=urljoin(url, pdf_link["href"]) if pdf_link else None,
        url_html=url,
        url_boletin=summary_url,
    )


class BOPVScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOPVMetadataDocument]:
        logger = lg.getLogger(self.download_day.__name__)
        response = self._session.get(CALENDAR_URL.format(month=day.strftime("%m%Y")))
        response.raise_for_status()
        summary_files = parse_calendar(response.text).get(day.strftime("%Y%m%d"), [])
        if not summary_files:
            logger.info("No BOPV bulletin for %s", day)

        docs = []
        for summary_file in summary_files:
            summary_url = urljoin(MONTH_URL.format(year=day.year, month=day.month), summary_file)
            response = self._session.get(summary_url)
            response.raise_for_status()
            urls = parse_summary(response.content, summary_url)
            logger.info("BOPV %s of %s has %s documents in sections %s", summary_url, day, len(urls), SECTIONS)
            for url in urls:
                try:
                    document_response = self._session.get(url)
                    document_response.raise_for_status()
                    docs.append(parse_document(document_response.content, url, summary_url, day))
                except Exception:
                    self._record_failure(url)
        return docs
