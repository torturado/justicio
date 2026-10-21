"""
Define the metadata to be stored in the embedding database.
"""

from src.etls.common.metadata import MetadataDocument


class TemplateMetadataDocument(MetadataDocument):
    """Metadata shared by documents loaded through the bulletin template."""

    source_name: str = "TEMPLATE"
    source_type: str = "Boletin"
