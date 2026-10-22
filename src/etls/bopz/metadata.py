from src.etls.common.metadata import MetadataDocument


class BOPZMetadataDocument(MetadataDocument):
    """Metadata of an edict published in the BOPZ."""

    source_name: str = "BOPZ"
    source_type: str = "Boletin"

    fecha_publicacion: str
    # Registry number of the edict in the bulletin, e.g. "10885/2018". It is not the number of a norm.
    numero_registro: str | None = None

    # Analisis
    materia: list[str] = []
