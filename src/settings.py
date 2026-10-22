from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, PositiveInt, model_validator

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "config.yaml"


class Settings(BaseModel):
    """Application configuration from config/config.yaml. Unknown or missing keys fail at startup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_size: PositiveInt

    embeddings_model_name: str
    embeddings_dimensions: PositiveInt

    qdrant_collection: str
    prefetch_limit: PositiveInt
    reranker_candidates: PositiveInt
    top_k_results: PositiveInt
    reranker_model_name: str
    reranker_backend: Literal["torch", "openvino"]
    reranker_device: str | None
    reranker_max_length: PositiveInt
    reranker_batch_size: PositiveInt

    sources: dict[str, str]
    default_sources: list[str]

    llm_model_name: str
    grounding_model_name: str
    seed: int
    max_output_tokens: PositiveInt
    llm_timeout_seconds: PositiveInt
    llm_retry_attempts: PositiveInt

    max_concurrent_answers: PositiveInt
    rate_limit_per_minute: PositiveInt

    prompt_system: str
    prompt_system_context: str

    @model_validator(mode="after")
    def _check_consistency(self) -> "Settings":
        unknown_sources = set(self.default_sources) - self.sources.keys()
        if not self.default_sources or unknown_sources:
            raise ValueError(f"default_sources must be a nonempty subset of sources (unknown: {unknown_sources})")
        if self.top_k_results > self.reranker_candidates:
            raise ValueError("top_k_results cannot exceed reranker_candidates")
        if self.reranker_backend == "openvino" and self.reranker_device not in (None, "cpu"):
            raise ValueError("The openvino reranker backend runs on the CPU; use the torch backend for a GPU")
        return self


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    with path.open(encoding="utf-8") as stream:
        return Settings.model_validate(yaml.safe_load(stream))
