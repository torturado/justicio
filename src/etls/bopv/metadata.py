from src.etls.common.metadata import MetadataDocument


class BOPVMetadataDocument(MetadataDocument):
    """Metadata of a BOPV document. ``identificador`` is the path of its page, e.g. "2024/02/2400757a"."""

    source_name: str = "BOPV"

    # Section of the bulletin, e.g. "OTRAS DISPOSICIONES"
    seccion: str
