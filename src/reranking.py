"""Cross-encoder relevance scoring of retrieved chunks.

The same model runs with PyTorch (``CrossEncoderReranker``, for GPUs) or as an int8 OpenVINO model
(``OpenVINOReranker``, for CPUs). On an Intel Core i5-13500, reranking 40 chunks takes about 6 s with OpenVINO
instead of 19 s with PyTorch, and both rank the evaluation set equally within one question (see
evaluation/rag/README.md).
"""

import logging as lg
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

# Measurement ID of the anonymous usage telemetry that OpenVINO's converter and NNCF send unless it is declined
_OPENVINO_TELEMETRY_ID = "G-W5E9RNLD4H"


class Reranker(Protocol):
    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        """Relevance score of each text for the query, in the same order as the texts."""
        ...


class CrossEncoderReranker:
    """Scores (query, text) pairs with a sentence-transformers CrossEncoder, loaded on construction.

    Scores are the model's relevance probabilities, not calibrated confidences. Texts longer than
    ``max_length`` tokens are truncated, so chunks should fit in that window.

    Not thread-safe: the service calls it from its single inference thread.
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str | None = None,
        max_length: int | None = None,
        batch_size: int = 8,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        from sentence_transformers import CrossEncoder

        self._model = CrossEncoder(model_name, device=device, max_length=max_length)
        self._batch_size = batch_size

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        scores = self._model.predict(
            [(query, text) for text in texts],
            batch_size=self._batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        if scores.ndim != 1 or len(scores) != len(texts):
            raise ValueError("The cross-encoder must return one scalar relevance score per text")
        return [float(score) for score in scores]


class OpenVINOReranker:
    """int8 OpenVINO version of a cross-encoder, created by ``export``. Scores like CrossEncoderReranker.

    Not thread-safe: the service calls it from its single inference thread.
    """

    MODEL_FILE = "model.xml"

    def __init__(self, model_dir: Path, *, max_length: int, batch_size: int = 8) -> None:
        import openvino as ov
        from transformers import AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self._model = ov.Core().compile_model(model_dir / self.MODEL_FILE, "CPU")
        self._max_length = max_length
        self._batch_size = batch_size

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        logits = [
            self._model(
                _encode(
                    self._tokenizer,
                    [(query, text) for text in texts[start : start + self._batch_size]],
                    self._max_length,
                )
            )[0][:, 0]
            for start in range(0, len(texts), self._batch_size)
        ]
        if not logits:
            return []
        return [float(score) for score in 1 / (1 + np.exp(-np.concatenate(logits)))]

    @classmethod
    def export(
        cls, model_name: str, model_dir: Path, calibration_pairs: Sequence[tuple[str, str]], max_length: int
    ) -> None:
        """Convert a Hugging Face cross-encoder to OpenVINO and quantize it to int8.

        The (query, text) calibration pairs should resemble the reranked inputs: quantizing with in-domain text
        keeps the ranking of the original model. Takes a few minutes and several GB of memory.
        """
        import nncf
        import openvino as ov
        import openvino_telemetry
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        logger = lg.getLogger(cls.export.__name__)
        openvino_telemetry.Telemetry.opt_out(tid=_OPENVINO_TELEMETRY_ID)
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name).eval()
        example = tokenizer(["pregunta"], ["texto"], return_tensors="pt")
        with torch.no_grad():
            ov_model = ov.convert_model(
                model, example_input={"input_ids": example["input_ids"], "attention_mask": example["attention_mask"]}
            )
        calibration = [_encode(tokenizer, [pair], max_length) for pair in calibration_pairs]
        quantized = nncf.quantize(
            ov_model, nncf.Dataset(calibration), model_type=nncf.ModelType.TRANSFORMER, subset_size=len(calibration)
        )
        model_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(dir=model_dir.parent))
        try:
            ov.save_model(quantized, staging / cls.MODEL_FILE)
            tokenizer.save_pretrained(staging)
            # Atomic, so that a concurrent export or an interrupted one never leaves a partial model
            staging.rename(model_dir)
        except OSError:
            if not (model_dir / cls.MODEL_FILE).exists():
                raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        logger.info("Exported int8 OpenVINO reranker to %s", model_dir)


def _encode(tokenizer, pairs: Sequence[tuple[str, str]], max_length: int) -> dict[str, np.ndarray]:
    encoded = tokenizer(
        [query for query, _ in pairs],
        [text for _, text in pairs],
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="np",
    )
    return {"input_ids": encoded["input_ids"], "attention_mask": encoded["attention_mask"]}


def openvino_model_dir(model_name: str) -> Path:
    """Where the OpenVINO export of a model is kept: next to the Hugging Face models, so that it persists."""
    from huggingface_hub import constants

    return Path(constants.HF_HOME) / "justicio" / f"{model_name.replace('/', '--')}-openvino-int8"
