"""Claude-backed answer judge (ADR-0010).

Structured output rather than parsing prose. A judge that returns
"I'd say roughly 0.8, though the citation for claim two is weak" is a judge whose
score depends on a regex, and the first unusual phrasing silently becomes a zero.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import anthropic
from anthropic.types import MessageParam

from ragoogle_core.evaluation.run import GenerationScore

logger = logging.getLogger(__name__)

#: Pinned by default and stored with the dataset when overridden, because a
#: score whose criteria cannot be recovered is not a measurement.
DEFAULT_RUBRIC = """Score the answer on three independent axes, each 0.0 to 1.0.

faithfulness: Does every factual claim follow from the provided sources? Score
this low for anything asserted that the sources do not support, even if it is
true in general. An answer that is correct but ungrounded is a failure here --
the sources are the only thing this system is entitled to rely on.

answer_relevance: Does it address the question actually asked? An accurate answer
to a different question scores low.

citation_correctness: Do the inline [n] markers point at sources that actually
support the adjacent claim? Score low for markers that are merely topically
related, and low for claims that needed a citation and have none.

Judge faithfulness against the sources alone. Where an expected answer is given,
use it for relevance -- not to penalise wording that differs but says the same
thing."""

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "faithfulness": {"type": "number", "minimum": 0, "maximum": 1},
        "answer_relevance": {"type": "number", "minimum": 0, "maximum": 1},
        "citation_correctness": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
    },
    "required": [
        "faithfulness",
        "answer_relevance",
        "citation_correctness",
        "rationale",
    ],
    "additionalProperties": False,
}

#: A capable judge by default. Using a cheaper model than the one under test
#: risks the judge being the weaker reasoner, which turns a quality measurement
#: into a measurement of the judge.
DEFAULT_JUDGE_MODEL = "claude-opus-5"


class AnthropicJudge:
    """Implements `ragoogle_core.ports.AnswerJudge`."""

    def __init__(
        self,
        client: anthropic.AsyncAnthropic | None = None,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_JUDGE_MODEL,
    ) -> None:
        self._client = client or (
            anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        )
        self._model = model

    @property
    def model(self) -> str:
        """Pinned onto the run's config so a score stays reproducible."""
        return self._model

    async def judge(
        self,
        *,
        question: str,
        answer: str,
        sources: Sequence[str],
        expected_answer: str | None = None,
        rubric: str | None = None,
    ) -> GenerationScore:
        numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(sources, start=1))
        parts = [
            f"Question:\n{question}",
            f"Sources:\n{numbered or '(none were retrieved)'}",
            f"Answer under evaluation:\n{answer}",
        ]
        if expected_answer:
            parts.append(f"Reference answer:\n{expected_answer}")

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=2048,
            system=rubric or DEFAULT_RUBRIC,
            messages=[MessageParam(role="user", content="\n\n".join(parts))],
            thinking={"type": "adaptive"},
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": _SCHEMA,
                }
            },
        )

        payload = json.loads("".join(b.text for b in response.content if b.type == "text"))
        return GenerationScore(
            faithfulness=_clamp(payload["faithfulness"]),
            answer_relevance=_clamp(payload["answer_relevance"]),
            citation_correctness=_clamp(payload["citation_correctness"]),
            rationale=payload.get("rationale"),
        )


def _clamp(value: object) -> float:
    """The schema constrains the range; clamping is the belt to its braces.

    GenerationScore raises on a value outside [0, 1], and a judge returning 1.01
    should not abort a two-hundred-case run.
    """
    return min(1.0, max(0.0, float(value)))  # type: ignore[arg-type]
