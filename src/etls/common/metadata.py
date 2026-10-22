from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MetadataDocument(BaseModel):
    """A downloaded document: its text and the metadata stored with each of its chunks.

    Field names have the same meaning in every source, because they are shown to the LLM and used in filters.
    Source-specific metadata belongs to subclasses, under names that do not reuse these meanings.
    """

    # Plain text of the document. It is split into chunks, so it is excluded from the chunk metadata.
    text: str = Field(exclude=True)

    source_name: str
    source_type: str = "Boletin"

    # Identifier of the document within its source
    identificador: str
    titulo: str | None = None
    departamento: str | None = None
    rango: str | None = None
    # Official number of a norm, e.g. "39/2015". Never a bulletin, page or registry number.
    numero_oficial: str | None = None

    # ISO dates (YYYY-MM-DD)
    fecha_publicacion: str | None = None
    fecha_disposicion: str | None = None

    url_pdf: str | None = None
    url_html: str | None = None
    url_boletin: str | None = None

    datetime_insert: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
