---
id: 0009
title: Stream the LangGraph retrieval and reasoning trace to a visualised timeline
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [langgraph, streaming, observability, ux, citations]
supersedes: []
superseded_by: []
---

# ADR-0009: Stream the LangGraph retrieval and reasoning trace to a visualised timeline

## Context

Retrieval in Ragoogle is not one step. A turn runs query rewriting, two parallel
recall strategies, rank fusion, a cross-encoder rerank, and then generation
(see [ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md)), and
some questions need more than one retrieval round before an answer is possible.

Presented as a spinner followed by an answer, this is unfalsifiable. When the
answer is wrong the user cannot tell whether retrieval missed the document,
ranking buried it, or the model had the right chunk and misread it. Those are
three different bugs with three different fixes, and the product requirement for
*visible* sourcing is not satisfied by citations alone — a citation shows what was
used, not what was considered and rejected.

LangGraph was chosen for orchestration partly for this reason: its execution is
already a graph of discrete node transitions, so the trace is a real artefact of
the run rather than something reconstructed for display.

## Decision

We will stream each LangGraph node transition to the client over SSE and render
it as a **live trace timeline**, escalating to the shared Three.js canvas only
when the graph actually branches.

- Every node emits a typed event on entry and exit: node name, duration, inputs
  summarised, and a compact result — candidate counts for recall, score movement
  for fusion, the reordering for rerank. Events are the same records written to
  OpenTelemetry spans, so the user-facing trace and the operator-facing trace
  cannot disagree.
- Linear runs render as a DOM timeline. This is the common case and does not need
  3D.
- Multi-step runs — a re-query after weak recall, parallel sub-questions, a
  self-correction loop — render on the Three.js canvas as a directed graph, where
  showing branch, convergence and iteration simultaneously is the thing a timeline
  cannot do. This is the "where necessary" qualifier taken literally: the 3D view
  appears when the shape of the reasoning is genuinely non-linear, not on every
  turn.
- Rejected candidates stay inspectable. Seeing that the right document *was*
  retrieved and then ranked eighth is the single most diagnostic signal available
  when an answer is wrong, and it is invisible in any citations-only design.
- The trace is persisted with the turn, so it can be reopened later and replayed
  by the evaluation context rather than existing only as a live animation.

## Consequences

### Positive

- Retrieval failures become diagnosable by the person who noticed them, and
  distinguishable from generation failures.
- Perceived latency improves: a user watching recall and rerank complete is not
  watching a spinner, even though the wall-clock time is unchanged.
- One event stream serves the UI, the OTel spans, and the eval replay, so
  instrumentation is not duplicated three ways.

### Negative

- Every graph node must emit structured events, which is a discipline that decays
  the moment someone adds a node without them. This needs a test, not a habit.
- Persisting traces grows storage roughly linearly with turns, and traces contain
  document excerpts — so they inherit the corpus's confidentiality and its
  retention policy.
- Streaming intermediate state exposes internals; a badly-worded node name becomes
  user-visible copy.

### Neutral

- SSE rather than WebSockets: the stream is unidirectional and SSE survives
  proxies and reconnects with less machinery.

## Alternatives Considered

**Spinner, then answer with citations.** Conventional and cheapest. Rejected
against the explicit requirement that interrogation be visible, and because it
makes wrong answers undiagnosable.

**Trace in operator tooling only.** Send spans to OTel, expose nothing to users.
Rejected because the person best placed to notice that the wrong document was
retrieved is the one who knows the corpus — the user, not the operator.

**3D visualisation for every turn.** Visually consistent and avoids two renderers.
Rejected as gratuitous: a linear five-node pipeline in 3D is harder to read than
the same pipeline as a list, and it would spend GPU on every message.
