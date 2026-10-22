import logging as lg
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from src.etls.boja.metadata import BOJAMetadataDocument
from src.etls.common.http import get_soup
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, normalize_whitespace

BASE_URL = "https://www.juntadeandalucia.es"
MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
# Sections of a bulletin whose dispositions are downloaded
SECTION_PATTERN = re.compile(r"\b(Disposiciones Generales|Otras Disposiciones)\b", re.IGNORECASE)


def calendar_url(day: date) -> str:
    """Year calendar with a link to the bulletins of each publication day."""
    return f"{BASE_URL}/{'eboja' if day.year >= 2012 else 'boja'}/{day.year}"


def is_extraordinary_day_url(url: str) -> bool:
    """Whether a calendar link points to a page listing the several bulletins of a day (when an extraordinary
    bulletin was published), e.g. https://www.juntadeandalucia.es/eboja/20240111.html, instead of to a bulletin."""
    return re.match(r".*/\d{8}\.html$", url) is not None


def parse_calendar(soup: BeautifulSoup, url: str, day: date) -> str | None:
    """URL linked from the year calendar for a day, or None if no bulletin was published that day."""
    tables = soup.find_all("table", class_="calendario_tabla")
    if not tables:
        raise ScrapperError(f"The BOJA calendar {url} has no month tables")
    for table in tables:
        match = re.search(r"Boletines del mes de (\w+) de (\d{4})", table.get("summary", ""))
        if match is None or int(match.group(2)) != day.year or MONTHS.get(match.group(1).lower()) != day.month:
            continue
        for link in table.find_all("a", href=True):
            day_number = link.get_text(strip=True)
            if day_number.isdigit() and int(day_number) == day.day:
                return urljoin(url, link["href"])
    return None


def parse_day_bulletins(soup: BeautifulSoup, url: str) -> list[str]:
    """URLs of the bulletins (ordinary and extraordinary) listed in the page of a day."""
    return [
        urljoin(url, link["href"])
        for listing in soup.find_all("ul", class_="mt-4 pl-3")
        for link in listing.find_all("a", href=True)
    ]


def parse_bulletin_sections(soup: BeautifulSoup, url: str) -> list[str]:
    """URLs of the downloaded sections listed in the index of a bulletin."""
    listing = soup.find("ol", class_="listado_ordenado_boja raiz")
    if listing is None:
        listing = soup.find(["ol", "ul"], class_=["listado_ordenado_boja", "listado_ordenado"])
    if listing is None:
        raise ScrapperError(f"The BOJA bulletin index {url} has no section list")
    return [
        urljoin(url, link["href"]) for link in listing.find_all("a", href=True) if SECTION_PATTERN.search(link.text)
    ]


def parse_section_dispositions(soup: BeautifulSoup, url: str) -> list[str]:
    """URLs of the HTML version of the dispositions listed in a bulletin section."""
    links = soup.find_all("a", class_="item_html", title=re.compile("Versión HTML CVE"), href=True)
    links += soup.find_all("a", title="Ver disposición", href=True)
    return [urljoin(url, link["href"]) for link in links]


def _tag_text(tag: Tag | None) -> str | None:
    return (normalize_whitespace(tag.get_text()) or None) if tag is not None else None


def parse_disposition(soup: BeautifulSoup, url: str, url_boletin: str, day: date) -> BOJAMetadataDocument | None:
    """Text and metadata of a disposition page, e.g. https://www.juntadeandalucia.es/boja/2024/7/1.

    Return None if the text of the disposition is not public.
    """
    if soup.find("h1", class_="title", string="Texto de acceso restringido"):
        return None
    body = soup.find(id="cuerpo", class_="grid_11 contenidos_nivel3 boja_disposicion")
    header = soup.find(class_="punteado_izquierda cabecera_detalle_disposicion")
    if body is None or header is None:
        raise ScrapperError("The BOJA disposition page has no header or body")

    title_item = header.find("div", class_="item")
    title = title_item.p if title_item is not None and title_item.p is not None else header.find("h4")
    department = header.find("h5")
    if department is None:
        department = header.find("h3")
    section = _tag_text(header.find("h2"))
    pdf_link = soup.find("a", class_="item_pdf_disposicion", href=True)

    # One line per paragraph, without the notice about the origin of the text
    paragraphs = (normalize_whitespace(p.get_text()) for p in body.find_all("p") if p.parent.get("class") != ["alerta"])
    text = "\n".join(paragraph for paragraph in paragraphs if paragraph)
    if not text:
        raise ScrapperError("The BOJA disposition page has no text")

    return BOJAMetadataDocument(
        text=text,
        identificador="/".join(url.split("/")[-3:]),
        titulo=_tag_text(title),
        departamento=_tag_text(department),
        # Without the section number, e.g. "1. Disposiciones generales"
        tipologia=re.sub(r"^\d+\.\s*", "", section) if section else None,
        fecha_publicacion=day.isoformat(),
        url_pdf=urljoin(url, pdf_link["href"]) if pdf_link is not None else None,
        url_html=url,
        url_boletin=url_boletin,
    )


class BOJAScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOJAMetadataDocument]:
        """Download the general and other dispositions of the bulletins published on a day."""
        logger = lg.getLogger(self.download_day.__name__)
        logger.info("Downloading BOJA content for day %s", day)
        documents = []
        for url_boletin in self._list_bulletins(day):
            for url in self._list_dispositions(url_boletin):
                try:
                    document = self.download_document(url, url_boletin, day)
                except Exception:
                    self._record_failure(url)
                    continue
                if document is None:
                    logger.info("Skipping disposition with restricted access %s", url)
                else:
                    documents.append(document)
        logger.info("Downloaded %s BOJA documents for day %s", len(documents), day)
        return documents

    def download_document(self, url: str, url_boletin: str, day: date) -> BOJAMetadataDocument | None:
        """Download a disposition, or return None if its text is not public."""
        lg.getLogger(self.download_document.__name__).info("Scrapping document: %s", url)
        return parse_disposition(get_soup(self._session, url), url, url_boletin, day)

    def _list_bulletins(self, day: date) -> list[str]:
        """URLs of the bulletins published on a day: none, the ordinary one, or also extraordinary ones."""
        url = calendar_url(day)
        day_url = parse_calendar(get_soup(self._session, url), url, day)
        if day_url is None:
            lg.getLogger(self._list_bulletins.__name__).info("No BOJA bulletin published on %s", day)
            return []
        if not is_extraordinary_day_url(day_url):
            return [day_url]
        bulletin_urls = parse_day_bulletins(get_soup(self._session, day_url), day_url)
        if not bulletin_urls:
            raise ScrapperError(f"The BOJA day page {day_url} lists no bulletins")
        return bulletin_urls

    def _list_dispositions(self, url_boletin: str) -> list[str]:
        """URLs of the dispositions of the downloaded sections of a bulletin."""
        section_urls = parse_bulletin_sections(get_soup(self._session, url_boletin), url_boletin)
        urls: list[str] = []
        for section_url in section_urls:
            try:
                urls += parse_section_dispositions(get_soup(self._session, section_url), section_url)
            except Exception:
                self._record_failure(section_url)
        # A disposition can be linked more than once
        return list(dict.fromkeys(urls))
