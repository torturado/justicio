import pytest

from src.etls.bocm.scrapper import parse_document, parse_summary
from src.etls.common.utils import ScrapperError

SUMMARY = """<html><body>
<div id="secciones-seccion_1">
  <div class="view-grouping">
    <div class="view-grouping-header"><h3>Sección: <strong>I. COMUNIDAD DE MADRID</strong> | A) Disposiciones Generales</h3></div>
    <div class="view-grouping-content">
      <article about="/bocm-20261016-1">
        <a href="https://www.bocm.es/boletin/CM_Orden_BOCM/2026/10/16/BOCM-20261016-1.PDF">pdf</a>
        <a href="/bocm-20261016-1" rel="nofollow, noindex">HTML</a>
        <a href="/boletin/CM_Orden_BOCM/2026/10/16/BOCM-20261016-1.xml">XML</a>
      </article>
    </div>
  </div>
  <div class="view-grouping">
    <div class="view-grouping-header"><h3>Sección: <strong>I. COMUNIDAD DE MADRID</strong> | D) Anuncios</h3></div>
    <div class="view-grouping-content"><a href="/bocm-20261016-2">HTML</a></div>
  </div>
</div>
<div id="secciones-seccion_2"><div class="view-content"><a href="/bocm-20261016-40">HTML</a></div></div>
<div id="secciones-seccion_3">
  <div class="view-content">
    <h3>Sección: <strong>III. ADMINISTRACIÓN LOCAL AYUNTAMIENTOS</strong></h3>
    <a href="/bocm-20261016-51">HTML</a>
    <a href="/bocm-20261016-52">HTML</a>
  </div>
</div>
<div id="secciones-seccion_4"><div class="view-content"><a href="/bocm-20261016-62">HTML</a></div></div>
<div id="secciones-seccion_5"><div class="view-content"><a href="/bocm-20261016-70">HTML</a></div></div>
</body></html>""".encode()


def _document_page(cve: str, section: str, headings: list[str], entradilla: str | None) -> bytes:
    """A document page with the markup of https://www.bocm.es/bocm-20261016-1"""
    paragraphs = "".join(f'<p style="text-align:center;"><b>{heading}</b><br/></p>' for heading in headings)
    title = f'<div id="entradilla"><i>{entradilla}</i></div>' if entradilla else ""
    return f"""<html><head>
<meta charset="utf-8" />
<meta name="TituloGSA" content="{cve}" />
<meta name="description" content="{" ".join(headings)} 1Texto truncado de la descripción" />
<meta property="og:url" content="https://www.bocm.es/{cve.lower()}" />
</head><body><div id="main"><div id="cuerpopopup">
<div class="cabecera_popup">
  <div class="titulo_cabpopup"><h1 class="tit10roj"><strong>Boletín Oficial de la Comunidad de Madrid - Núm 247</strong></h1></div>
  <div id="titulo_cabecera">
    <h2>Sección {section}: <strong>{headings[0]}</strong></h2><br/>
    <h2><b>Código de Verificación Electrónica (CVE): {cve}</b></h2>
    <h2><b>Páginas: 32</b></h2>
    <div class="eli-data">
      <a href="https://www.bocm.es/eli/es-md/d/2026/10/14/84">https://www.bocm.es/eli/es-md/d/2026/10/14/84</a>
      <div class="pdf-link icon-link"><a href="https://www.bocm.es/boletin/CM_Orden_BOCM/2026/10/16/{cve}.PDF">PDF</a></div>
      <div class="xml-link icon-link"><a href="/boletin/CM_Orden_BOCM/2026/10/16/{cve}.xml">XML</a></div>
    </div>
  </div>
</div>
<div class="txtPopup"><div id="cabeceras"><div class="field field-name-body"><div class="field-items">
<div class="field-item even" property="content:encoded">
  <p></p>
  <div id="cabeceras">{paragraphs}</div>
  <div id="numIns"><big>1</big></div>
  {title}
  <div id="cuerpo">
    <p><parrafo>Primer párrafo del texto.</parrafo></p>
    <table><tr><td>Concepto</td><td>Importe</td></tr></table>
  </div>
</div></div></div></div></div>
<div id="piepaginapopup">https://www.bocm.es Código de Verificación Electrónica (CVE): {cve}</div>
</div></div></body></html>""".encode()


def test_summary_lists_documents_of_sections_one_a_three_and_four():
    assert parse_summary(SUMMARY) == [
        "https://www.bocm.es/bocm-20261016-1",
        "https://www.bocm.es/bocm-20261016-51",
        "https://www.bocm.es/bocm-20261016-52",
        "https://www.bocm.es/bocm-20261016-62",
    ]


def test_summary_without_sections_is_an_error():
    with pytest.raises(ScrapperError):
        parse_summary(b"<html><body><p>Mantenimiento</p></body></html>")


def test_section_one_document_has_its_disposition_title_and_bulletin_number():
    title = "DECRETO 84/2026, de 14 de octubre, del Consejo de Gobierno, por el que se aprueba el Reglamento."
    headings = ["I. COMUNIDAD DE MADRID", "A) Disposiciones Generales", "CONSEJERÍA DE ECONOMÍA, HACIENDA Y EMPLEO"]
    doc = parse_document(_document_page("BOCM-20261016-1", "1.1.104.1", headings, title))

    assert doc.text == "\n".join([*headings, "1", title, "Primer párrafo del texto.", "Concepto | Importe"])
    metadata = doc.model_dump()
    assert metadata["source_name"] == "BOCM"
    assert metadata["identificador"] == "BOCM-20261016-1"
    assert metadata["titulo"] == title
    assert metadata["rango"] == "DECRETO"
    assert metadata["departamento"] == "CONSEJERÍA DE ECONOMÍA, HACIENDA Y EMPLEO"
    assert metadata["subseccion"] == "A) Disposiciones Generales"
    assert metadata["apartado"] is None
    assert metadata["numero_boletin"] == "247"
    assert metadata["numero_oficial"] is None
    assert metadata["seccion_normalizada"] == "1.1.104.1"
    assert metadata["seccion"] == "1"
    assert metadata["paginas"] == "32"
    assert metadata["fecha_publicacion"] == "2026-10-16"
    assert metadata["url_pdf"] == "https://www.bocm.es/boletin/CM_Orden_BOCM/2026/10/16/BOCM-20261016-1.PDF"
    assert metadata["url_html"] == "https://www.bocm.es/bocm-20261016-1"


def test_section_three_document_has_council_as_department_and_subject():
    headings = ["III. ADMINISTRACIÓN LOCAL", "AYUNTAMIENTO DE ALCOBENDAS", "ORGANIZACIÓN Y FUNCIONAMIENTO"]
    doc = parse_document(
        _document_page(
            "BOCM-20261016-55", "3.10.20A", headings, "Alcobendas. Organización y funcionamiento. Reglamento"
        )
    )

    assert doc.titulo == "Alcobendas. Organización y funcionamiento. Reglamento"
    assert doc.departamento == "AYUNTAMIENTO DE ALCOBENDAS"
    assert doc.apartado == "ORGANIZACIÓN Y FUNCIONAMIENTO"
    assert doc.subseccion is None
    assert doc.rango is None
    assert doc.seccion == "3"


def test_document_without_title_falls_back_to_the_description():
    headings = ["IV. ADMINISTRACIÓN DE JUSTICIA", "TRIBUNAL SUPERIOR DE JUSTICIA DE MADRID"]
    doc = parse_document(_document_page("BOCM-20261020-62", "4.50.1.1", headings, None))

    assert doc.titulo == (
        "IV. ADMINISTRACIÓN DE JUSTICIA TRIBUNAL SUPERIOR DE JUSTICIA DE MADRID 1Texto truncado de la descripción"
    )
    assert doc.departamento == "TRIBUNAL SUPERIOR DE JUSTICIA DE MADRID"
    assert doc.rango is None
