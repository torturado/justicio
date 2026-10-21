"""Measure exact-law-and-article retrieval before and after cross-encoder reranking."""

import argparse
import asyncio
import json
import re
import typing as tp
import unicodedata
from pathlib import Path

QUESTIONS_PATH = Path(__file__).with_name("questions.json")
_ARTICLE_TITLE = re.compile(r"^art[ií]culo\s+(\d+[a-z]?)\b", re.IGNORECASE)
_CITATION = re.compile(r"\[(\d+)\]")


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _matches_target(payload: tp.Mapping[str, tp.Any], target: tp.Mapping[str, str]) -> bool:
    metadata = payload.get("metadata") or payload
    identifier = metadata.get("identificador") or metadata.get("boe_id")
    if identifier != target["boe_id"]:
        return False
    title = _normalize(str(metadata.get("bloque_titulo") or metadata.get("article") or ""))
    article_match = _ARTICLE_TITLE.match(title)
    return article_match is not None and article_match.group(1) == target["article"].casefold()


def _metrics(
    docs: tp.Sequence[tp.Tuple[tp.Mapping[str, tp.Any], float]], target: tp.Mapping[str, str]
) -> tp.Dict[str, tp.Any]:
    ranks = [rank for rank, (payload, _) in enumerate(docs, start=1) if _matches_target(payload, target)]
    first_rank = ranks[0] if ranks else None
    return {
        "hit_at_1": bool(first_rank and first_rank <= 1),
        "hit_at_5": bool(first_rank and first_rank <= 5),
        "hit_at_12": bool(first_rank and first_rank <= 12),
        "reciprocal_rank": 1 / first_rank if first_rank else 0,
        "first_target_rank": first_rank,
        "candidate_count": len(docs),
    }


async def _evaluate(collection_name: str, include_answers: bool) -> tp.Dict[str, tp.Any]:
    # Importing the service uses the same embedder, Qdrant filters, and reranker as /qa.
    from src.service.main import RERANKER, qa, retrieve

    questions = json.loads(QUESTIONS_PATH.read_text())
    results = []
    for question in questions:
        candidates = await retrieve(question["query"], collection_name, rerank=False)
        reranked = await asyncio.to_thread(RERANKER.rerank, question["query"], candidates)
        target = {"boe_id": question["boe_id"], "article": question["article"]}
        result = {
            "id": question["id"],
            "query": question["query"],
            "target": target,
            "hybrid": _metrics(candidates, target),
            "reranked": _metrics(reranked, target),
        }
        if include_answers:
            answer_result = await qa(input_query=question["query"], collection_name=collection_name)
            citations = [int(number) for number in _CITATION.findall(answer_result["answer"])]
            source_by_number = {source["number"]: source for source in answer_result["sources"]}
            result["answer"] = answer_result["answer"]
            result["grounding_validation"] = answer_result.get("grounding_validation")
            result["citation_grounding"] = {
                "citation_numbers": citations,
                "unknown_citations": sorted(set(citations) - source_by_number.keys()),
                "target_article_cited": any(
                    number in source_by_number and _matches_target(source_by_number[number], target)
                    for number in citations
                ),
            }
        results.append(result)

    def aggregate(key: str) -> tp.Dict[str, float]:
        return {
            metric: sum(item[key][metric] for item in results) / len(results)
            for metric in ("hit_at_1", "hit_at_5", "hit_at_12", "reciprocal_rank")
        }

    report = {
        "collection": collection_name,
        "question_count": len(results),
        "hybrid_mean": aggregate("hybrid"),
        "reranked_mean": aggregate("reranked"),
        "results": results,
    }
    if include_answers:
        answers_with_citations = [
            item["citation_grounding"] for item in results if item["citation_grounding"]["citation_numbers"]
        ]
        citation_count = sum(len(item["citation_numbers"]) for item in answers_with_citations)
        unknown_citation_count = sum(len(item["unknown_citations"]) for item in answers_with_citations)
        report["citation_grounding_mean"] = {
            "citation_reference_precision": (
                (citation_count - unknown_citation_count) / citation_count if citation_count else None
            ),
            "target_article_cited_rate": sum(item["target_article_cited"] for item in results) / len(results),
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default="justicio", help="Qdrant collection to evaluate")
    parser.add_argument("--output", type=Path, help="Optional path for the JSON report")
    parser.add_argument(
        "--answers",
        action="store_true",
        help="Call Gemini and measure whether citations resolve to returned sources and cite the expected article",
    )
    args = parser.parse_args()
    report = asyncio.run(_evaluate(args.collection, args.answers))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
