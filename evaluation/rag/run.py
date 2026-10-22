"""Measure retrieval of the expected sources and, optionally, the grounding and abstention of the answers."""

import argparse
import asyncio
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from src.initialize import initialize_logging
from src.service.answering import PromptSource, citation_numbers
from src.service.retrieval import select_hits
from src.service.services import Services, create_services

QUESTIONS_PATH = Path(__file__).with_name("questions.json")
_ARTICLE_TITLE = re.compile(r"^art[ií]culo\s+(\d+[a-z]?)\b", re.IGNORECASE)


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _matches(payload: Mapping[str, Any], target: Mapping[str, str]) -> bool:
    if payload.get("identificador") != target["identificador"]:
        return False
    if "article" not in target:
        return True
    article_match = _ARTICLE_TITLE.match(_normalize(str(payload.get("bloque_titulo") or "")))
    return article_match is not None and article_match.group(1) == target["article"].casefold()


def _metrics(payloads: Sequence[Mapping[str, Any]], targets: Sequence[Mapping[str, str]], top_k: int) -> dict[str, Any]:
    """Rank of the first chunk of each target; a hit at k requires every target within the first k chunks."""
    ranks = [
        next((rank for rank, payload in enumerate(payloads, start=1) if _matches(payload, target)), None)
        for target in targets
    ]
    metrics: dict[str, Any] = {
        f"hit_at_{k}": all(rank is not None and rank <= k for rank in ranks) for k in sorted({1, 5, top_k})
    }
    metrics["reciprocal_rank"] = sum(1 / rank for rank in ranks if rank) / len(ranks)
    metrics["target_ranks"] = ranks
    return metrics


def _targets_found(payloads: Sequence[Mapping[str, Any]], targets: Sequence[Mapping[str, str]]) -> bool:
    return all(any(_matches(payload, target) for payload in payloads) for target in targets)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


async def _evaluate_question(services: Services, question: Mapping[str, Any], include_answers: bool) -> dict[str, Any]:
    settings = services.settings
    retriever = services.retriever
    sources = question.get("sources") or settings.default_sources
    targets = question["targets"]
    result: dict[str, Any] = {"id": question["id"], "query": question["query"], "targets": targets}

    if targets:
        hybrid = await retriever.search(question["query"], sources)
        reranked = await retriever.rerank(question["query"], hybrid)
        score_by_id = {hit.id: score for hit, score in reranked}
        scores = [score_by_id[hit.id] for hit in hybrid]
        top_k = settings.top_k_results
        result["hybrid"] = _metrics([hit.payload for hit in hybrid], targets, top_k)
        result["reranked"] = _metrics([hit.payload for hit, _ in reranked], targets, top_k)
        # Whether every target is among the sources given to the LLM, with and without keeping the best
        # hybrid results (HYBRID_HEAD_SIZE)
        selected = select_hits(hybrid, scores, top_k) if hybrid else []
        unprotected = select_hits(hybrid, scores, top_k, head_size=0) if hybrid else []
        result["selected"] = _targets_found([hit.payload for hit, _ in selected], targets)
        result["selected_without_hybrid_head"] = _targets_found([hit.payload for hit, _ in unprotected], targets)

    if include_answers:
        retrieved = await retriever.retrieve(question["query"], sources)
        if retrieved:
            answer, validation = await services.answerer.answer(
                question["query"],
                [PromptSource(title=source.title, text=source.text) for source in retrieved],
                scope="; ".join(settings.sources[source] for source in sources),
            )
            status = validation.status
        else:
            answer, status = "", "not_run"
        cited = [number for number in citation_numbers(answer) if 1 <= number <= len(retrieved)]
        result["answer"] = {
            "text": answer,
            "grounding_status": status,
            "abstained": status != "passed",
            "citation_count": len(citation_numbers(answer)),
            "valid_citation_count": len(cited),
            "targets_cited": bool(targets)
            and _targets_found([retrieved[number - 1].payload for number in cited], targets),
        }
    return result


async def _evaluate(include_answers: bool) -> dict[str, Any]:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    async with create_services() as services:
        results = [await _evaluate_question(services, question, include_answers) for question in questions]

    answerable = [result for result in results if result["targets"]]
    report: dict[str, Any] = {
        "question_count": len(results),
        "answerable_count": len(answerable),
        "retrieval": {
            stage: {
                metric: _mean([float(result[stage][metric]) for result in answerable])
                for metric in answerable[0][stage]
                if metric != "target_ranks"
            }
            for stage in ("hybrid", "reranked")
        }
        if answerable
        else {},
        "selected_hit_rate": _mean([float(result["selected"]) for result in answerable]),
        "selected_without_hybrid_head_hit_rate": _mean(
            [float(result["selected_without_hybrid_head"]) for result in answerable]
        ),
    }
    if include_answers:
        unanswerable = [result for result in results if not result["targets"]]
        citations = sum(result["answer"]["citation_count"] for result in results)
        report["answers"] = {
            "answered_rate": _mean([float(not result["answer"]["abstained"]) for result in answerable]),
            "targets_cited_rate": _mean([float(result["answer"]["targets_cited"]) for result in answerable]),
            "abstention_rate_unanswerable": _mean([float(result["answer"]["abstained"]) for result in unanswerable]),
            # An unanswerable question answered with citations that passed the audit usually states that the
            # sources do not contain the answer; review these answers in "results"
            "unanswerable_answered_ids": [result["id"] for result in unanswerable if not result["answer"]["abstained"]],
            "citation_reference_precision": (
                sum(result["answer"]["valid_citation_count"] for result in results) / citations if citations else None
            ),
        }
    report["results"] = results
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional path for the JSON report")
    parser.add_argument(
        "--answers",
        action="store_true",
        help="Call Gemini to measure answer rate, cited targets and abstention on unanswerable questions",
    )
    args = parser.parse_args()
    initialize_logging()
    report = asyncio.run(_evaluate(args.answers))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
