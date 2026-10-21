import logging as lg
import re
import typing as tp
from datetime import date, datetime, timezone

from langchain_text_splitters import RecursiveCharacterTextSplitter
from lxml import etree

from src.etls.boe_consolidada.defs import CHUNK_OVERLAP, CHUNK_SIZE
from src.etls.common.utils import normalize_whitespace, xml_element_text, xml_to_text
from src.vector_store import Chunk

_TEXT_SPLITTER = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
_HEADING_PREFIX_LEVELS = {
    "libro": 1,
    "parte": 1,
    "titulo": 2,
    "capitulo": 3,
    "seccion": 4,
    "subseccion": 5,
}


def document_is_in_force(document_root: etree._Element) -> bool:
    metadata = document_root.find("./data/metadatos")
    if metadata is None:
        raise ValueError("BOE consolidated response has no metadatos element")
    return all(
        xml_element_text(metadata.find(field)) != "S"
        for field in ("estatus_derogacion", "estatus_anulacion", "vigencia_agotada")
    )


def catalogue_item_from_document(document_root: etree._Element) -> tp.Dict[str, tp.Any]:
    metadata = document_root.find("./data/metadatos")
    if metadata is None:
        raise ValueError("BOE consolidated response has no metadatos element")

    item: tp.Dict[str, tp.Any] = {}
    for field in (
        "fecha_actualizacion",
        "identificador",
        "fecha_disposicion",
        "numero_oficial",
        "titulo",
        "fecha_publicacion",
        "fecha_vigencia",
        "vigencia_agotada",
        "url_eli",
        "url_html_consolidada",
    ):
        item[field] = xml_element_text(metadata.find(field))
    for field in ("ambito", "departamento", "rango"):
        element = metadata.find(field)
        item[field] = {
            "codigo": element.get("codigo", "") if element is not None else "",
            "texto": xml_element_text(element),
        }
    return item


def build_chunks(catalogue_item: tp.Dict[str, tp.Any], document_root: etree._Element, today: date) -> tp.List[Chunk]:
    logger = lg.getLogger(build_chunks.__name__)
    text_element = document_root.find("./data/texto")
    if text_element is None:
        raise ValueError("BOE consolidated response has no texto element")

    blocks = text_element.findall("./bloque")
    revision = _fecha_revision(blocks, today)
    document_metadata = _document_metadata(catalogue_item, revision)
    heading_stack: tp.Dict[int, str] = {}
    chunks: tp.List[Chunk] = []

    for block in blocks:
        block_type = block.get("tipo", "")
        normalized_type = block_type.lower()
        block_id = block.get("id", "")
        block_title = normalize_whitespace(block.get("titulo", ""))
        expiry_date = _parse_date(block.get("fecha_caducidad"))
        versions = block.findall("./version")
        version = _current_version(versions, today)

        if normalized_type == "encabezado":
            level = _heading_level(version) if version is not None else None
            if level is None:
                level = _heading_level_from_title(block_title)
            if level is not None:
                _clear_heading_levels(heading_stack, level)
            if expiry_date is not None and expiry_date <= today:
                continue
            if version is not None:
                heading = _heading_text(version) or normalize_whitespace(block_title)
                if level is not None and heading:
                    heading_stack[level] = heading
            continue

        if block_title.casefold().startswith(("disposición", "anexo")):
            heading_stack.clear()

        if normalized_type == "firma" or (expiry_date is not None and expiry_date <= today):
            continue
        if version is None:
            continue

        body = xml_to_text(version)
        if not body:
            continue

        if normalized_type == "preambulo":
            block_title = "Preámbulo"
        elif not block_title:
            block_title = block_type.replace("_", " ").capitalize()

        title_parts = [normalize_whitespace(catalogue_item.get("titulo", ""))]
        title_parts.extend(heading_stack[level] for level in sorted(heading_stack))
        title_parts.append(block_title)
        title = " > ".join(part for part in title_parts if part)
        version_date = _version_date(version)
        version_metadata = {
            **document_metadata,
            "bloque_id": block_id,
            "bloque_tipo": block_type,
            "bloque_titulo": block_title,
            "url": _block_url(catalogue_item, block_id),
            "fecha_vigencia_version": version_date.isoformat() if version_date else None,
            "id_norma_version": version.get("id_norma"),
        }
        for part in _TEXT_SPLITTER.split_text(body):
            if part.strip():
                chunks.append(Chunk(title=title, text=part, metadata=version_metadata.copy()))

    logger.info("Built %s chunks for [%s]", len(chunks), catalogue_item.get("identificador", ""))
    return chunks


def _document_metadata(catalogue_item: tp.Dict[str, tp.Any], revision: tp.Optional[date]) -> tp.Dict[str, tp.Any]:
    return {
        "identificador": catalogue_item.get("identificador", ""),
        "titulo": normalize_whitespace(catalogue_item.get("titulo", "")),
        "rango": normalize_whitespace(_catalogue_text(catalogue_item.get("rango"))),
        "departamento": normalize_whitespace(_catalogue_text(catalogue_item.get("departamento"))),
        "ambito": normalize_whitespace(_catalogue_text(catalogue_item.get("ambito"))),
        "numero_oficial": catalogue_item.get("numero_oficial") or None,
        "fecha_disposicion": _date_string(catalogue_item.get("fecha_disposicion")),
        "fecha_publicacion": _date_string(catalogue_item.get("fecha_publicacion")),
        "fecha_vigencia": _date_string(catalogue_item.get("fecha_vigencia")),
        "fecha_actualizacion": _date_string(catalogue_item.get("fecha_actualizacion")),
        "url_eli": catalogue_item.get("url_eli") or None,
        "url_html_consolidada": catalogue_item.get("url_html_consolidada") or None,
        "fecha_revision": revision.isoformat() if revision else None,
        "source_name": "BOE",
        "source_type": "Legislación consolidada",
        "datetime_insert": datetime.now(timezone.utc).isoformat(),
    }


def _fecha_revision(blocks: tp.Sequence[etree._Element], today: date) -> tp.Optional[date]:
    future_dates: tp.List[date] = []
    for block in blocks:
        expiry_date = _parse_date(block.get("fecha_caducidad"))
        if expiry_date is not None and expiry_date > today:
            future_dates.append(expiry_date)
        for version in block.findall("./version"):
            version_date = _version_date(version)
            if version_date is not None and version_date > today:
                future_dates.append(version_date)
    return min(future_dates) if future_dates else None


def _current_version(versions: tp.Sequence[etree._Element], today: date) -> tp.Optional[etree._Element]:
    eligible = [
        (version_date, _parse_date(version.get("fecha_publicacion")) or version_date, version)
        for version in versions
        if (version_date := _version_date(version)) is not None and version_date <= today
    ]
    return max(eligible, key=lambda item: (item[0], item[1]))[2] if eligible else None


def _version_date(version: etree._Element) -> tp.Optional[date]:
    return _parse_date(version.get("fecha_vigencia")) or _parse_date(version.get("fecha_publicacion"))


def _heading_level(version: tp.Optional[etree._Element]) -> tp.Optional[int]:
    if version is None:
        return None
    levels = [
        level
        for paragraph in version.findall(".//p")
        if (level := _heading_class_level(paragraph.get("class", ""))) is not None
    ]
    return min(levels) if levels else None


def _heading_class_level(class_name: str) -> tp.Optional[int]:
    normalized_class = class_name.lower()
    for prefix, level in _HEADING_PREFIX_LEVELS.items():
        if normalized_class == prefix or normalized_class.startswith(f"{prefix}_"):
            return level
    return None


def _heading_level_from_title(title: str) -> tp.Optional[int]:
    normalized_title = normalize_whitespace(title).lower()
    for prefix, level in _HEADING_PREFIX_LEVELS.items():
        if normalized_title.startswith(prefix):
            return level
    return None


def _clear_heading_levels(stack: tp.Dict[int, str], level: int) -> None:
    for existing_level in list(stack):
        if existing_level >= level:
            del stack[existing_level]


def _heading_text(version: etree._Element) -> str:
    grouped: tp.Dict[str, tp.Dict[str, str]] = {}
    for paragraph in version.findall(".//p"):
        classes = paragraph.get("class", "").lower().split()
        if not classes:
            continue
        class_name = classes[0]
        for suffix in ("_num", "_tit"):
            if class_name.endswith(suffix):
                prefix = class_name[: -len(suffix)]
                if prefix in _HEADING_PREFIX_LEVELS:
                    grouped.setdefault(prefix, {})[suffix[1:]] = xml_element_text(paragraph)
                break
        else:
            if _heading_class_level(class_name) is not None:
                grouped.setdefault(class_name, {})["full"] = xml_element_text(paragraph)

    if grouped:
        heading_parts: tp.List[str] = []
        for prefix, values in grouped.items():
            if "full" in values:
                heading_parts.append(_format_full_heading(values["full"]))
            elif number := values.get("num"):
                heading_parts.append(f"{number.rstrip('. ')}. {values['tit']}" if values.get("tit") else number)
        if heading_parts:
            return " ".join(heading_parts)
    return " ".join(text for paragraph in version.findall(".//p") if (text := xml_element_text(paragraph)))


def _format_full_heading(text: str) -> str:
    match = re.match(r"^(Sección\s+\S+?)(?:\s+)(.+)$", text, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1).rstrip('. ')}. {match.group(2)}"
    return text


def _catalogue_text(value: tp.Any) -> str:
    return (
        normalize_whitespace(value.get("texto", ""))
        if isinstance(value, dict)
        else normalize_whitespace(str(value or ""))
    )


def _date_string(value: tp.Any) -> tp.Optional[str]:
    parsed = _parse_date(str(value)) if value else None
    return parsed.isoformat() if parsed else None


def _parse_date(value: tp.Optional[str]) -> tp.Optional[date]:
    if not value:
        return None
    raw_date = value[:8]
    try:
        return datetime.strptime(raw_date, "%Y%m%d").date()
    except ValueError:
        return None


def _block_url(catalogue_item: tp.Dict[str, tp.Any], block_id: str) -> tp.Optional[str]:
    url = catalogue_item.get("url_html_consolidada")
    return f"{url}#{block_id}" if url and block_id else url or None
