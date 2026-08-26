---
id: 0001
title: Structure RAGDrive as a DDD hexagonal monorepo
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: platform
tags: [architecture, ddd, monorepo]
supersedes: []
superseded_by: []
---

# ADR-0001: Structure RAGDrive as a DDD hexagonal monorepo

## Context

RAGDrive is a RAG chat platform that interrogates documents ingested from Google
Drive, with an explicit requirement that the ingestion pipeline serve providers
beyond Google. Several parts of the system are known to be volatile from day one:

- the **embedding model** (see [ADR-0002](0002-voyage-3-large-as-default-embedding-model.md)) is a vendor choice that will be re-evaluated as
  benchmarks move;
- the **document source** is Google Drive today and explicitly must not be the
  only one tomorrow;
- the **chat model** is user-selectable at runtime across the Claude family;
- the **retrieval strategy** (see [ADR-0004](0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md)) mixes two search modes and a reranker, each
  independently swappable.

Every one of those is an *infrastructure* concern wearing a *domain* costume. If
they are allowed to reach into request handlers and business logic, swapping any
one of them becomes a cross-cutting rewrite. The requirement for enforced domain
rules and full unit-test coverage points the same way: the rules need somewhere
to live that has no I/O in it.

## Decision

We will structure the platform as a monorepo of deployable apps over a set of
bounded contexts, each context following ports-and-adapters (hexagonal) layering.

```
apps/
  api/            FastAPI composition root + HTTP adapters
  frontend/       React 19 + MUI chat client
  observability/  Three.js live topology app
packages/
  ragoogle-core/  domain + application layers (zero I/O imports)
  ragoogle-infra/ adapters: pgvector, Drive, Voyage, Anthropic, OTel
tools/            adr, codegen, quality gates
infra/            Terraform per cloud
```

Bounded contexts: **Ingestion** (sources, permissions, extraction, chunking),
**Retrieval** (embedding, search, ranking, citation), **Conversation**
(LangGraph orchestration, model selection, message history), **Evaluation**
(datasets, runs, scores), **Configuration** (source registration, credentials).

The layering rule is enforced mechanically, not by convention: the domain layer
may import only from the standard library and its own context; the application
layer may import domain and ports; only adapters may import a vendor SDK. A
quality gate walks the import graph and fails the build on any violation, so the
rule is a test rather than a code-review habit.

## Consequences

### Positive

- Swapping the embedding vendor, the vector store, or a document source is an
  adapter change behind an unchanged port, with the domain tests untouched.
- The domain layer has no I/O, so full unit-test coverage of business rules is
  achievable without fixtures, containers, or network mocks.
- The ingestion pipeline generalises to non-Google providers by construction —
  `DocumentSource` is a port from the first commit rather than a later refactor.

### Negative

- More indirection than a flat FastAPI app: a single feature touches a port, an
  adapter, and a use case. This is real friction on small changes.
- The import-graph gate will occasionally reject a pragmatic shortcut, and
  someone will want to disable it. The gate is only worth having if it stays on.

### Neutral

- Monorepo means one version, one CI pipeline, and coordinated releases across
  three deployable apps. Acceptable at this size; revisit if the apps diverge in
  release cadence.

## Alternatives Considered

**Flat FastAPI service with routers and a services module.** Fastest to first
working chat, and the shape most RAG tutorials use. Rejected because the
vendor-swappability requirement is load-bearing here, and retrofitting ports onto
a service layer that already imports SDKs directly is exactly the rewrite this
decision exists to avoid.

**Separate repositories per bounded context.** Would give genuinely independent
release cadence and harder boundaries. Rejected as premature: the contexts share
a schema and a release train today, and cross-repo changes would dominate the
early build.

**Domain layer with an ORM-backed active record.** Simpler persistence story.
Rejected because it puts SQLAlchemy inside the domain, which is precisely the
import the enforcement gate exists to forbid, and it makes the aggregate rules
untestable without a database.
