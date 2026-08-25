---
id: 0002
title: Use Voyage voyage-3-large as the default embedding model
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: rag-core
tags: [embeddings, retrieval, vendor, cost]
supersedes: []
superseded_by: []
---

# ADR-0002: Use Voyage voyage-3-large as the default embedding model

## Context

Retrieval quality in a RAG system is bounded above by the embedding model: the
generator cannot cite a chunk the retriever never surfaced. The corpus is Google
Drive content — business and technical documents, mixed prose and tables, mostly
English, individual documents ranging from a paragraph to tens of pages.

Four candidates were put to the decision-maker with their trade-offs:

| Option | Dims | Hosting | Notes |
|---|---|---|---|
| Voyage `voyage-3-large` | 256/512/**1024**/2048 | API | Anthropic's recommended embedding partner; 32k context; int8 + binary quantization |
| OpenAI `text-embedding-3-large` | 3072 (reducible) | API | Widely deployed; adds a second vendor next to Anthropic |
| BAAI `bge-m3` | 1024 | Self-hosted | No per-token cost, no data egress; emits sparse vectors natively; ~2GB RAM |
| Google `gemini-embedding-001` | 3072 (truncatable) | Vertex AI | One cloud credential shared with Drive; ties default path to GCP |

The decision also fixes the `vector(n)` column width in Postgres, so it is not
free to defer: changing it later is a re-embed of the entire corpus plus an index
rebuild.

## Decision

We will use **Voyage `voyage-3-large` at 1024 dimensions** as the default
embedding model, behind an `EmbeddingProvider` port with adapters for all four
candidates.

1024 is chosen over the 2048 and 256 Matryoshka points deliberately. `voyage-3-large`
is trained so that truncated prefixes remain valid embeddings, and 1024 sits at
the knee of the curve: it retains effectively all of the retrieval quality of the
full 2048 while halving both the pgvector index size and the distance-computation
cost. HNSW build time and memory scale linearly in dimensionality, and this is the
single largest lever on vector-search latency we control.

The dimension is a configuration value, not a constant. The migration that
creates the embeddings table reads it, and a startup check refuses to serve if
the configured provider's output dimension disagrees with the deployed column —
failing loudly at boot rather than silently writing truncated vectors.

## Consequences

### Positive

- Best-in-class retrieval quality on the document types this corpus actually
  contains, from the vendor Anthropic recommends for Claude-based RAG.
- 32k input context means long Drive documents chunk on semantic boundaries
  rather than being forced small by the embedder's window.
- Quantization support (int8, binary) is a ready lever if the corpus grows past
  what full-precision HNSW holds comfortably in memory.

### Negative

- A second API vendor and a second key (`VOYAGE_API_KEY`) to provision, rotate,
  and budget for, on top of Anthropic.
- Document text leaves our infrastructure to be embedded. For a Drive corpus that
  may contain confidential material this is a real disclosure surface, and it is
  the reason the self-hosted adapter is built rather than merely contemplated.
- Ingestion throughput is now bounded by a third-party rate limit.

### Neutral

- ~$0.18/M tokens. At this corpus size embedding cost is dominated by chat
  inference cost; it becomes material only on full re-embeds.

## Alternatives Considered

**OpenAI `text-embedding-3-large`.** Strong and ubiquitous, marginally cheaper.
Lost on quality for this corpus type and because it introduces a third model
vendor without displacing either of the first two.

**Self-hosted `bge-m3`.** The only option where document text never leaves our
infrastructure, and its native sparse vectors would have given hybrid search for
free. Lost as the *default* on retrieval quality and ingest throughput, but it is
the recommended configuration for a confidential corpus and ships as a supported
adapter with its own Docker service — this is a default, not a lock-in.

**Google `gemini-embedding-001`.** Appealing for credential and billing
consolidation given documents already live in Google Drive. Lost because it
couples the default retrieval path to GCP, which directly contradicts the
requirement that the platform deploy to Azure, AWS, and GCP alike
(see [ADR-0005](0005-terraform-multi-cloud-iac.md)).
