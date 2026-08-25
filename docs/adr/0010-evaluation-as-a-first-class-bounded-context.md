---
id: 0010
title: Evaluation as a first-class bounded context, configurable from the UI
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [evals, quality, langgraph, regression]
supersedes: []
superseded_by: []
---

# ADR-0010: Evaluation as a first-class bounded context, configurable from the UI

## Context

Every decision in this log that touches retrieval quality — the embedding model
([ADR-0002](0002-voyage-3-large-as-default-embedding-model.md)), the fusion and
rerank strategy ([ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md)),
chunking, the prompt — is a claim that one configuration retrieves better than
another. Without measurement those claims are unfalsifiable, and the system
regresses silently: a chunking tweak that improves three queries and breaks
thirty looks exactly like a chunking tweak that works.

The requirement is that users manage evals from the config page, which means
evaluation cannot be a developer script in a repo. It has to be a domain concept
with datasets, runs, and scores that a non-engineer can create and read.

The distinguishing property this system has is that real traffic is replayable:
sessions are server-authoritative
([ADR-0007](0007-client-cached-session-context-hydrated-at-inception.md)) and
every turn persists its full retrieval trace
([ADR-0009](0009-stream-and-visualise-the-retrieval-reasoning-trace.md)). A bad
answer a user actually received can become a test case, which is a far better
source of eval data than questions invented against a corpus.

## Decision

We will model **Evaluation as its own bounded context** with `Dataset`, `Case`,
`Run`, and `Score` as aggregates, exposed through the configuration UI.

- A `Case` is a question, an optional expected answer, and — critically — the set
  of document chunks that *should* be retrieved. That last part lets retrieval be
  scored independently of generation, which is what makes a regression
  attributable to a specific stage rather than to "the system".
- Metrics span both stages: recall@k, MRR and nDCG over the retrieval trace;
  faithfulness, answer relevance and citation correctness over the generated
  answer, judged by Claude as an LLM-judge with the rubric stored alongside the
  dataset so a score is reproducible and its criteria auditable.
- Promotion from traffic: any turn can be promoted to a `Case` from the chat UI
  in one action, carrying its trace with it. This is the pipeline that keeps
  datasets grounded in real failures.
- A `Run` pins the full configuration it executed against — embedding model and
  dimension, retrieval weights, rerank on/off, chat model, prompt version — so two
  runs are comparable and a score is never orphaned from what produced it.
- Runs execute as a LangGraph graph, reusing the same nodes as live traffic
  rather than a parallel evaluation path. An eval that does not exercise the
  production graph measures something else.

## Consequences

### Positive

- Configuration changes become measurable rather than argued, and the decisions in
  this log become testable claims.
- Regressions are attributed to a stage, because retrieval and generation are
  scored separately.
- Datasets grow from real failures instead of imagined queries.

### Negative

- LLM-judged metrics cost inference per case per run, so a large dataset makes
  a full run genuinely expensive, and cost scales with the thing we want to
  encourage (running evals often).
- An LLM judge is itself a model that can be wrong or drift between versions;
  pinning the judge model and rubric mitigates but does not remove this.
- Promoting real turns into datasets copies document excerpts into eval storage,
  extending the corpus's confidentiality boundary.

### Neutral

- Datasets are versioned; editing a case forks rather than mutates, so historical
  runs stay interpretable.

## Alternatives Considered

**A pytest suite of fixture questions.** Cheap, in CI, familiar. Rejected as it
fails the stated requirement — users cannot manage it — and fixture questions
drift away from what the corpus is actually asked.

**An external eval platform.** Better tooling than we will build, and no
maintenance. Rejected because the promotion-from-traffic flow is the core value
here, and it depends on traces that live in our database.

**Generation-only scoring.** Half the work for most of the signal. Rejected
because it cannot distinguish "the retriever never found it" from "the model
ignored it", which is exactly the distinction that makes a regression fixable.
