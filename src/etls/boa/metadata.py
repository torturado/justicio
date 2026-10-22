from src.etls.common.metadata import MetadataDocument


class BOAMetadataDocument(MetadataDocument):
    """Metadata of a BOA document. ``identificador`` is the DOCN of the BOA search engine, e.g. "007938891"."""

    source_name: str = "BOA"

    # Number of the bulletin that published the document
    numero_boletin: str
    seccion: str | None = None
    subseccion: str | None = None
    codigo_materia: str | None = None
