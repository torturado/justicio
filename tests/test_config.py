import pytest
from pydantic import ValidationError

from src.etls.sources import BULLETIN_SCRAPPERS
from src.settings import Settings, load_settings


def test_every_bulletin_source_has_a_scope_for_the_answers():
    assert BULLETIN_SCRAPPERS.keys() <= load_settings().sources.keys()


def test_openvino_reranker_cannot_be_configured_for_a_gpu():
    settings = load_settings().model_dump()

    with pytest.raises(ValidationError):
        Settings.model_validate({**settings, "reranker_backend": "openvino", "reranker_device": "cuda"})
