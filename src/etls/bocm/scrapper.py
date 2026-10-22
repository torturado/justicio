import logging as lg
import re
from datetime import date, datetime
from urllib.parse import urljoin, urlsplit

import lxml.html
from bs4 import BeautifulSoup

from src.etls.bocm.metadata import BOCMMetadataDocument
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace, xml_element_text, xml_to_text

BOCM_URL = "https://www.bocm.es"
SEARCH_DAY_URL = f"{BOCM_URL}/search-day-month"
SUMMARY_URL = BOCM_URL + "/boletin-completo/BOCM-{date}/{number}"

# Page of a bulletin, to which the day search redirects, e.g. https://www.bocm.es/boletin/bocm-20240126-22
_BULLETIN_URL = re.compile(r"/boletin/bocm-(\d{8})-(\d+)$")
_SUBSECTION_A = re.compile(r"\bA\)")
_BULLETIN_NUMBER = re.compile(r"Núm\.?\s*(\d+)")
_SECTION_CODE = re.compile(r"Sección\s+([^\s:]+)\s*:")
_PAGES = re.compile(r"Páginas:\s*(\d+)")
# Norm type that starts the title of a disposition, e.g. "RESOLUCIÓN de 15 de enero..." or "DECRETO 84/2026, de..."
_RANGO = re.compile(r"[A-ZÁÉÍÓÚÜÑ]{2,}(?= (?:de |\d))")


def parse_summary(content: bytes) -> list[str]:
    """URLs of the documents of sections I.A (Disposiciones Generales), III (Administración Local) and IV
    (Administración de Justicia) in a complete summary, e.g. https://www.bocm.es/boletin-completo/BOCM-20240103/2.
    """
    soup = BeautifulSoup(content, "lxml")
    if not soup.select('div[id^="secciones-seccion_"]'):
        raise ScrapperError("The BOCM summary has no sections")
    containers = []
    # Section I is grouped by subsection, e.g. "Sección: I. COMUNIDAD DE MADRID | A) Disposiciones Generales"
    for group in soup.select("#secciones-seccion_1 .view-grouping"):
        header = group.select_one(".view-grouping-header")
        if header is not None and _SUBSECTION_A.search(header.get_text()):
            containers.append(group)
    containers += soup.select("#secciones-seccion_3, #secciones-seccion_4")
    return [
        urljoin(BOCM_URL, link["href"]) for container in containers for link in container.select('a[href^="/bocm-"]')
    ]


def _texts(page: lxml.html.HtmlElement, xpath: str) -> list[str]:
    return [text for element in page.xpath(xpath) if (text := xml_element_text(element))]


def _attribute(page: lxml.html.HtmlElement, xpath: str) -> str | None:
    values = page.xpath(xpath)
    return normalize_whitespace(values[0]) if values else None


def _search(pattern: re.Pattern, text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None


def parse_document(content: bytes) -> BOCMMetadataDocument:
    """Text and metadata of a document from its HTML page, e.g. https://www.bocm.es/bocm-20240129-24."""
    page = lxml.html.fromstring(content)
    cve = _attribute(page, '//meta[@name="TituloGSA"]/@content')
    bodies = page.xpath('//div[@property="content:encoded"]')
    if cve is None or not bodies:
        raise ScrapperError("The BOCM document page has no CVE or no body")
    text = xml_to_text(bodies[0])
    if not text:
        raise ScrapperError(f"BOCM document {cve} has no text")

    # e.g. "Boletín Oficial de la Comunidad de Madrid - Núm 245 Sección 1.3.103.1: I. COMUNIDAD DE MADRID
    # Código de Verificación Electrónica (CVE): BOCM-20261014-32 Páginas: 2"
    header = " ".join(_texts(page, '//div[contains(@class, "cabecera_popup")]//h1 | //div[@id="titulo_cabecera"]/h2'))
    numero_boletin = _search(_BULLETIN_NUMBER, header)
    seccion_normalizada = _search(_SECTION_CODE, header)
    if numero_boletin is None or seccion_normalizada is None:
        raise ScrapperError(f"The header of BOCM document {cve} has no bulletin number or section")
    seccion = seccion_normalizada.split(".")[0]

    # Headings of the body: the section name, then the subsection and the department (section I), the council and the
    # subject (section III) or the issuing body (other sections), and optionally lower bodies
    headings: list[str | None] = [*_texts(page, '(//div[@id="cabeceras"])[last()]/p'), None, None, None]
    subseccion = apartado = None
    if seccion == "1":
        subseccion, departamento = headings[1], headings[2]
    elif seccion == "3":
        departamento, apartado = headings[1], headings[2]
    else:
        departamento = headings[1]

    titulo = " ".join(_texts(page, '//div[@id="entradilla"]')) or _attribute(
        page, '//meta[@name="description"]/@content'
    )
    rango = _RANGO.match(titulo or "")
    # The header also links the ELI, the consolidated text, the XML and the JSON-LD of the document
    url_pdf = _attribute(
        page, '//div[@id="titulo_cabecera"]//a[contains(translate(@href, "PDF", "pdf"), ".pdf")]/@href'
    )
    return BOCMMetadataDocument(
        text=text,
        identificador=cve,
        titulo=titulo,
        departamento=departamento,
        rango=rango.group(0) if rango else None,
        numero_boletin=numero_boletin,
        paginas=_search(_PAGES, header),
        seccion_normalizada=seccion_normalizada,
        seccion=seccion,
        subseccion=subseccion,
        apartado=apartado,
        fecha_publicacion=datetime.strptime(cve.split("-")[1], "%Y%m%d").date().isoformat(),
        url_pdf=urljoin(BOCM_URL, url_pdf) if url_pdf else None,
        url_html=_attribute(page, '//meta[@property="og:url"]/@content') or f"{BOCM_URL}/{cve.lower()}",
    )


class BOCMScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOCMMetadataDocument]:
        logger = lg.getLogger(self.download_day.__name__)
        summary_url = self._find_summary_url(day)
        if summary_url is None:
            logger.info("No BOCM bulletin for %s", day)
            return []
        response = self._session.get(summary_url)
        response.raise_for_status()
        urls = parse_summary(response.content)
        logger.info("BOCM of %s (%s) has %s documents in the indexed sections", day, summary_url, len(urls))

        docs = []
        for url in urls:
            try:
                document_response = self._session.get(url)
                document_response.raise_for_status()
                docs.append(parse_document(document_response.content))
            except Exception:
                self._record_failure(url)
        return docs

    def _find_summary_url(self, day: date) -> str | None:
        """URL of the complete summary of the bulletin of a day, or None if no bulletin was published that day."""
        # The day search redirects to the page of the bulletin, or stays on the search page if there is none
        response = self._session.post(SEARCH_DAY_URL, data={"field_date[date]": day.strftime("%d/%m/%Y")})
        response.raise_for_status()
        match = _BULLETIN_URL.search(urlsplit(response.url).path)
        if match is not None:
            return SUMMARY_URL.format(date=match.group(1), number=match.group(2))
        if urlsplit(response.url).path == urlsplit(SEARCH_DAY_URL).path:
            return None
        raise ScrapperError(f"Unexpected result of the BOCM day search: {response.url}")
