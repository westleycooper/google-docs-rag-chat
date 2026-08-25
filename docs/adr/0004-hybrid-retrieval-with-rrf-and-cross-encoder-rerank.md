---
id: 0004
title: Hybrid dense/BM25 retrieval fused with RRF and a cross-encoder rerank
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [retrieval, pgvector, ranking, citations]
supersedes: []
superseded_by: []
---

# ADR-0004: Hybrid dense/BM25 retrieval fused with RRF and a cross-encoder rerank

## Context

The product requirement is not merely a plausible answer — it is a *visibly
sourced* one, with document icons and references back to the originating Drive
file. That raises the bar on retrieval twice over: the right chunk must be in the
candidate set, and it must be ranked highly enough to be cited rather than merely
retrieved.

Dense vector search alone has a well-known and, for this corpus, disqualifying
weakness. Business documents are full of tokens that carry meaning without
carrying semantics: invoice numbers, project codenames, ticket ids, surnames,
version strings. Embeddings smear exactly those. A user asking about `PRJ-4471`
gets chunks that are *about the same sort of thing* rather than the chunk that
contains the string.

Postgres already ships the complement: `tsvector` full-text search, which is
precise on exactly those tokens and weak where dense search is strong. Both can
live in the same table, in the same transaction, with no additional service.

## Decision

We will implement three-stage retrieval:

1. **Recall.** Run pgvector HNSW cosine search and Postgres BM25 full-text search
   in parallel, each returning ~50 candidates.
2. **Fusion.** Combine with Reciprocal Rank Fusion, `score = Σ 1/(k + rank_i)`
   with `k = 60`. RRF is chosen over weighted score blending because dense cosine
   distance and BM25 relevance are not on a common scale, and any fixed weighting
   between them is a magic number that silently rots as the corpus changes. RRF
   consumes only ranks, so it needs no normalisation and no tuning.
3. **Precision.** Cross-encoder rerank the fused top ~50 down to the top ~8 that
   actually enter the prompt. A cross-encoder attends over query and passage
   jointly rather than comparing two independently-computed vectors, which is why
   it can tell "mentions the topic" from "answers the question" — the distinction
   that decides whether a citation is right or merely relevant.

Each stage is a port with its own adapter, and each is independently disableable
by configuration so the cheaper strategies remain live options per deployment.

## Consequences

### Positive

- Exact-match queries (ids, names, codes) and conceptual queries both work, which
  dense-only cannot deliver and which this corpus demands.
- Citation precision improves specifically because reranking optimises the *top*
  of the list, and the top is what gets shown to the user with an icon next to it.
- The BM25 half costs no new infrastructure — it is a GIN index in the database
  that is already there.

### Negative

- ~150ms added per query for the rerank stage. This is the single largest
  contributor to chat time-to-first-token and will be the first thing to
  scrutinise under a latency budget.
- A third model in the serving path, with its own memory footprint, failure mode,
  and warm-up cost.
- Two indexes per corpus (HNSW + GIN) to build and keep current on ingest.

### Neutral

- `k = 60` in RRF is the value from the original Cormack et al. formulation and is
  notably insensitive; it is exposed as configuration but is not expected to be
  a tuning target.

## Alternatives Considered

**Hybrid without the reranker.** Captures most of the win — exact-match recall is
the big jump — with no extra model, no extra latency, and no warm-up. This is a
genuinely close second and is the configuration to fall back to if the latency
budget tightens. It loses on the specific requirement that drove the decision:
top-of-list precision is what determines whether the cited source is the right
one.

**Dense vector only.** Simplest, fastest, one index. Rejected on the exact-match
failure above, which is not a corner case for business documents but a routine
query shape.

**Weighted score blending instead of RRF.** Would allow deliberately favouring one
retriever. Rejected because it requires normalising two incomparable scales and
hard-codes a weight that no one will revisit as the corpus drifts.
