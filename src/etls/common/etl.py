import logging as lg
import os
import typing as tp

from langchain_text_splitters import RecursiveCharacterTextSplitter

from src.etls.common.metadata import MetadataDocument
from src.initialize import initialize_logging
from src.vector_store import Chunk, VectorStore

initialize_logging()


class ETL:
    def __init__(self, config_loader, vector_store: VectorStore):
        self._vector_store = vector_store
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=config_loader["chunk_size"],
            chunk_overlap=config_loader["chunk_overlap"],
        )

    def run(self, docs: tp.List[MetadataDocument]) -> int:
        """Index the documents that are not indexed yet and remove their downloaded text files.

        :return: number of documents indexed
        """
        logger = lg.getLogger(self.run.__name__)
        logger.info("Indexing %s documents", len(docs))
        indexed = 0
        for doc in docs:
            if self._vector_store.has_document(doc.identificador):
                logger.info("Skipping document %s: already indexed", doc.identificador)
            else:
                chunks = self._split_document(doc)
                self._vector_store.replace_document(doc.identificador, chunks)
                logger.info("Indexed document %s in %s chunks", doc.identificador, len(chunks))
                indexed += 1
            os.remove(doc.filepath)
        logger.info("Indexed %s of %s documents", indexed, len(docs))
        return indexed

    def _split_document(self, doc: MetadataDocument) -> tp.List[Chunk]:
        with open(doc.filepath) as f:
            text = f.read()
        metadata = doc.model_dump(exclude={"filepath"})
        title = doc.titulo or doc.identificador
        return [Chunk(title=title, text=part, metadata=metadata) for part in self._text_splitter.split_text(text)]
