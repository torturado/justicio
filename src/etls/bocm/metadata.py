from pydantic import Field

from src.etls.common.metadata import MetadataDocument

# Código de Verificación Electrónica (CVE) of a document
CVE_REGEX = r"^BOCM-\d{8}-\d{1,3}$"


class BOCMMetadataDocument(MetadataDocument):
    """Metadata of a BOCM document. ``identificador`` is its CVE."""

    source_name: str = "BOCM"

    identificador: str = Field(pattern=CVE_REGEX, examples=["BOCM-20240129-24"])
    # Number of the bulletin that published the document
    numero_boletin: str
    paginas: str | None = None
    # Code of the section, e.g. "1.3.103.1", and its first level, e.g. "1"
    seccion_normalizada: str
    seccion: str
    # Subsection of section I, e.g. "A) Disposiciones Generales"
    subseccion: str | None = None
    # Subject of a section III (local administration) document, e.g. "URBANISMO"
    apartado: str | None = None
