"""The evaluation use case (ADR-0010), against in-memory ports."""

from __future__ import annotations

import pytest
from tests.fakes import (
    FakeAnswerJudge,
    FakeChatModel,
    FakeEmbeddingProvider,
    FakeReranker,
    FakeVectorStore,
)

from ragoogle_core.application import (
    EvaluationRequest,
    RetrieveContext,
    RunEvaluation,
)
from ragoogle_core.evaluation import (
    Case,
    Dataset,
    EvaluationConfig,
    EvaluationState,
    GenerationScore,
)
from ragoogle_core.retrieval import Chunk, DocumentRef
from ragoogle_core.shared.errors import InvariantViolation
from ragoogle_core.shared.identifiers import CaseId, ChunkId, DatasetId, DocumentId, SourceId

SOURCE = SourceId.new()
CORPUS = [
    "Revenue for the quarter rose twelve percent against plan.",
    "Project PRJ-4471 was descoped after the vendor review.",
    "Headcount grew by four in the delivery organisation.",
]


def make_chunk(text: str, ordinal: int) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document=DocumentRef(
            document_id=DocumentId.new(),
            source_id=SOURCE,
            external_id=f"d{ordinal}",
            title="Q3 Review",
            mime_type="text/plain",
        ),
        ordinal=ordinal,
        text=text,
        token_count=len(text.split()),
    )


def config(**kw) -> EvaluationConfig:
    defaults = dict(
        embedding_model="fake-embed",
        embedding_dimensions=8,
        chat_model="claude-opus-5",
        retrieval_limit=3,
        candidate_limit=10,
        rrf_k=60,
        rerank_enabled=True,
    )
    return EvaluationConfig(**{**defaults, **kw})


@pytest.fixture
async def wired():
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    chunks = [make_chunk(t, i) for i, t in enumerate(CORPUS)]
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))
    judge = FakeAnswerJudge()
    use_case = RunEvaluation(
        RetrieveContext(embeddings, store, FakeReranker()),
        FakeChatModel(reply_text="Revenue rose twelve percent [1]."),
        judge,
    )
    return use_case, chunks, judge


def dataset(*cases: Case) -> Dataset:
    return Dataset(dataset_id=DatasetId.new(), name="Regression", cases=cases)


async def final(use_case, request):
    runs = [run async for run in use_case(request)]
    return runs[-1], runs


# -- guards ---------------------------------------------------------------


def test_an_empty_dataset_is_refused():
    """A run over zero cases reports a perfect score for a system nobody tested."""
    with pytest.raises(InvariantViolation, match="empty dataset"):
        EvaluationRequest(dataset=dataset(), config=config())


# -- execution ------------------------------------------------------------


async def test_a_run_completes_and_records_a_result_per_case(wired):
    use_case, _chunks, _ = wired
    cases = (
        Case(case_id=CaseId.new(), question="What happened to revenue?"),
        Case(case_id=CaseId.new(), question="What happened to PRJ-4471?"),
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(*cases), config=config()))

    assert run.state is EvaluationState.COMPLETED
    assert len(run.results) == 2
    assert run.duration_seconds is not None


async def test_progress_is_yielded_after_every_case(wired):
    """A few hundred cases takes minutes; the config UI needs progress, not a spinner."""
    use_case, _, _ = wired
    cases = tuple(Case(case_id=CaseId.new(), question=f"Question {i}?") for i in range(3))
    _, runs = await final(use_case, EvaluationRequest(dataset=dataset(*cases), config=config()))
    # start + one per case + completion
    assert len(runs) == 5
    assert [len(r.results) for r in runs] == [0, 1, 2, 3, 3]


async def test_the_run_pins_the_dataset_version_it_scored(wired):
    """A score is meaningless if the questions behind it can change afterwards."""
    use_case, _, _ = wired
    ds = dataset(Case(case_id=CaseId.new(), question="revenue?"))
    grown = ds.with_case(Case(case_id=CaseId.new(), question="headcount?"))
    run, _ = await final(use_case, EvaluationRequest(dataset=grown, config=config()))
    assert run.dataset_version == grown.version
    assert run.dataset_version != ds.version


async def test_the_configuration_is_pinned_to_the_run(wired):
    use_case, _, _ = wired
    cfg = config(rerank_enabled=False, rrf_k=30)
    run, _ = await final(
        use_case,
        EvaluationRequest(
            dataset=dataset(Case(case_id=CaseId.new(), question="revenue?")), config=cfg
        ),
    )
    assert run.config.rrf_k == 30
    assert run.config.differences(config())["rrf_k"] == (30, 60)


# -- retrieval scoring ----------------------------------------------------


async def test_retrieval_is_scored_against_expected_chunks(wired):
    use_case, chunks, _ = wired
    case = Case(
        case_id=CaseId.new(),
        question="What happened to revenue?",
        expected_chunk_ids=frozenset({chunks[0].chunk_id}),
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    score = run.results[0].retrieval
    assert score is not None
    assert score.is_defined
    assert score.recall == 1.0


async def test_a_case_with_no_expected_chunks_scores_undefined(wired):
    use_case, _, _ = wired
    case = Case(case_id=CaseId.new(), question="anything?")
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    score = run.results[0].retrieval
    assert score is not None
    assert not score.is_defined
    assert run.mean_recall is None


async def test_a_case_the_retriever_misses_entirely_is_flagged(wired):
    """Points at ingestion or chunking rather than at ranking."""
    use_case, _, _ = wired
    case = Case(
        case_id=CaseId.new(),
        question="What happened to revenue?",
        expected_chunk_ids=frozenset({ChunkId.new()}),  # not in the corpus
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert len(run.missed_entirely) == 1


# -- generation scoring ---------------------------------------------------


async def test_generation_is_judged_when_an_expected_answer_exists(wired):
    use_case, _, judge = wired
    case = Case(
        case_id=CaseId.new(),
        question="What happened to revenue?",
        expected_answer="It rose twelve percent.",
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert run.results[0].generation is not None
    assert judge.calls


async def test_the_judge_sees_the_sources_actually_given_to_the_model(wired):
    """Faithfulness asks whether the answer follows from what the model was given."""
    use_case, _, judge = wired
    case = Case(case_id=CaseId.new(), question="revenue?", expected_answer="It rose.")
    await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert judge.calls[0]["sources"]
    assert all(isinstance(s, str) for s in judge.calls[0]["sources"])


async def test_a_custom_rubric_reaches_the_judge(wired):
    """A score whose criteria cannot be recovered is not a measurement."""
    use_case, _, judge = wired
    case = Case(case_id=CaseId.new(), question="q?", expected_answer="a")
    await final(
        use_case,
        EvaluationRequest(dataset=dataset(case), config=config(), rubric="Be extremely harsh."),
    )
    assert judge.calls[0]["rubric"] == "Be extremely harsh."


async def test_no_judge_means_no_generation_score(wired):
    """Retrieval is still scored -- half the signal beats none."""
    _, chunks, _ = wired
    store, embeddings = FakeVectorStore(), FakeEmbeddingProvider()
    await store.upsert(chunks, await embeddings.embed_documents([c.text for c in chunks]))
    use_case = RunEvaluation(
        RetrieveContext(embeddings, store, FakeReranker()), FakeChatModel(), judge=None
    )
    case = Case(
        case_id=CaseId.new(),
        question="revenue?",
        expected_answer="It rose.",
        expected_chunk_ids=frozenset({chunks[0].chunk_id}),
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert run.results[0].generation is None
    assert run.results[0].retrieval is not None


async def test_a_hallucinating_answer_is_surfaced_not_averaged_away(wired):
    use_case, _, judge = wired
    judge.fixed = GenerationScore(faithfulness=0.1, answer_relevance=0.95, citation_correctness=0.2)
    case = Case(case_id=CaseId.new(), question="q?", expected_answer="a")
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert len(run.hallucinations) == 1


# -- failure isolation ----------------------------------------------------


async def test_one_failing_case_does_not_end_the_run(wired):
    use_case, _, _ = wired

    original = use_case._retrieve
    calls = {"n": 0}

    async def sometimes_fail(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("vector store unreachable")
        return await original(request)

    use_case._retrieve = sometimes_fail
    cases = (
        Case(case_id=CaseId.new(), question="first?"),
        Case(case_id=CaseId.new(), question="second?"),
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(*cases), config=config()))

    assert run.state is EvaluationState.COMPLETED
    assert len(run.failures) == 1
    assert "retrieval:" in (run.failures[0].error or "")
    assert not run.results[1].failed


async def test_a_judge_failure_keeps_the_retrieval_score(wired):
    """Half a measurement is worth more than none."""
    use_case, chunks, judge = wired

    async def explode(**kw):
        raise TimeoutError("judge timed out")

    judge.judge = explode
    case = Case(
        case_id=CaseId.new(),
        question="What happened to revenue?",
        expected_answer="It rose.",
        expected_chunk_ids=frozenset({chunks[0].chunk_id}),
    )
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert run.results[0].failed
    assert "generation:" in (run.results[0].error or "")
    assert run.results[0].retrieval is not None


async def test_latency_is_recorded_per_case(wired):
    use_case, _, _ = wired
    case = Case(case_id=CaseId.new(), question="revenue?")
    run, _ = await final(use_case, EvaluationRequest(dataset=dataset(case), config=config()))
    assert run.results[0].latency_ms >= 0
