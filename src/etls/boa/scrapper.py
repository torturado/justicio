import json
import logging as lg
import re
from datetime import date, datetime

from bs4 import BeautifulSoup

from src.etls.boa.metadata import BOAMetadataDocument
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace

SEARCH_URL = "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI"
# Sections I (Disposiciones Generales), III (Otras Disposiciones y Acuerdos) and IV (Administración de Justicia), in the
# query syntax of the BOA search engine ("o" means OR). The whole bulletin would be
# "BOA+o+Disposiciones+o+Personal+o+Acuerdos+o+Justicia+o+Anuncios".
SECTIONS_QUERY = "BOA+o+Disposiciones+o+Justicia"
PAGE_SIZE = 250
NO_DOCUMENTS_MESSAGE = "No se han recuperado documentos"

# A backslash that does not start a valid JSON escape is literal text, e.g. the Word field code `HYPERLINK \l "3"`
_JSON_ESCAPE = re.compile(r'(\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})|\\')
_LINE_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_DATE = re.compile(r"\d{8}")
# Position of a result among the results of the search, e.g. "3 de 24"
_RESULT_POSITION = re.compile(r"(\d+) de (\d+)")
# Every URL of a URL field is enclosed in ` and ´, e.g. "`https://...´`https://...´   "
_FIELD_URL = re.compile(r"`([^`´]+)´")


def parse_search_response(content: bytes) -> list[dict]:
    """Results of a page of the BOA search engine in JSON mode; empty if the search found no documents.

    The response is labelled ISO-8859-1 but encoded in windows-1252 (e.g. "€" is 0x80). Its JSON can contain raw control
    characters and backslashes that are not escapes, which are kept as text.
    """
    text = content.decode("cp1252", errors="replace")
    try:
        results = json.loads(_JSON_ESCAPE.sub(lambda match: match.group(1) or r"\\", text), strict=False)
    except json.JSONDecodeError as error:
        # When nothing is found, the search engine answers with an HTML page
        if NO_DOCUMENTS_MESSAGE in text:
            return []
        raise ScrapperError("The BOA search engine did not return JSON") from error
    if not isinstance(results, list):
        raise ScrapperError("The BOA search engine did not return a list of results")
    return results


def _optional_text(value: str | None) -> str | None:
    return normalize_whitespace(value or "") or None


def _capitalized(value: str | None) -> str | None:
    text = _optional_text(value)
    return text.capitalize() if text else None


def _iso_date(value: str | None) -> str | None:
    """ISO date of a YYYYMMDD value, or None if it is empty or not a date."""
    # strptime alone would also accept shorter values, e.g. "2024013"
    if not value or not _DATE.fullmatch(value):
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _first_url(value: str | None) -> str | None:
    match = _FIELD_URL.search(value or "")
    return match.group(1).strip() if match else None


def _html_to_text(html: str) -> str:
    """Plain text of the HTML body of a BOA document, one line per paragraph (paragraphs are separated by <br/>)."""
    # Line breaks in the HTML source are just whitespace
    text = BeautifulSoup(_LINE_BREAK.sub("\n", normalize_whitespace(html)), "html.parser").get_text()
    lines = (normalize_whitespace(line) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def parse_document(result: dict) -> BOAMetadataDocument:
    """Text and metadata of a document from a result of the BOA search engine (see README.md for an example)."""
    identificador = result["DOCN"]
    text = _html_to_text(result.get("Texto") or "")
    if not text:
        raise ScrapperError(f"BOA document {identificador} has no text")
    fecha_publicacion = _iso_date(result.get("FechaPublicacion"))
    if fecha_publicacion is None:
        raise ScrapperError(f"BOA document {identificador} has no publication date")
    return BOAMetadataDocument(
        text=text,
        identificador=identificador,
        numero_boletin=result["Numeroboletin"],
        titulo=_optional_text(result.get("Titulo")),
        departamento=_capitalized(result.get("Emisor")),
        rango=_capitalized(result.get("Rango")),
        seccion=_optional_text(result.get("Seccion")),
        subseccion=_optional_text(result.get("Subseccion")),
        codigo_materia=_optional_text(result.get("CodigoMateria")),
        fecha_publicacion=fecha_publicacion,
        fecha_disposicion=_iso_date(result.get("Fechadisposicion")),
        url_pdf=_first_url(result.get("UrlPdf")),
        url_boletin=_first_url(result.get("UrlBCOM")),
    )


def _result_position(result: dict) -> tuple[int, int]:
    """Position of a result and total number of results of the search."""
    match = _RESULT_POSITION.fullmatch(str(result.get("NOrden", "")).strip())
    if match is None:
        raise ScrapperError(f"Unexpected position of a BOA result: {result.get('NOrden')!r}")
    return int(match.group(1)), int(match.group(2))


class BOAScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOAMetadataDocument]:
        logger = lg.getLogger(self.download_day.__name__)
        docs = []
        first = 1
        total = 1
        while first <= total:
            results = self._search(day, first)
            if not results:
                break
            position, total = _result_position(results[0])
            # Asked for results past the last one, the search engine returns the first page again
            if position != first:
                raise ScrapperError(f"BOA results of {day} start at {position} instead of {first}")
            for result in results:
                try:
                    docs.append(parse_document(result))
                except Exception:
                    self._record_failure(f"BOA document {result.get('DOCN')} of {day.isoformat()}")
            first += len(results)
        logger.info("BOA of %s has %s documents in the indexed sections", day, len(docs))
        return docs

    def _search(self, day: date, first: int) -> list[dict]:
        """A page of the documents published on a day in the indexed sections, starting at position ``first``."""
        params = {
            "CMD": "VERLST",
            "BASE": "BZHT",
            "DOCS": f"{first}-{first + PAGE_SIZE - 1}",
            "SEC": "OPENDATABOAJSONAPP",
            "OUTPUTMODE": "JSON",
            "SEPARADOR": "",
            "PUBL-C": day.strftime("%Y%m%d"),
            "SECC-C": SECTIONS_QUERY,
        }
        response = self._session.get(SEARCH_URL, params=params)
        response.raise_for_status()
        return parse_search_response(response.content)
