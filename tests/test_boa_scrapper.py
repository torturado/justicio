import pytest

from src.etls.boa.scrapper import parse_document, parse_search_response
from src.etls.common.utils import ScrapperError

# Modelled on the responses of the BOA search engine: windows-1252, a raw control character (\x1c) and a backslash that
# is not a JSON escape inside strings, next to valid escapes
SEARCH_RESPONSE = (
    "[\n{\n"
    '"NOrden" : "1 de 1",\n'
    '"DOCN" : "007961653",\n'
    '"Titulo" : "ORDEN de \\"prueba\\" C:\\\\boa",\n'
    '"Texto" : "<br/> Importe: 22.734,00 \u20ac. <br/> V\u00e9ase HYPERLINK \\l &quot;3&quot; y la no\x1cficaci\u00f3n"\n'
    "}\n]\n"
).encode("cp1252")

NO_DOCUMENTS_RESPONSE = b"""<!DOCTYPE html><html lang="es-ES"><head><title>Bolet&iacute;n Oficial de Arag&oacute;n</title>
</head><body><span class="titulo">No se han recuperado documentos</span></body></html>"""

RESULT = {
    "NOrden": "3 de 24",
    "DOCN": "007938891",
    "FechaPublicacion": "20240207",
    "Numeroboletin": "27",
    "Seccion": "I. Disposiciones Generales",
    "Subseccion": "",
    "Fechadisposicion": "20240130",
    "Rango": "DECRETO",
    "Emisor": "DEPARTAMENTO DE SANIDAD",
    "Titulo": "DECRETO 14/2024, de 30 de enero, del Gobierno de Aragón, por el que se regula el transporte sanitario.",
    "Texto": "<br/> El artículo 43 de la &quot;Constitución&quot;. <br/> La Ley 14/1986,\n de 25 de abril. <br/>  ",
    "CodigoMateria": "",
    "UrlPdf": "`https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315467100404´"
    "`https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315468110404´              ",
    "UrlBCOM": "`https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315461040202´"
    "`https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315462050202´                ",
}


def test_search_response_keeps_euro_control_characters_and_literal_backslashes():
    [result] = parse_search_response(SEARCH_RESPONSE)

    assert result["DOCN"] == "007961653"
    assert result["Titulo"] == 'ORDEN de "prueba" C:\\boa'
    assert result["Texto"] == (
        "<br/> Importe: 22.734,00 €. <br/> Véase HYPERLINK \\l &quot;3&quot; y la no\x1cficación"
    )


def test_search_response_without_documents_is_empty():
    assert parse_search_response(NO_DOCUMENTS_RESPONSE) == []


def test_search_response_that_is_not_json_is_an_error():
    with pytest.raises(ScrapperError):
        parse_search_response(b"<html><body>Error interno</body></html>")


def test_document_has_text_by_paragraph_and_iso_dates():
    doc = parse_document(RESULT)

    assert doc.text == 'El artículo 43 de la "Constitución".\nLa Ley 14/1986, de 25 de abril.'
    metadata = doc.model_dump()
    assert "text" not in metadata
    assert metadata["source_name"] == "BOA"
    assert metadata["identificador"] == "007938891"
    assert metadata["titulo"] == RESULT["Titulo"]
    assert metadata["numero_boletin"] == "27"
    assert metadata["numero_oficial"] is None
    assert metadata["rango"] == "Decreto"
    assert metadata["departamento"] == "Departamento de sanidad"
    assert metadata["seccion"] == "I. Disposiciones Generales"
    assert metadata["subseccion"] is None
    assert metadata["codigo_materia"] is None
    assert metadata["fecha_publicacion"] == "2024-02-07"
    assert metadata["fecha_disposicion"] == "2024-01-30"
    assert metadata["url_pdf"] == "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315467100404"
    assert metadata["url_boletin"] == "https://www.boa.aragon.es/cgi-bin/EBOA/BRSCGI?CMD=VEROBJ&MLKOB=1315461040202"


@pytest.mark.parametrize("fecha_disposicion", ["", "2024013", "sin fecha"])
def test_document_without_valid_disposition_date_has_none(fecha_disposicion):
    doc = parse_document(RESULT | {"Fechadisposicion": fecha_disposicion, "Rango": ""})

    assert doc.fecha_disposicion is None
    assert doc.rango is None


def test_document_without_text_is_an_error():
    with pytest.raises(ScrapperError):
        parse_document(RESULT | {"Texto": "<br/> <br/>"})
