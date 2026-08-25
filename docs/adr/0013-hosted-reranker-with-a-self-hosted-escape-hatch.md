---
id: 0013
title: Voyage rerank-2.5 as the default cross-encoder, with a self-hosted escape hatch
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [rerank, latency, vendor, confidentiality]
supersedes: []
superseded_by: []
---

# ADR-0013: Voyage rerank-2.5 as the default cross-encoder, with a self-hosted escape hatch

## Context

[ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md) commits to
a cross-encoder rerank as retrieval's third stage but does not say where the
model runs. Implementing the adapter forced the question, and it is not the same
question as the embedding one in
[ADR-0002](0002-voyage-3-large-as-default-embedding-model.md) even though it
looks like it.

Two things make reranking different from embedding:

- **It cannot be precomputed.** Embeddings are computed once at ingest and
  indexed. A cross-encoder scores (query, passage) pairs jointly, which is
  exactly why it outperforms vector similarity — and exactly why every score
  must be computed at query time, on the user's critical path.
- **Every query sends corpus text to the model.** Embedding sends document text
  once, at ingest. Reranking sends the retrieved passages on *every single
  query*, which multiplies the disclosure surface by query volume rather than by
  corpus size.

The candidates: a hosted API (Voyage `rerank-2.5`, Cohere Rerank), or a local
cross-encoder (`bge-reranker-v2-m3`, `ms-marco-MiniLM`) run in-process or in a
sidecar.

## Decision

We will use **Voyage `rerank-2.5`** by default, behind the existing `Reranker`
port, and treat a self-hosted adapter as a supported configuration rather than a
hypothetical one.

The default is Voyage for three reasons that are about deployment rather than
quality. The key is already provisioned for embeddings, so it adds no new vendor
relationship. A local cross-encoder means shipping torch and a model download
into every API container, which turns a ~200MB image into a multi-gigabyte one
and adds a cold-start penalty to a component we expect to scale horizontally.
And reranking is bursty in a way ingestion is not — it fires on every query — so
capacity for a local model has to be provisioned for peak query rate rather than
for average ingest throughput.

Two behaviours are deliberate in the adapter. An oversized candidate set is
**truncated with a warning rather than rejected**: reranking the best 1000 of
1200 candidates is a far better outcome for the user than an error, and the
warning is what tells an operator the candidate limit is misconfigured. And
scores are **clamped into [0, 1]** rather than trusted, because the value is
rendered to users as citation relevance and an out-of-range score would
otherwise fail `Citation`'s invariant deep in the response path instead of at
the boundary where it can be explained.

The escape hatch matters as much as the default. For a confidential corpus the
per-query disclosure above is disqualifying, and the answer there is the same as
in ADR-0002: self-host. The port makes that an adapter swap.

## Consequences

### Positive

- No new vendor, no new key, no model weights in the image.
- Reranking capacity scales with the API rather than being provisioned for peak.
- The port keeps the self-hosted path open without speculative work now.

### Negative

- A network round-trip lands on the critical path of every query, so rerank
  latency is now a function of someone else's availability as well as their
  compute. This is the single largest addition to time-to-first-token.
- Retrieved corpus text leaves our infrastructure on every query, not once per
  document. For a confidential corpus this is the decisive objection, and it is
  why the self-hosted adapter is a supported configuration rather than a
  footnote.
- A Voyage outage degrades retrieval to fused RRF order. Acceptable because the
  degradation is graceful and reported in the turn's `degraded` list — but it is
  a real reduction in answer quality that users will notice before operators do.

### Neutral

- One vendor now serves both embedding and reranking, which concentrates
  dependency but also concentrates the mitigation: the same decision to
  self-host addresses both.

## Alternatives Considered

**A local cross-encoder as the default** (`bge-reranker-v2-m3`). No per-query
egress, no third-party latency, no per-query cost. Rejected as the *default* on
deployment cost — torch in every container, GPU or a large CPU allocation, and
cold starts on a horizontally-scaled component — not on quality. It remains the
recommendation for a confidential corpus, and the port exists so that is a
configuration change rather than a rewrite.

**Cohere Rerank.** Comparable quality and maturity. Rejected only because it
would add a third vendor to a system that already holds a Voyage key for
embeddings; nothing about it is worse.

**No reranker, relying on fused RRF order.** Already the graceful-degradation
path, and genuinely the right configuration under a tight latency budget — this
is the fallback ADR-0004 named. Rejected as the default because top-of-list
precision is what determines whether the *cited* source is the right one, which
is the product requirement that motivated hybrid retrieval in the first place.
