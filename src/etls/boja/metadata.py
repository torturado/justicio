from src.etls.common.metadata import MetadataDocument


class BOJAMetadataDocument(MetadataDocument):
    """Metadata of a disposition published in the BOJA."""

    source_name: str = "BOJA"
    source_type: str = "Boletin"

    # Bulletin section of the disposition, e.g. "Disposiciones generales"
    tipologia: str | None = None
