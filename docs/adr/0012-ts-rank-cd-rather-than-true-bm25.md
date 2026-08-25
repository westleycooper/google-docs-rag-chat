---
id: 0012
title: Use Postgres ts_rank_cd for lexical ranking rather than true BM25
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: vectorstore
tags: [retrieval, bm25, postgres, correction]
supersedes: []
superseded_by: []
---

# ADR-0012: Use Postgres ts_rank_cd for lexical ranking rather than true BM25

## Context

[ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md) describes
the lexical half of retrieval as "Postgres full-text BM25". That wording is
imprecise, and the imprecision surfaced while implementing the adapter.

Postgres core has no BM25. Its full-text search offers `ts_rank` and
`ts_rank_cd`, which are cosine-density ranking functions over weighted lexeme
positions. They are not Okapi BM25: they do not model term saturation via `k1`,
and they do not normalise by document length against a corpus average via `b`.
In practice this makes them less discriminating than BM25 on corpora with widely
varying document lengths.

Real BM25 in Postgres requires an extension — ParadeDB's `pg_search`
(`pg_bm25`) is the mature option — or a separate search engine.

This is worth an ADR rather than a silent substitution because a reader of
ADR-0004 would otherwise reasonably believe the system computes BM25, and would
be surprised by the ranking behaviour when it did not match.

## Decision

We will use `ts_rank_cd(search_vector, query, 32)` for lexical ranking, and treat
ADR-0004's "BM25" as shorthand for "lexical relevance ranking" rather than a
literal algorithm commitment.

The reason this is acceptable rather than merely convenient is specific:
**RRF consumes ranks, not scores** (ADR-0004). Fusion reads only the position a
retriever assigned, so the difference between BM25 and `ts_rank_cd` matters
exactly as much as it changes the *ordering* of the top ~50 candidates — and no
more. The score scale, which is where the two differ most, is discarded before
fusion ever sees it. This is the same property that made RRF the right fusion
choice in the first place, now paying a second dividend.

Normalisation flag `32` divides the rank by itself plus one, bounding the output
in `[0, 1)`. Not required by RRF, but it keeps the raw score meaningful when it
is surfaced in the trace for diagnostics.

The remaining exposure is length normalisation: a very long chunk can out-rank a
short, precisely-matching one in a way BM25's `b` parameter would damp. Chunking
bounds chunk length to `max_tokens` (default 512), so the variance this
depends on is small by construction — an incidental benefit of a decision taken
for embedding quality.

`plainto_tsquery` is used rather than `to_tsquery`, because user input reaches
this function directly and `to_tsquery` raises a syntax error on ordinary
punctuation. A chat box that returns a 500 for an apostrophe is not a search
feature.

## Consequences

### Positive

- No extension beyond `vector` and `pg_trgm`, so the schema deploys unchanged on
  Azure Database for PostgreSQL, AWS RDS, and Cloud SQL — all three of which
  allow `vector` but none of which offer `pg_search`. This directly preserves
  [ADR-0005](0005-terraform-multi-cloud-iac.md).
- Ranking is computed inside the same query, transaction and index as the rest of
  retrieval.
- The claim ADR-0004 makes is now accurate rather than approximately true.

### Negative

- Lexical ranking is weaker than BM25 on length-varied corpora. Chunking bounds
  this, but does not eliminate it.
- If lexical recall proves to be the pipeline's weak stage, fixing it means an
  extension or an external engine, not a parameter change.

### Neutral

- `ts_rank_cd` over `ts_rank`: the cover-density variant rewards query terms
  appearing near each other, which suits multi-term questions.

## Alternatives Considered

**ParadeDB `pg_search`.** Genuine BM25 inside Postgres, with a purpose-built
index. Rejected because it is unavailable on all three managed Postgres offerings
the platform targets, which would force a self-managed database and undo
ADR-0005's portability.

**Compute BM25 in the application from `ts_stat` term statistics.** Exact control
of `k1` and `b`. Rejected as a per-query cost and a second corpus statistic to
keep current, for a difference RRF largely discards.

**Elasticsearch or OpenSearch for the lexical half.** Best-in-class BM25.
Rejected on the same grounds as in ADR-0011: an entire additional service to
operate, secure and keep in sync with Postgres, for one half of a pipeline whose
precision comes from the cross-encoder.

**Amend ADR-0004 in place.** Would have been simpler than a new record. Rejected
because ADR-0004's reasoning is still sound and its decision unchanged; what was
wrong was one word of implementation detail, and rewriting an accepted decision
to hide that a detail was imprecise is exactly the rot a decision log exists to
prevent.
