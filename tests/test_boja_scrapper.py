from datetime import date

import pytest
from bs4 import BeautifulSoup

from src.etls.boja.scrapper import is_extraordinary_day_url, parse_calendar, parse_disposition
from src.etls.common.utils import ScrapperError

CALENDAR_URL = "https://www.juntadeandalucia.es/eboja/2024"
BULLETIN_URL = "https://www.juntadeandalucia.es/eboja/2024/7/index.html"
DISPOSITION_URL = "https://www.juntadeandalucia.es/boja/2024/7/1"
DAY = date(2024, 1, 10)

CALENDAR = """
<table class="calendario_tabla" summary="Boletines del mes de enero de 2024">
  <tr><td><a href="https://www.juntadeandalucia.es/eboja/2024/1/index.html">2</a></td>
      <td><a href="https://www.juntadeandalucia.es/eboja/2024/7/index.html">10</a></td>
      <td><a href="https://www.juntadeandalucia.es/eboja/20240111.html">11</a></td>
      <td><a href="https://www.juntadeandalucia.es/eboja/2024.html">Siguiente</a></td></tr>
</table>
<table class="calendario_tabla" summary="Boletines del mes de febrero de 2024">
  <tr><td><a href="https://www.juntadeandalucia.es/eboja/2024/23/index.html">10</a></td></tr>
</table>
"""

DISPOSITION = """
<div id="cuerpo" class="grid_11 contenidos_nivel3 boja_disposicion">
  <div class="punteado_izquierda cabecera_detalle_disposicion">
    <h2>1. Disposiciones generales</h2>
    <h5>Consejería de la Presidencia, Interior, Diálogo Social y Simplificación Administrativa</h5>
    <div class="item"><p>Corrección de errores de la Resolución de 26 de diciembre de 2023.</p></div>
  </div>
  <div class="alerta"><p>Atención: El texto que se muestra a continuación ha sido extraído de los ficheros.</p></div>
  <p>Advertido error en la Resolución,
     se procede a su subsanación:</p>
  <p>&nbsp;</p>
  <p>En la página 19622/3, donde dice:</p>
</div>
<a class="item_pdf_disposicion" href="BOJA24-007-00001-118-01_00295096.pdf">PDF</a>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.juntadeandalucia.es/eboja/20240111.html", True),
        ("https://www.juntadeandalucia.es/eboja/2024/7/index.html", False),
        ("https://www.juntadeandalucia.es/eboja/2024/501/", False),
    ],
)
def test_extraordinary_day_url_is_a_day_page(url, expected):
    assert is_extraordinary_day_url(url) is expected


def test_calendar_links_the_bulletin_of_the_day_in_its_month():
    soup = _soup(CALENDAR)

    assert parse_calendar(soup, CALENDAR_URL, DAY) == BULLETIN_URL
    assert parse_calendar(soup, CALENDAR_URL, date(2024, 2, 10)) == (
        "https://www.juntadeandalucia.es/eboja/2024/23/index.html"
    )
    assert parse_calendar(soup, CALENDAR_URL, date(2024, 1, 3)) is None


def test_calendar_without_month_tables_is_an_error():
    with pytest.raises(ScrapperError):
        parse_calendar(_soup("<p>Mantenimiento</p>"), CALENDAR_URL, DAY)


def test_disposition_text_and_metadata():
    doc = parse_disposition(_soup(DISPOSITION), DISPOSITION_URL, BULLETIN_URL, DAY)

    assert doc is not None
    assert doc.text == (
        "Corrección de errores de la Resolución de 26 de diciembre de 2023.\n"
        "Advertido error en la Resolución, se procede a su subsanación:\n"
        "En la página 19622/3, donde dice:"
    )
    metadata = doc.model_dump()
    assert metadata["source_name"] == "BOJA"
    assert metadata["identificador"] == "2024/7/1"
    assert metadata["titulo"] == "Corrección de errores de la Resolución de 26 de diciembre de 2023."
    assert metadata["departamento"] == (
        "Consejería de la Presidencia, Interior, Diálogo Social y Simplificación Administrativa"
    )
    assert metadata["tipologia"] == "Disposiciones generales"
    assert metadata["fecha_publicacion"] == "2024-01-10"
    assert metadata["fecha_disposicion"] is None
    assert metadata["url_pdf"] == "https://www.juntadeandalucia.es/boja/2024/7/BOJA24-007-00001-118-01_00295096.pdf"
    assert metadata["url_html"] == DISPOSITION_URL
    assert metadata["url_boletin"] == BULLETIN_URL


def test_disposition_header_fallbacks():
    html = """
    <div id="cuerpo" class="grid_11 contenidos_nivel3 boja_disposicion">
      <div class="punteado_izquierda cabecera_detalle_disposicion">
        <h3>Universidades</h3>
        <h4>Resolución de 2 de enero de 2024, de la Universidad de Sevilla.</h4>
      </div>
      <p>Texto.</p>
    </div>
    """
    doc = parse_disposition(_soup(html), DISPOSITION_URL, BULLETIN_URL, DAY)

    assert doc is not None
    assert doc.departamento == "Universidades"
    assert doc.titulo == "Resolución de 2 de enero de 2024, de la Universidad de Sevilla."
    assert doc.tipologia is None
    assert doc.url_pdf is None


def test_restricted_disposition_is_skipped():
    html = '<h1 class="title">Texto de acceso restringido</h1>'

    assert parse_disposition(_soup(html), DISPOSITION_URL, BULLETIN_URL, DAY) is None


def test_disposition_without_body_is_an_error():
    with pytest.raises(ScrapperError):
        parse_disposition(_soup("<h1>Página no encontrada</h1>"), DISPOSITION_URL, BULLETIN_URL, DAY)
