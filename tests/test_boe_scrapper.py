from src.etls.boe.scrapper import parse_document, parse_summary

SUMMARY = b"""<?xml version="1.0" encoding="utf-8"?>
<response><data><sumario><diario numero="260">
  <seccion codigo="1" nombre="I. Disposiciones generales">
    <departamento codigo="1" nombre="JEFATURA DEL ESTADO"><epigrafe nombre="Leyes">
      <item><identificador>BOE-A-2026-1</identificador></item>
    </epigrafe></departamento>
  </seccion>
  <seccion codigo="2A" nombre="II. A. Nombramientos">
    <departamento codigo="2" nombre="MINISTERIO"><item><identificador>BOE-A-2026-2</identificador></item></departamento>
  </seccion>
  <seccion codigo="T" nombre="Tribunal Constitucional">
    <departamento codigo="3" nombre="TRIBUNAL CONSTITUCIONAL"><item><identificador>BOE-A-2026-3</identificador></item></departamento>
  </seccion>
</diario></sumario></data></response>"""

DOCUMENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<documento fecha_actualizacion="20261013112601">
  <metadatos>
    <identificador>BOE-A-2023-12203</identificador>
    <departamento codigo="7723">Jefatura del Estado</departamento>
    <rango codigo="1300">Ley</rango>
    <fecha_disposicion>20230524</fecha_disposicion>
    <numero_oficial>12/2023</numero_oficial>
    <titulo>Ley 12/2023, de 24 de mayo, por el derecho a la vivienda.</titulo>
    <fecha_publicacion>20230525</fecha_publicacion>
    <url_pdf>https://www.boe.es/boe/dias/2023/05/25/pdfs/BOE-A-2023-12203.pdf</url_pdf>
  </metadatos>
  <analisis>
    <materias><materia codigo="1">Vivienda</materia></materias>
    <referencias>
      <anteriores>
        <anterior referencia="BOE-A-2022-4972" orden="1"><palabra codigo="270">MODIFICA</palabra><texto>el art. 46</texto></anterior>
      </anteriores>
      <posteriores/>
    </referencias>
  </analisis>
  <texto>
    <p>FELIPE VI</p>
    <table><tr><td>Concepto</td><td>Importe</td></tr></table>
  </texto>
</documento>"""


def test_summary_lists_documents_of_sections_one_and_t():
    assert parse_summary(SUMMARY) == ["BOE-A-2026-1", "BOE-A-2026-3"]


def test_document_metadata_uses_canonical_fields_and_iso_dates():
    doc = parse_document(DOCUMENT)

    assert doc.text == "FELIPE VI\nConcepto | Importe"
    metadata = doc.model_dump()
    assert "text" not in metadata
    assert metadata["source_name"] == "BOE"
    assert metadata["numero_oficial"] == "12/2023"
    assert metadata["fecha_publicacion"] == "2023-05-25"
    assert metadata["fecha_disposicion"] == "2023-05-24"
    assert metadata["url_html"] == "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2023-12203"
    assert metadata["materias"] == ["Vivienda"]
    assert metadata["ref_anteriores"] == [{"id": "BOE-A-2022-4972", "palabra": "MODIFICA", "texto": "el art. 46"}]
