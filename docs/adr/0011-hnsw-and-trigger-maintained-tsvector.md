---
id: 0011
title: HNSW over IVFFlat, and a trigger-maintained tsvector
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: vectorstore
tags: [pgvector, indexing, postgres, recall]
supersedes: []
superseded_by: []
---

# ADR-0011: HNSW over IVFFlat, and a trigger-maintained tsvector

## Context

[ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md) settles
the retrieval *strategy* — dense and lexical recall, fused by RRF, reranked. It
does not settle how either index is built, and both choices have a defensible
alternative that behaves differently in ways that only show up later.

pgvector offers two index types. IVFFlat partitions vectors into lists around
centroids computed from the data present when the index is built. HNSW builds a
navigable small-world graph incrementally.

Separately, the `tsvector` column backing lexical search has to be populated by
something. The obvious choice is the ingestion code that writes the row.

## Decision

**HNSW, with `vector_cosine_ops`, `m = 16`, `ef_construction = 64`.**

IVFFlat's clustering is computed from the corpus at build time, which is a poor
fit here twice over. Ragoogle's index is created by a migration against an empty
database — there is no data to cluster — and the corpus then grows continuously
as sources are ingested, drifting away from whatever centroids were chosen. The
resulting recall decay is silent: queries keep returning results, they are just
increasingly not the right ones. HNSW builds incrementally and holds recall as
rows arrive, which matches how this corpus actually accumulates.

Cosine distance rather than inner product. `voyage-3-large` returns normalised
vectors, which makes the two rank-equivalent today; cosine is the one that stays
correct if a future provider behind the `EmbeddingProvider` port does not
normalise. Choosing the operator class that survives a provider swap costs
nothing now.

**The `tsvector` is maintained by a database trigger, not by application code.**

This is the more consequential half. A chunk written by a backfill script, a
migration, or a hand-run `psql` becomes searchable on exactly the same terms as
one written by the ingester. Lexical recall that depends on every writer
remembering to populate a column is lexical recall that will rot, and its failure
mode is the precise one hybrid retrieval exists to prevent: exact-match queries
quietly returning nothing while the system looks healthy.

Heading path is weighted `A` and body text `B`, so a query matching a section
title outranks the same term buried in prose.

## Consequences

### Positive

- Recall does not decay as the corpus grows, and the index is valid from the
  moment the migration runs.
- Every writer to `chunks` gets correct lexical indexing for free, including
  writers that do not exist yet.
- Both indexes live in the same table and the same transaction as the rows they
  index, so a chunk is never visible to one retriever and not the other.

### Negative

- HNSW builds are slower and use more memory than IVFFlat, and the cost is paid
  on every bulk ingest. At `m = 16` the graph also adds meaningful storage per
  vector.
- A trigger is invisible from the application: someone reading the ingestion code
  will not see the `tsvector` being populated and may reasonably conclude it is
  not. Mitigated by the comment in the migration and by an integration test that
  inserts a row without touching the column.
- Changing the text-search configuration (`english`) later means dropping and
  recreating the function, the trigger, and reindexing.

### Neutral

- `m = 16` / `ef_construction = 64` are pgvector's defaults. They are the right
  starting point; `ef_search` is the runtime knob to tune first if recall proves
  insufficient, and it needs no rebuild.
- On a small table the planner will prefer a sequential scan over the GIN index.
  That is correct at that size and not a misconfiguration — verified at 5,000
  rows, where a seq scan genuinely costs less.

## Alternatives Considered

**IVFFlat.** Faster to build, smaller on disk, and entirely reasonable for a
static corpus indexed once after loading. Rejected because this corpus is neither
static nor present at index-creation time, and its failure mode is silent recall
decay rather than an error.

**Application-side `tsvector` population.** Visible in the code that writes the
row, and one less piece of database machinery. Rejected because it makes correct
lexical search a property of every current and future write path rather than of
the table.

**A generated column instead of a trigger.** `GENERATED ALWAYS AS ... STORED`
would be declarative and harder to bypass. Rejected because Postgres requires the
expression to be immutable, and `to_tsvector` is only immutable when the
configuration is passed as a literal in a form that also forbids the
`setweight`/`||` composition the heading weighting needs.

**A separate search engine.** Elasticsearch or OpenSearch would give better BM25
than Postgres full-text. Rejected as a whole additional service to operate,
secure and keep in sync, in exchange for an improvement to one half of a pipeline
whose precision comes from the reranker.
