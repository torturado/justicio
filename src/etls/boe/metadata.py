import typing as tp
from datetime import datetime

from pydantic import BaseModel, field_validator

from src.etls.common.metadata import MetadataDocument


class BOEMetadataReferencia(BaseModel):
    id: str
    palabra: str
    texto: str


class BOEMetadataDocument(MetadataDocument):
    """Class for keeping metadata of a BOE Document scrapped."""

    # Source
    source_name: str = "BOE"
    source_type: str = "Boletin"

    # Metadatos
    identificador: str
    diario: str
    numero_oficial: str = ""
    departamento: str
    rango: str = ""
    titulo: str
    url_pdf: str
    origen_legislativo: str = ""
    fecha_publicacion: str
    fecha_disposicion: str = ""
    anio: str
    mes: str
    dia: str

    # Analisis
    observaciones: str = ""
    ambito_geografico: str = ""
    modalidad: str = ""
    tipo: str = ""
    materias: tp.List[str]
    alertas: tp.List[str]
    notas: tp.List[str]
    ref_posteriores: tp.List[str]
    ref_anteriores: tp.List[str]

    @field_validator("ref_posteriores", mode="before")
    @classmethod
    def ref_posteriores_to_json(cls, references):
        return [
            reference.model_dump_json() if isinstance(reference, BOEMetadataReferencia) else reference
            for reference in references
        ]

    @field_validator("ref_anteriores", mode="before")
    @classmethod
    def ref_anteriores_to_json(cls, references):
        return [
            reference.model_dump_json() if isinstance(reference, BOEMetadataReferencia) else reference
            for reference in references
        ]

    @field_validator("fecha_publicacion", "fecha_disposicion")
    @classmethod
    def isoformat(cls, v):
        if v:
            return datetime.strptime(v, "%Y%m%d").strftime("%Y-%m-%d")
        return v
