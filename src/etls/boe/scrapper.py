import logging as lg
from datetime import date, datetime

from lxml import etree

from src.etls.boe.metadata import BOEMetadataDocument, BOEMetadataReferencia
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import ScrapperError, xml_element_text, xml_to_text

SUMMARY_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{day}"
DOCUMENT_URL = "https://www.boe.es/diario_boe/xml.php?id={identificador}"
HTML_URL = "https://www.boe.es/diario_boe/txt.php?id={identificador}"
# Sections I (general provisions) and T (Constitutional Court rulings)
SECTIONS = ("1", "T")


def _iso_date(value: str) -> str | None:
    return datetime.strptime(value, "%Y%m%d").date().isoformat() if value else None


def _references(analisis: etree._Element | None, path: str) -> list[BOEMetadataReferencia]:
    if analisis is None:
        return []
    return [
        BOEMetadataReferencia(
            id=reference.get("referencia", ""),
            palabra=xml_element_text(reference.find("palabra")),
            texto=xml_element_text(reference.find("texto")),
        )
        for reference in analisis.iterfind(path)
    ]


def parse_document(content: bytes) -> BOEMetadataDocument:
    """Text and metadata of a document from its BOE XML (https://www.boe.es/diario_boe/xml.php?id=...)."""
    root = etree.fromstring(content)
    metadatos = root.find("metadatos")
    texto = root.find("texto")
    if metadatos is None or texto is None:
        raise ScrapperError("The BOE document has no metadatos or texto element")

    def field(name: str) -> str:
        return xml_element_text(metadatos.find(name))

    analisis = root.find("analisis")

    def analysis_field(name: str) -> str:
        return xml_element_text(analisis.find(name)) if analisis is not None else ""

    def analysis_list(path: str) -> list[str]:
        return [xml_element_text(item) for item in analisis.iterfind(path)] if analisis is not None else []

    identificador = field("identificador")
    return BOEMetadataDocument(
        text=xml_to_text(texto),
        identificador=identificador,
        titulo=field("titulo"),
        departamento=field("departamento") or None,
        rango=field("rango") or None,
        numero_oficial=field("numero_oficial") or None,
        fecha_publicacion=_iso_date(field("fecha_publicacion")),
        fecha_disposicion=_iso_date(field("fecha_disposicion")),
        url_pdf=field("url_pdf") or None,
        url_html=HTML_URL.format(identificador=identificador),
        url_eli=field("url_eli") or None,
        diario=field("diario"),
        origen_legislativo=field("origen_legislativo"),
        observaciones=analysis_field("observaciones"),
        ambito_geografico=analysis_field("ambito_geografico"),
        modalidad=analysis_field("modalidad"),
        tipo=analysis_field("tipo"),
        materias=analysis_list("materias/materia"),
        alertas=analysis_list("alertas/alerta"),
        notas=analysis_list("notas/nota"),
        ref_posteriores=_references(analisis, "referencias/posteriores/posterior"),
        ref_anteriores=_references(analisis, "referencias/anteriores/anterior"),
    )


def parse_summary(content: bytes) -> list[str]:
    """Identifiers of the documents of the indexed sections in a day summary of the BOE open data API."""
    root = etree.fromstring(content)
    return [
        xml_element_text(identifier)
        for section in root.iterfind(".//seccion")
        if section.get("codigo") in SECTIONS
        for identifier in section.iterfind(".//item/identificador")
    ]


class BOEScrapper(BaseScrapper):
    def download_day(self, day: date) -> list[BOEMetadataDocument]:
        logger = lg.getLogger(self.download_day.__name__)
        summary_url = SUMMARY_URL.format(day=day.strftime("%Y%m%d"))
        response = self._session.get(summary_url, headers={"Accept": "application/xml"})
        if response.status_code == 404:
            logger.info("No BOE bulletin for %s (HTTP 404)", day)
            return []
        response.raise_for_status()
        identifiers = parse_summary(response.content)
        logger.info("BOE of %s has %s documents in sections %s", day, len(identifiers), SECTIONS)

        docs = []
        for identificador in identifiers:
            url = DOCUMENT_URL.format(identificador=identificador)
            try:
                document_response = self._session.get(url)
                document_response.raise_for_status()
                docs.append(parse_document(document_response.content))
            except Exception:
                self._record_failure(url)
        return docs
