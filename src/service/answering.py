"""Grounded answer generation with Gemini and a claim-to-citation audit."""

import json
import logging as lg
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel

from src.settings import Settings

ABSTENTION = (
    "No puedo ofrecer una respuesta fiable: la comprobación no pudo confirmar que todas las "
    "afirmaciones estén respaldadas por las fuentes citadas."
)
_GROUNDING_AUDIT_INSTRUCTION = (
    "Audita una respuesta jurídica usando exclusivamente las fuentes citadas que se adjuntan. "
    "Comprueba cada afirmación factual o jurídica: cada una debe llevar una cita y el fragmento "
    "indicado debe respaldar esa afirmación concreta. No basta con que trate del mismo tema. "
    "Marca como no respaldadas las afirmaciones sin cita, las que extrapolen el texto y las que "
    "mezclen requisitos de procedimientos distintos. No uses conocimiento externo. Devuelve "
    "supported=true únicamente si todas las afirmaciones están respaldadas y no hay citas inválidas. "
    "Devuelve listas vacías cuando no haya elementos que enumerar."
)
# [2], [1, 3], [1-3] or [1–3]
_CITATION_GROUP = re.compile(r"\[([1-9]\d*(?:\s*[-–]\s*[1-9]\d*)?(?:\s*,\s*[1-9]\d*(?:\s*[-–]\s*[1-9]\d*)?)*)\]")
_RANGE_SEPARATOR = re.compile(r"\s*[-–]\s*")


class LanguageModelError(Exception):
    """The language model returned no usable answer."""


@dataclass(frozen=True)
class PromptSource:
    title: str
    text: str


class GroundingAudit(BaseModel):
    """Structured result of the claim-to-citation verification pass."""

    supported: bool
    unsupported_claims: list[str]
    invalid_citations: list[int]


class GroundingValidation(BaseModel):
    status: Literal["passed", "failed", "unavailable", "not_run"]
    unsupported_claims: list[str] = []
    invalid_citations: list[int] = []


def citation_numbers(answer: str) -> list[int]:
    """Source numbers cited in an answer, in order, expanding ranges."""
    numbers: list[int] = []
    for group in _CITATION_GROUP.findall(answer):
        for part in group.split(","):
            bounds = [int(bound) for bound in _RANGE_SEPARATOR.split(part.strip())]
            numbers += list(range(bounds[0], bounds[1] + 1)) if len(bounds) == 2 and bounds[0] <= bounds[1] else bounds
    return numbers


def format_contents(query: str, sources: Sequence[PromptSource], feedback: GroundingValidation | None = None) -> str:
    """User message: the numbered sources as delimited data, then the question."""
    numbered = "\n\n".join(
        f"[{number}] {source.title}\n{source.text}" for number, source in enumerate(sources, start=1)
    )
    contents = f"<fuentes>\n{numbered}\n</fuentes>\n\n<pregunta>\n{query}\n</pregunta>"
    if feedback is None:
        return contents
    problems = [f"- Afirmación sin respaldo suficiente: {claim}" for claim in feedback.unsupported_claims]
    if feedback.invalid_citations:
        problems.append(f"- Citas inválidas: {', '.join(f'[{number}]' for number in feedback.invalid_citations)}")
    return (
        f"{contents}\n\nUna revisión de una respuesta anterior a esta pregunta encontró estos problemas:\n"
        + "\n".join(problems)
        + "\nEscribe una nueva respuesta en la que cada afirmación esté respaldada por el fragmento que cita; "
        "omite lo que las fuentes no respalden."
    )


class Answerer:
    def __init__(self, client: genai.Client, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def answer(
        self, query: str, sources: Sequence[PromptSource], scope: str | None = None
    ) -> tuple[str, GroundingValidation]:
        """Answer from the sources, or abstain when the audit cannot confirm every claim.

        An answer whose citations fail the audit is regenerated once with the audit findings. An answer
        without citations is not regenerated: it usually states that the sources are insufficient.
        """
        logger = lg.getLogger(self.answer.__name__)
        answer = await self._generate(query, sources, scope)
        validation = await self._validate(query, answer, sources)
        if validation.status == "failed" and citation_numbers(answer):
            logger.info("Regenerating an answer that failed the grounding audit")
            answer = await self._generate(query, sources, scope, feedback=validation)
            validation = await self._validate(query, answer, sources)
        if validation.status != "passed":
            logger.info("Abstaining: grounding validation %s", validation.status)
            return ABSTENTION, validation
        return answer, validation

    async def _generate(
        self,
        query: str,
        sources: Sequence[PromptSource],
        scope: str | None,
        feedback: GroundingValidation | None = None,
    ) -> str:
        system_instruction = [self._settings.prompt_system, self._settings.prompt_system_context]
        if scope:
            system_instruction.append(f"Ámbito de las fuentes consultadas: {scope}.")
        response = await self._client.aio.models.generate_content(
            model=self._settings.llm_model_name,
            contents=format_contents(query, sources, feedback),
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                seed=self._settings.seed,
                max_output_tokens=self._settings.max_output_tokens,
            ),
        )
        lg.getLogger(self._generate.__name__).info("Answer usage: %s", response.usage_metadata)
        if not response.text:
            finish_reason = response.candidates[0].finish_reason if response.candidates else None
            raise LanguageModelError(f"The language model returned no answer (finish reason: {finish_reason})")
        return response.text

    async def _validate(self, query: str, answer: str, sources: Sequence[PromptSource]) -> GroundingValidation:
        numbers = citation_numbers(answer)
        invalid_citations = sorted(set(numbers) - set(range(1, len(sources) + 1)))
        if invalid_citations:
            return GroundingValidation(status="failed", invalid_citations=invalid_citations)
        if not numbers:
            return GroundingValidation(status="failed", unsupported_claims=["La respuesta no contiene citas."])

        cited_sources = [
            {"number": number, "title": sources[number - 1].title, "text": sources[number - 1].text}
            for number in sorted(set(numbers))
        ]
        try:
            audit = await self._audit(query, answer, cited_sources)
        except Exception:
            lg.getLogger(self._validate.__name__).exception("Citation grounding audit failed")
            return GroundingValidation(status="unavailable")
        valid = audit.supported and not audit.unsupported_claims and not audit.invalid_citations
        return GroundingValidation(
            status="passed" if valid else "failed",
            unsupported_claims=audit.unsupported_claims,
            invalid_citations=sorted(set(audit.invalid_citations)),
        )

    async def _audit(self, query: str, answer: str, cited_sources: list[dict]) -> GroundingAudit:
        response = await self._client.aio.models.generate_content(
            model=self._settings.grounding_model_name,
            contents=json.dumps(
                {"question": query, "answer": answer, "cited_sources": cited_sources}, ensure_ascii=False
            ),
            config=types.GenerateContentConfig(
                system_instruction=[_GROUNDING_AUDIT_INSTRUCTION],
                response_mime_type="application/json",
                response_schema=GroundingAudit,
                seed=self._settings.seed,
                max_output_tokens=self._settings.max_output_tokens,
            ),
        )
        if isinstance(response.parsed, GroundingAudit):
            return response.parsed
        if isinstance(response.parsed, dict):
            return GroundingAudit.model_validate(response.parsed)
        if response.text:
            return GroundingAudit.model_validate_json(response.text)
        raise LanguageModelError("The language model returned no structured citation audit")
