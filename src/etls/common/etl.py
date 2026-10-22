import logging as lg
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.etls.common.metadata import MetadataDocument
from src.vector_store import Chunk, VectorIndex


@dataclass
class IndexStats:
    indexed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def create_text_splitter(chunk_size: int) -> RecursiveCharacterTextSplitter:
    # No overlap: the answer context joins adjacent chunks, which would otherwise repeat text
    return RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=0)


def split_document(doc: MetadataDocument, text_splitter: RecursiveCharacterTextSplitter) -> list[Chunk]:
    metadata = doc.model_dump()
    title = doc.titulo or doc.identificador
    return [
        Chunk(title=title, text=part, metadata=metadata) for part in text_splitter.split_text(doc.text) if part.strip()
    ]


def index_documents(
    docs: Sequence[MetadataDocument], index: VectorIndex, text_splitter: RecursiveCharacterTextSplitter
) -> IndexStats:
    """Index the documents that are not indexed yet. A failing document is logged and recorded, not fatal."""
    logger = lg.getLogger(index_documents.__name__)
    logger.info("Indexing %s documents", len(docs))
    stats = IndexStats()
    for doc in docs:
        try:
            if index.has_document(doc.source_name, doc.identificador):
                stats.skipped += 1
                continue
            chunks = split_document(doc, text_splitter)
            index.replace_document(doc.source_name, doc.identificador, chunks)
            logger.info("Indexed document %s in %s chunks", doc.identificador, len(chunks))
            stats.indexed += 1
        except Exception:
            logger.exception("Failed to index document %s", doc.identificador)
            stats.failed.append(doc.identificador)
    logger.info("Indexed %s, skipped %s and failed %s documents", stats.indexed, stats.skipped, len(stats.failed))
    return stats
