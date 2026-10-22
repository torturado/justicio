from src.etls.common.metadata import MetadataDocument


class TemplateMetadataDocument(MetadataDocument):
    """Metadata of a document of the template bulletin.

    The canonical fields are inherited from MetadataDocument. Add only source-specific fields, with names that do not
    reuse the meaning of a canonical one.
    """

    source_name: str = "TEMPLATE"
    source_type: str = "Boletin"

    # Example of a source-specific field: bulletin section of the document
    seccion: str | None = None
