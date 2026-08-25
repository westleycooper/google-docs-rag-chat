"""The answer-judge port (ADR-0010).

An LLM judge is itself a model that can be wrong and that drifts between
versions, which is why the judge model is pinned on the run's config and the
rubric is stored with the dataset. A score whose criteria cannot be recovered is
not a measurement.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from ragoogle_core.evaluation.run import GenerationScore


@runtime_checkable
class AnswerJudge(Protocol):
    @property
    def model(self) -> str:
        """Which model is judging.

        Part of the port, not an adapter detail: ADR-0010 requires the judge to
        be pinned onto the run's configuration, because an unpinned judge makes
        a score unreproducible -- the metric drifts under you as the judge model
        changes.
        """
        ...

    async def judge(
        self,
        *,
        question: str,
        answer: str,
        sources: Sequence[str],
        expected_answer: str | None = None,
        rubric: str | None = None,
    ) -> GenerationScore:
        """Score an answer against the sources it claims to rest on.

        `sources` is the text actually placed in the prompt, not what should have
        been retrieved: faithfulness asks whether the answer follows from what
        the model was given, which is a different question from whether the right
        thing was retrieved. Conflating them is what makes a regression
        unattributable.
        """
        ...
