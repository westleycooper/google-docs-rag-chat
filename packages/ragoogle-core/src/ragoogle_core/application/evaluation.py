"""The evaluation use case (ADR-0010).

Runs execute through the *production* retrieval path -- the same
`RetrieveContext` live traffic uses -- rather than a parallel evaluation
pipeline. An eval that does not exercise what production runs measures something
else, and the moment the two diverge the scores stop meaning anything.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ragoogle_core.application.chat import SYSTEM_PROMPT, _render_prompt
from ragoogle_core.application.retrieval import RetrievalRequest, RetrieveContext
from ragoogle_core.evaluation.dataset import Case, Dataset
from ragoogle_core.evaluation.metrics import score_retrieval
from ragoogle_core.evaluation.run import (
    CaseResult,
    EvaluationConfig,
    EvaluationRun,
)
from ragoogle_core.ports.chat_model import ChatModel
from ragoogle_core.ports.judge import AnswerJudge
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import RunId

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvaluationRequest:
    dataset: Dataset
    config: EvaluationConfig
    rubric: str | None = None
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.dataset.cases:
            raise InvariantViolation(
                "cannot evaluate an empty dataset; a run over zero cases reports "
                "a perfect score for a system nobody tested"
            )


class RunEvaluation:
    """Execute a dataset and score both stages separately."""

    def __init__(
        self,
        retrieve: RetrieveContext,
        model: ChatModel,
        judge: AnswerJudge | None = None,
    ) -> None:
        self._retrieve = retrieve
        self._model = model
        self._judge = judge

    async def __call__(self, request: EvaluationRequest) -> AsyncIterator[EvaluationRun]:
        """Yield the run after each case, so a long run is observable while it runs.

        A dataset of a few hundred cases takes minutes; returning only at the end
        would leave the config UI with a spinner and no way to show progress or
        an early regression.
        """
        run = EvaluationRun(
            run_id=RunId.new(),
            dataset_id=request.dataset.dataset_id,
            dataset_version=request.dataset.version,
            config=request.config,
        ).start()
        yield run

        for case in request.dataset.cases:
            run = run.record(await self._evaluate(case, request))
            yield run

        yield run.complete()

    async def _evaluate(self, case: Case, request: EvaluationRequest) -> CaseResult:
        started = time.perf_counter()
        try:
            result = await self._retrieve(
                RetrievalRequest(
                    query=case.question,
                    limit=request.config.retrieval_limit,
                    candidate_limit=request.config.candidate_limit,
                    rrf_k=request.config.rrf_k,
                    use_rerank=request.config.rerank_enabled,
                )
            )
        except Exception as error:
            logger.warning("retrieval failed for case %s: %s", case.case_id, error)
            return CaseResult(
                case_id=case.case_id,
                error=f"retrieval: {type(error).__name__}: {error}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        retrieved = [str(c.chunk.chunk_id) for c in result.citations]
        retrieval_score = score_retrieval(
            retrieved,
            frozenset(str(c) for c in case.expected_chunk_ids),
            request.config.retrieval_limit,
        )

        generation_score = None
        if case.scores_generation and self._judge is not None:
            try:
                reply = await self._model.complete(
                    system=SYSTEM_PROMPT,
                    messages=[("user", _render_prompt(case.question, result.citations))],
                    model_id=request.config.chat_model,
                    max_tokens=request.max_tokens,
                )
                generation_score = await self._judge.judge(
                    question=case.question,
                    answer=reply.text,
                    # What the model was actually given, not what should have
                    # been retrieved: faithfulness is about following from the
                    # provided sources, which is a different question.
                    sources=[c.chunk.text for c in result.citations],
                    expected_answer=case.expected_answer,
                    rubric=request.rubric,
                )
            except Exception as error:
                logger.warning("generation failed for case %s: %s", case.case_id, error)
                return CaseResult(
                    case_id=case.case_id,
                    retrieval=retrieval_score,
                    error=f"generation: {type(error).__name__}: {error}",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )

        return CaseResult(
            case_id=case.case_id,
            retrieval=retrieval_score,
            generation=generation_score,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
