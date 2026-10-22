from datetime import date

import pytest

from src.etls.bopv.scrapper import parse_calendar, parse_document, parse_summary
from src.etls.common.utils import ScrapperError

CALENDAR = """<html><head><script type='text/javascript'>
var rutaRaiz = cogerRutaRaiz();
var diasHabilitados = ['20251128','20251201'];
var enlaces = [['s25_0230.shtml'],['s25_0231.shtml','s25_0232.shtml']];
var asuntos = ['2505100','2505120'];
</script></head><body><div id='datepicker'></div></body></html>"""

SUMMARY_URL = "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/s26_0199.shtml"
# The heading of a section is only filled in before its first document
SUMMARY = """<html><head><meta charset="utf-8"></head><body><div class="colCentral">
<h2 class="tituGeneral">Sumario n.º <span>199</span>, lunes 19 de octubre de 2026</h2>
<h4 class="BOPVSumarioSeccion">DISPOSICIONES GENERALES</h4>
<h5 class="BOPVSumarioOrganismo">DEPARTAMENTO DE SALUD</h5>
<div class="txtBloque"><p class="BOPVSumarioTitulo"><a href="2604300a.shtml">DECRETO 170/2026</a></p></div>
<h4 class="BOPVSumarioSeccion">AUTORIDADES Y PERSONAL</h4>
<div class="txtBloque"><p class="BOPVSumarioTitulo"><a href="2604301a.shtml">DECRETO 171/2026</a></p></div>
<h4 class="BOPVSumarioSeccion">OTRAS DISPOSICIONES</h4>
<div class="txtBloque"><p class="BOPVSumarioTitulo"><a href="2604302a.shtml">ORDEN de 1 de octubre</a></p></div>
<h4 class="BOPVSumarioSeccion"></h4>
<div class="txtBloque"><p class="BOPVSumarioTitulo"><a href="2604303a.shtml">ORDEN de 2 de octubre</a></p></div>
<h4 class="BOPVSumarioSeccion">ANUNCIOS</h4>
<div class="txtBloque"><p class="BOPVSumarioTitulo"><a href="2604304a.shtml">ANUNCIO</a></p></div>
</div></body></html>""".encode()

DOCUMENT_URL = "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604178a.shtml"
DOCUMENT = """<html><head><meta charset="utf-8"></head><body><div class="colCentralinterior">
<h2 class="tituGeneral">N.º <span>194</span>, viernes 9 de octubre de 2026</h2>
<ul class="listaFormatos">Otros formatos:
  <li class="formatoPdf"><a href="2604178a.pdf">PDF <span>(96 KB - 1 Pág.)</span></a></li>
</ul>
<p class="otrosFormatos">El contenido de los otros formatos se ha obtenido del PDF oficial</p>
<h4 class="BOPVSeccion">OTRAS DISPOSICIONES</h4>
<h5 class="BOPVOrganismo">DEPARTAMENTO DE ECONOMÍA, TRABAJO Y EMPLEO<br/></h5>
<h5 class="BOPVOrden">4178</h5>
<p class="BOPVTitulo">DECRETO 163/2026, de 6 de octubre, por el que se nombra directora de Inclusión.</p>
<p class="BOPVDetalle">De conformidad con lo dispuesto en la Ley 7/1981,
  de 30 de junio, de Gobierno,</p>
<h6 class="BOPVClave">DISPONGO:</h6>
<p class="BOPVDetalle">Artículo único.– Nombrar directora de <b>Inclusión</b>.</p>
<p class="BOPVFirmaLugFec">Dado en Vitoria-Gasteiz, a 6 de octubre de 2026.</p>
<p class="BOPVFirmaNombre">IMANOL PRADALES GIL.</p>
<span class="bopvanalisisdoc"><a href="#">Análisis documental</a></span>
</div></body></html>"""


def test_calendar_maps_each_day_to_its_summaries():
    assert parse_calendar(CALENDAR) == {
        "20251128": ["s25_0230.shtml"],
        "20251201": ["s25_0231.shtml", "s25_0232.shtml"],
    }


def test_calendar_code_is_rejected_without_being_executed(tmp_path):
    marker = tmp_path / "executed"
    html = (
        f"var diasHabilitados = [__import__('pathlib').Path({str(marker)!r}).touch()];"
        "var enlaces = [['s26_0001.shtml']];"
    )

    with pytest.raises(ScrapperError):
        parse_calendar(html)
    assert not marker.exists()


@pytest.mark.parametrize("link", ["../../otro.shtml", "https://example.com/s26_0001.shtml", "s26_0001.pdf"])
def test_calendar_rejects_summaries_outside_the_month(link):
    with pytest.raises(ScrapperError):
        parse_calendar(f"var diasHabilitados = ['20261001']; var enlaces = [[{link!r}]];")


def test_calendar_without_arrays_is_an_error():
    with pytest.raises(ScrapperError):
        parse_calendar("<html><body><div id='datepicker'></div></body></html>")


def test_summary_lists_documents_of_the_disposition_sections():
    assert parse_summary(SUMMARY, SUMMARY_URL) == [
        "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604300a.shtml",
        "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604302a.shtml",
        "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604303a.shtml",
    ]


def test_summary_without_documents_is_an_error():
    with pytest.raises(ScrapperError):
        parse_summary(b"<html><body><div class='colCentral'></div></body></html>", SUMMARY_URL)


def test_document_text_follows_the_page_order():
    doc = parse_document(DOCUMENT.encode(), DOCUMENT_URL, SUMMARY_URL, date(2026, 10, 9))

    assert doc.text == "\n".join(
        [
            "DECRETO 163/2026, de 6 de octubre, por el que se nombra directora de Inclusión.",
            "De conformidad con lo dispuesto en la Ley 7/1981, de 30 de junio, de Gobierno,",
            "DISPONGO:",
            "Artículo único.– Nombrar directora de Inclusión.",
            "Dado en Vitoria-Gasteiz, a 6 de octubre de 2026.",
            "IMANOL PRADALES GIL.",
        ]
    )
    metadata = doc.model_dump()
    assert metadata["source_name"] == "BOPV"
    assert metadata["identificador"] == "2026/10/2604178a"
    assert metadata["titulo"] == "DECRETO 163/2026, de 6 de octubre, por el que se nombra directora de Inclusión."
    assert metadata["rango"] == "DECRETO"
    assert metadata["departamento"] == "DEPARTAMENTO DE ECONOMÍA, TRABAJO Y EMPLEO"
    assert metadata["seccion"] == "OTRAS DISPOSICIONES"
    assert metadata["fecha_publicacion"] == "2026-10-09"
    assert metadata["fecha_disposicion"] is None
    assert metadata["url_pdf"] == "https://www.euskadi.eus/web01-bopv/es/bopv2/datos/2026/10/2604178a.pdf"
    assert metadata["url_html"] == DOCUMENT_URL
    assert metadata["url_boletin"] == SUMMARY_URL


def test_document_without_pdf_link_has_no_pdf_url():
    html = DOCUMENT.replace('<li class="formatoPdf"><a href="2604178a.pdf">', '<li class="formatoPdf"><a>')

    doc = parse_document(html.encode(), DOCUMENT_URL, SUMMARY_URL, date(2026, 10, 9))

    assert doc.url_pdf is None
