from pydantic import BaseModel

from src.etls.common.metadata import MetadataDocument


class BOEMetadataReferencia(BaseModel):
    id: str
    palabra: str
    texto: str


class BOEMetadataDocument(MetadataDocument):
    """Metadata of a document of the daily BOE."""

    source_name: str = "BOE"
    source_type: str = "Boletin"

    identificador: str
    titulo: str
    diario: str = ""
    origen_legislativo: str = ""
    url_eli: str | None = None

    # Analisis
    observaciones: str = ""
    ambito_geografico: str = ""
    modalidad: str = ""
    tipo: str = ""
    materias: list[str] = []
    alertas: list[str] = []
    notas: list[str] = []
    ref_posteriores: list[BOEMetadataReferencia] = []
    ref_anteriores: list[BOEMetadataReferencia] = []
