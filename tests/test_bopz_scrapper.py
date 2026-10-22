import pytest
from bs4 import BeautifulSoup

from src.etls.bopz.scrapper import build_title, edict_id, extract_metadata, has_page, parse_edict, parse_listing
from src.etls.common.utils import ScrapperError

PDF_HREF = r"./UploadServlet?ruta=Boletines\2018\298\Edictos\bop_10885_2018.pdf"
PDF_URL = r"https://bop.dpz.es/BOPZ/UploadServlet?ruta=Boletines\2018\298\Edictos\bop_10885_2018.pdf"

PUBLISHED_ROW = f"""
<div class="row listadoEdictos">
  <div class="small-10 columns" onclick="javascript:abreVentanaDetalleEdicto('605267')">
    <p><span>Nº. Reg:</span> 10885/2018 <span>Fecha Pub:</span> 29/12/2018</p>
    <p><span>Publicador:</span> AYUNTAMIENTO DE ZARAGOZA</p>
    <p><span>Materia:</span> Empleo Público</p>
  </div>
  <div class="small-2 columns"><a class="adjunto" href="{PDF_HREF}">PDF</a></div>
</div>
"""

ROW_WITHOUT_ONCLICK = f"""
<div class="row listadoEdictos">
  <div class="small-10 columns"><p><span>Nº. Reg:</span> 10886/2018</p></div>
  <div class="small-2 columns"><a class="adjunto" href="{PDF_HREF.replace("10885", "10886")}">PDF</a></div>
</div>
"""

ROW_WITHOUT_ANCHOR = """
<div class="row listadoEdictos">
  <div class="small-10 columns" onclick="javascript:abreVentanaDetalleEdicto('605268')"><p>Sin adjunto</p></div>
</div>
"""

ROW_WITH_OTHER_ATTACHMENT = """
<div class="row listadoEdictos">
  <div class="small-10 columns" onclick="javascript:abreVentanaDetalleEdicto('605269')"></div>
  <a class="adjunto" href="./UploadServlet?ruta=Anexos\\2018\\anexo.docx">Anexo</a>
</div>
"""

EDICT_PAGE = """
<html><head><title>Edicto: APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018</title></head>
<body>
  <h3>APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018</h3>
  <div class="row"><div class="medium-12 panel">
    <p><strong>SECCIÓN QUINTA</strong></p><p>El Gobierno de Zaragoza adoptó el siguiente acuerdo:</p>
  </div></div>
</body></html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _row(html: str):
    return _soup(html).find("div", class_="row listadoEdictos")


def test_listing_keeps_the_rows_of_edicts_published_in_a_bulletin():
    listing = _soup(PUBLISHED_ROW + ROW_WITHOUT_ONCLICK + ROW_WITHOUT_ANCHOR + ROW_WITH_OTHER_ATTACHMENT)

    rows = parse_listing(listing)

    assert [url_pdf for _, url_pdf in rows] == [PDF_URL, PDF_URL.replace("10885", "10886")]


def test_edict_id_comes_from_the_onclick_handler():
    assert edict_id(_row(PUBLISHED_ROW)) == "605267"
    assert edict_id(_row(ROW_WITHOUT_ONCLICK)) is None


def test_listing_row_metadata():
    assert extract_metadata(_row(PUBLISHED_ROW)) == {
        "numero_registro": "10885/2018",
        "fecha_publicacion": "2018-12-29",
        "departamento": "AYUNTAMIENTO DE ZARAGOZA",
        "materia": ["Empleo Público"],
    }


def test_listing_row_metadata_skips_missing_labels():
    html = '<div class="row listadoEdictos"><span><b>Nº.</b> Reg:</span><span>Publicador:</span></div>'

    assert extract_metadata(_row(html)) == {}


def test_pager_links_the_following_result_pages():
    # Pager of the live search results, which numbers pages from 0
    pager = _soup(
        '<ul class="pagination">'
        '<li class="current"><a class="pag" href="#" onclick="javascript:setPagina(0)">1</a></li>'
        '<li class="unavailable"><a class="pag" href="#" onclick="javascript:setPagina(1)">2</a></li>'
        "</ul>"
    )

    assert has_page(pager, 1)
    assert not has_page(pager, 2)
    assert not has_page(_soup(PUBLISHED_ROW), 1)


def test_edict_page_text_and_summary():
    text, summary = parse_edict(_soup(EDICT_PAGE))

    assert text == "SECCIÓN QUINTA\nEl Gobierno de Zaragoza adoptó el siguiente acuerdo:"
    assert summary == "APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018"


def test_edict_page_without_text_is_an_error():
    with pytest.raises(ScrapperError):
        parse_edict(_soup("<html><head><title>Edicto</title></head><body></body></html>"))


def test_title_describes_the_edict_instead_of_its_registry_number():
    metadata = extract_metadata(_row(PUBLISHED_ROW))

    assert build_title("APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018", metadata) == (
        "APROBACIÓN DE LA OFERTA DE EMPLEO PÚBLICO PARA EL AÑO 2018 (AYUNTAMIENTO DE ZARAGOZA, Empleo Público)"
    )
    assert build_title(None, metadata) == "AYUNTAMIENTO DE ZARAGOZA, Empleo Público"
