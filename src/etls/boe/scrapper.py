import logging as lg
import tempfile
import typing as tp
from datetime import date, datetime

import requests
from bs4 import BeautifulSoup
from lxml import etree
from requests.exceptions import RequestException

from src.etls.boe.metadata import BOEMetadataDocument, BOEMetadataReferencia
from src.etls.common.scrapper import BaseScrapper
from src.etls.common.utils import xml_to_text
from src.etls.utils import create_retry_session
from src.initialize import initialize_logging

initialize_logging()


def _extract_metadata(soup) -> tp.Dict:
    metadata_dict = {}

    # Metadatos
    if identificador := soup.documento.metadatos.identificador:
        metadata_dict["identificador"] = identificador.get_text()

    if diario := soup.documento.metadatos.diario:
        metadata_dict["diario"] = diario.get_text()

    if numero_oficial := soup.documento.metadatos.numero_oficial:
        metadata_dict["numero_oficial"] = numero_oficial.get_text()

    if departamento := soup.documento.metadatos.departamento:
        metadata_dict["departamento"] = departamento.get_text()

    if rango := soup.documento.metadatos.rango:
        metadata_dict["rango"] = rango.get_text()

    if titulo := soup.documento.metadatos.titulo:
        metadata_dict["titulo"] = titulo.get_text()

    if url_pdf := soup.documento.metadatos.url_pdf:
        metadata_dict["url_pdf"] = url_pdf.get_text()

    if origen_legislativo := soup.documento.metadatos.origen_legislativo:
        metadata_dict["origen_legislativo"] = origen_legislativo.get_text()

    if fecha_publicacion := soup.documento.metadatos.fecha_publicacion:
        metadata_dict["fecha_publicacion"] = fecha_publicacion.get_text()

    if fecha_disposicion := soup.documento.metadatos.fecha_disposicion:
        metadata_dict["fecha_disposicion"] = fecha_disposicion.get_text()

    metadata_dict["anio"] = datetime.strptime(fecha_publicacion.get_text(), "%Y%m%d").strftime("%Y")

    metadata_dict["mes"] = datetime.strptime(fecha_publicacion.get_text(), "%Y%m%d").strftime("%m")

    metadata_dict["dia"] = datetime.strptime(fecha_publicacion.get_text(), "%Y%m%d").strftime("%d")

    # Analisis
    if observaciones := soup.documento.analisis.observaciones:
        metadata_dict["observaciones"] = observaciones.get_text()

    if ambito_geografico := soup.documento.analisis.ambito_geografico:
        metadata_dict["ambito_geografico"] = ambito_geografico.get_text()

    if modalidad := soup.documento.analisis.modalidad:
        metadata_dict["modalidad"] = modalidad.get_text()

    if tipo := soup.documento.analisis.tipo:
        metadata_dict["tipo"] = tipo.get_text()

    metadata_dict["materias"] = [
        materia.get_text() for materia in soup.select("documento > analisis > materias > materia")
    ]
    metadata_dict["alertas"] = [alerta.get_text() for alerta in soup.select("documento > analisis > alertas > alerta")]
    metadata_dict["notas"] = [nota.get_text() for nota in soup.select("documento > analisis > notas > nota")]
    metadata_dict["ref_posteriores"] = [
        BOEMetadataReferencia(
            id=ref["referencia"],
            palabra=ref.palabra.get_text(),
            texto=ref.texto.get_text(),
        )
        for ref in soup.select("documento > analisis > referencias > posteriores > posterior")
    ]
    metadata_dict["ref_anteriores"] = [
        BOEMetadataReferencia(
            id=ref["referencia"],
            palabra=ref.palabra.get_text(),
            texto=ref.texto.get_text(),
        )
        for ref in soup.select("documento > analisis > referencias > anteriores > anterior")
    ]
    return metadata_dict


def _list_links_day(url: str, session: requests.Session) -> tp.List[str]:
    """Get a list of links in a BOE url day filtering by Seccion 1 and Seccion T.

    :param url: day summary URL from the BOE open data API.
    :return: list of id documents to explore (links)
    """
    logger = lg.getLogger(_list_links_day.__name__)
    logger.info("Scrapping day: %s", url)
    response = session.get(url, headers={"Accept": "application/xml"}, timeout=10)
    if response.status_code == 404:
        logger.info("No BOE bulletin for %s (HTTP 404)", url)
        return []
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "xml")
    id_links = [
        identifier.get_text(strip=True)
        for section in soup.find_all("seccion", codigo=["1", "T"])
        for identifier in section.find_all("identificador")
    ]
    logger.info("Scrapped day successfully %s (%s BOE documents)", url, len(id_links))
    return id_links


class BOEScrapper(BaseScrapper):
    def __init__(self):
        self._session = create_retry_session(retries=5)

    def download_day(self, day: date) -> tp.List[BOEMetadataDocument]:
        """Download all the documents for a specific date."""
        logger = lg.getLogger(self.download_day.__name__)
        logger.info("Downloading BOE content for day %s", day)
        day_str = day.strftime("%Y%m%d")
        day_url = f"https://www.boe.es/datosabiertos/api/boe/sumario/{day_str}"
        metadata_documents = []
        id_links = _list_links_day(day_url, self._session)
        for id_link in id_links:
            url_document = f"https://www.boe.es/diario_boe/xml.php?id={id_link}"
            try:
                metadata_doc = self.download_document(url_document)
                metadata_documents.append(metadata_doc)
            except (RequestException, AttributeError) as error:
                logger.error("Not scrapped document %s on day %s: %s", url_document, day_url, error)
        logger.info("Downloaded BOE content for day %s", day)
        return metadata_documents

    def download_document(self, url: str) -> BOEMetadataDocument:
        """Get text and metadata from a BOE xml url document.

        :param url: document url link. Examples:
            * https://www.boe.es/diario_boe/xml.php?id=BOE-A-2022-14630
            * https://www.boe.es/diario_boe/xml.php?id=BOE-A-2023-12203
        :return: document with metadata and filepath with text content
        """
        logger = lg.getLogger(self.download_document.__name__)
        logger.info("Scrapping document: %s", url)
        response = self._session.get(url, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "xml")
        with tempfile.NamedTemporaryFile("w", delete=False) as fn:
            text = xml_to_text(etree.fromstring(response.content).find("texto"))
            fn.write(text)
        metadata_doc = BOEMetadataDocument(filepath=fn.name, **_extract_metadata(soup))
        logger.info("Scrapped document successfully %s", url)
        return metadata_doc
