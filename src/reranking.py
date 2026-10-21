"""Cross-encoder reranking of the vector store's (payload, score) candidates."""

from __future__ import annotations

import typing as tp
from threading import Lock

if tp.TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

PayloadT = tp.TypeVar("PayloadT", bound=tp.Mapping[str, object])


class CrossEncoderReranker:
    """Reuse a scalar-scoring CrossEncoder, loaded on the first nonempty request.

    ``model`` accepts a model name/path or an existing CrossEncoder. Explicit
    ``device`` and ``max_length`` overrides also apply to an injected model on
    first use; otherwise its configuration is preserved. ``max_length=None``
    keeps the model's token limit. Truncation is handled by its tokenizer.

    Initialization and inference are serialized per instance for worker-thread
    use. Share one instance and call it off the async service's event loop:
    ``await asyncio.to_thread(reranker.rerank, query, candidates, top_k=10)``.
    An injected model should not be concurrently used outside this wrapper.
    """

    def __init__(
        self,
        model: str | CrossEncoder,
        *,
        device: str | None = None,
        max_length: int | None = None,
        batch_size: int = 8,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_length is not None and max_length <= 0:
            raise ValueError("max_length must be positive or None")
        self._model_source = model
        self._device = device
        self._max_length = max_length
        self._batch_size = batch_size
        self._model: CrossEncoder | None = None
        self._lock = Lock()

    def rerank(
        self,
        query: str,
        candidates: tp.Sequence[tp.Tuple[PayloadT, float]],
        *,
        top_k: int | None = None,
    ) -> tp.List[tp.Tuple[PayloadT, float]]:
        """Return original payloads with descending cross-encoder scores.

        Score each query against ``metadata.chunk_title`` (when present) and
        ``page_content``. Input RRF scores are replaced, not combined with the
        model scores; ties retain the input order. Scores are model-specific,
        not calibrated probabilities. All candidates are scored before top_k
        is applied. Empty input or top_k=0 returns [] without loading a model.
        """
        if top_k is not None and top_k < 0:
            raise ValueError("top_k must be nonnegative or None")
        if not candidates or top_k == 0:
            return []

        pairs = [(query, _document_text(payload)) for payload, _ in candidates]
        with self._lock:
            model = self._get_model()
            scores = model.predict(
                pairs,
                batch_size=self._batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                convert_to_tensor=False,
            )
        if scores.ndim != 1 or len(scores) != len(candidates):
            raise ValueError("The cross-encoder must return one scalar relevance score per candidate")

        reranked = [(payload, float(score)) for (payload, _), score in zip(candidates, scores)]
        reranked.sort(key=lambda candidate: candidate[1], reverse=True)
        return reranked[:top_k]

    def _get_model(self) -> CrossEncoder:
        # Called under the lock so concurrent first requests load only one model.
        if self._model is None:
            if isinstance(self._model_source, str):
                from sentence_transformers import CrossEncoder

                model = CrossEncoder(self._model_source, device=self._device, max_length=self._max_length)
            else:
                model = self._model_source
                if self._device is not None:
                    model.to(self._device)
                if self._max_length is not None:
                    model.max_length = self._max_length
            self._model = model
        return self._model


def _document_text(payload: tp.Mapping[str, object]) -> str:
    text = payload["page_content"]
    if not isinstance(text, str):
        raise TypeError("Candidate page_content must be a string")
    metadata = payload.get("metadata")
    title = metadata.get("chunk_title") if isinstance(metadata, tp.Mapping) else None
    if isinstance(title, str) and title and title.casefold() != "none":
        return f"{title}\n{text}"
    return text
