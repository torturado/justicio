from datetime import date

from lxml import etree

from src.etls.boe_consolidada.parser import build_chunks, catalogue_item_from_document, document_is_in_force
from src.etls.common.etl import create_text_splitter

TODAY = date(2026, 10, 21)

DOCUMENT = """<?xml version="1.0" encoding="utf-8"?>
<response><data>
  <metadatos>
    <identificador>BOE-A-2000-1</identificador>
    <titulo>Ley 1/2000, de 7 de enero, de prueba.</titulo>
    <numero_oficial>1/2000</numero_oficial>
    <rango codigo="1300">Ley</rango>
    <fecha_actualizacion>20100101</fecha_actualizacion>
    <estatus_derogacion>N</estatus_derogacion>
    <estatus_anulacion>N</estatus_anulacion>
    <vigencia_agotada>N</vigencia_agotada>
    <url_html_consolidada>https://www.boe.es/buscar/act.php?id=BOE-A-2000-1</url_html_consolidada>
  </metadatos>
  <texto>
    <bloque id="ti" tipo="encabezado" titulo="TÍTULO I">
      <version id_norma="BOE-A-2000-1" fecha_publicacion="20000108" fecha_vigencia="20000109">
        <p class="titulo_num">TÍTULO I</p><p class="titulo_tit">Disposiciones generales</p>
      </version>
    </bloque>
    <bloque id="a1" tipo="precepto" titulo="Artículo 1">
      <version id_norma="BOE-A-2000-1" fecha_publicacion="20000108" fecha_vigencia="20000109"><p>Texto original.</p></version>
      <version id_norma="BOE-A-2010-5" fecha_publicacion="20100101" fecha_vigencia="20100102"><p>Texto vigente.</p></version>
      <version id_norma="BOE-A-2030-1" fecha_publicacion="20300101" fecha_vigencia="20300102"><p>Texto futuro.</p></version>
    </bloque>
    <bloque id="a2" tipo="precepto" titulo="Artículo 2" fecha_caducidad="20050101">
      <version id_norma="BOE-A-2000-1" fecha_publicacion="20000108" fecha_vigencia="20000109"><p>Caducado.</p></version>
    </bloque>
    <bloque id="fi" tipo="firma" titulo="Firma">
      <version id_norma="BOE-A-2000-1" fecha_publicacion="20000108" fecha_vigencia="20000109"><p>Firmado.</p></version>
    </bloque>
  </texto>
</data></response>""".encode()


def _chunks(chunk_size=1500):
    root = etree.fromstring(DOCUMENT)
    return build_chunks(catalogue_item_from_document(root), root, TODAY, create_text_splitter(chunk_size))


def test_current_version_of_each_article_is_indexed_with_its_heading_path():
    chunks = _chunks()

    assert [(chunk.title, chunk.text) for chunk in chunks] == [
        ("Ley 1/2000, de 7 de enero, de prueba. > TÍTULO I. Disposiciones generales > Artículo 1", "Texto vigente.")
    ]
    metadata = chunks[0].metadata
    assert metadata["source_type"] == "Legislación consolidada"
    assert metadata["numero_oficial"] == "1/2000"
    assert metadata["bloque_titulo"] == "Artículo 1"
    assert metadata["fecha_vigencia_version"] == "2010-01-02"
    assert metadata["url"] == "https://www.boe.es/buscar/act.php?id=BOE-A-2000-1#a1"


def test_next_future_version_sets_the_revision_date():
    assert _chunks()[0].metadata["fecha_revision"] == "2030-01-02"


def test_long_article_is_split_into_chunks_within_the_chunk_size():
    long_document = DOCUMENT.replace(b"<p>Texto vigente.</p>", b"<p>" + b"palabra " * 400 + b"</p>")
    root = etree.fromstring(long_document)

    chunks = build_chunks(catalogue_item_from_document(root), root, TODAY, create_text_splitter(1000))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 1000 for chunk in chunks)


def test_repealed_norm_is_not_in_force():
    root = etree.fromstring(DOCUMENT.replace(b"<estatus_derogacion>N", b"<estatus_derogacion>S"))

    assert not document_is_in_force(root)
