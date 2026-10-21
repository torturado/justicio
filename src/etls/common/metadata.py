import typing as tp
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MetadataDocument(BaseModel):
    # Text
    filepath: str

    # Source
    source_name: str
    source_type: str

    identificador: str
    titulo: tp.Optional[str] = None

    datetime_insert: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
