from datetime import date

from src.etls.common.etl import create_text_splitter, index_documents, split_document
from src.etls.common.metadata import MetadataDocument
from src.etls.common.scrapper import BaseScrapper


def _doc(identificador, text="Primer párrafo.\nSegundo párrafo."):
    return MetadataDocument(text=text, source_name="BOCM", identificador=identificador, titulo=f"Orden {identificador}")


class FakeIndex:
    def __init__(self, indexed=(), failing=()):
        self._indexed = set(indexed)
        self._failing = set(failing)
        self.replaced = {}

    def has_document(self, source_name, identificador):
        return identificador in self._indexed

    def replace_document(self, source_name, identificador, chunks):
        if identificador in self._failing:
            raise ConnectionError("Qdrant unavailable")
        self.replaced[identificador] = chunks


def test_a_failing_document_does_not_stop_the_others():
    index = FakeIndex(indexed={"A"}, failing={"B"})

    stats = index_documents([_doc("A"), _doc("B"), _doc("C")], index, create_text_splitter(1500))

    assert (stats.indexed, stats.skipped, stats.failed) == (1, 1, ["B"])
    assert list(index.replaced) == ["C"]


def test_chunks_carry_the_document_metadata_without_its_text():
    chunks = split_document(_doc("A", text="uno dos tres cuatro " * 10), create_text_splitter(50))

    assert len(chunks) > 1
    assert all(chunk.title == "Orden A" and len(chunk.text) <= 50 for chunk in chunks)
    assert chunks[0].metadata["identificador"] == "A"
    assert "text" not in chunks[0].metadata


class FlakyScrapper(BaseScrapper):
    def download_day(self, day):
        if day.day == 2:
            raise TimeoutError("summary unavailable")
        return [_doc(f"BOCM-{day:%Y%m%d}-1")]


def test_a_failing_day_is_recorded_and_the_other_days_are_downloaded():
    scrapper = FlakyScrapper(session=object())

    docs = scrapper.download_days(date(2026, 10, 1), date(2026, 10, 3))

    assert [doc.identificador for doc in docs] == ["BOCM-20261001-1", "BOCM-20261003-1"]
    assert scrapper.failures == ["day 2026-10-02"]
