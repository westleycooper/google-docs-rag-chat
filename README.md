# Ragoogle

RAG chat over Google Drive and other document sources, with a selectable Claude
model, pgvector retrieval, and a live architecture view.

Answers cite the documents they rest on, and show **how** those documents were
found — because a citation tells you what was used, not what was considered and
rejected, and that difference is what makes a wrong answer diagnosable.

---

## What it looks like

### Chat

Ask a question; watch retrieval happen; get an answer with its sources.

![Chat](docs/screenshots/chat.png)

> Shown here with no `ANTHROPIC_API_KEY` configured, which is why the model
> picker says so. With a key, each turn streams its retrieval trace, a row of
> source chips, and a live context meter in the right-hand panel.

### Configuration

Register document sources, see what the last ingestion run **could not read**,
and manage evaluation datasets.

![Configuration](docs/screenshots/configuration.png)

The skip list is the point. A folder the ingester was denied is reported with
the principal it was denied to — because a silent skip is indistinguishable from
an empty folder, and that ambiguity is how a RAG system ends up confidently
telling you a document does not exist.

### Architecture

The running system, polled live, with the decisions that shaped each component.

![Architecture](docs/screenshots/observability.png)

Selecting a node lists the ADRs that constrain it. "Why is this component like
this?" is answered in the same place as "is it up?".

---

## Quick start

```bash
git clone <this repo> && cd google-docs-rag-chat
cp .env.example .env          # then fill in the two API keys
docker compose up --build
```

| | |
|---|---|
| Chat | http://localhost:5173 |
| Architecture | http://localhost:5174 |
| API docs | http://localhost:8000/docs |

You need two keys in `.env`:

- **`VOYAGE_API_KEY`** — embeddings ([get one](https://dashboard.voyageai.com/))
- **`ANTHROPIC_API_KEY`** — chat, token counting, the eval judge
  ([get one](https://console.anthropic.com/settings/keys))

and one generated secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # RAGOOGLE_CREDENTIAL_SECRET
```

Without that last one the API **refuses** to store or use Google credentials
rather than holding them in plaintext, so ingestion will not start.

### Connecting a Drive

1. Open **Configuration → Add source**.
2. Choose an authentication mode:
   - **Service account** — a Workspace admin grants domain-wide delegation for
     `drive.readonly`. Ingestion acts as the subject you name, and the corpus is
     whatever that person can see. Best for a shared drive.
   - **OAuth** — you consent directly. Works for personal Gmail, needs no admin.
3. Paste the credential. It is encrypted before it touches the database and
   there is no endpoint that reads it back.
4. Press ▶ to ingest.

Permission failures never fail the run. They are recorded, and shown to you.

---

## How it works

```
Google Drive ──► Ingestion ──► chunks + vectors ──► Postgres/pgvector
                     │                                    │
                     └── skips (audited) ─────────────┐   │
                                                      ▼   ▼
  question ──► dense search ─┐                     Configuration UI
              lexical search ─┴─► RRF ─► rerank ─► Claude ─► answer + citations
                     │                                         │
                     └──────────── trace ──────────────────────┘
```

**Retrieval is hybrid.** Dense pgvector search finds things that *mean* the same;
Postgres full-text finds things that *say* the same. Business documents are full
of tokens that carry meaning without carrying semantics — invoice numbers,
project codes, surnames — and embeddings smear exactly those. Ask about
`PRJ-4471` and dense search alone returns documents about *that sort of thing*
rather than the one containing the string.

The two are fused with Reciprocal Rank Fusion, which reads only *rank*, never
score. Cosine distance and text relevance are not on a common scale, so any
arithmetic mixing their raw values hides a weighting that rots as the corpus
changes.

A cross-encoder then reranks the top ~50 down to the ~8 that enter the prompt.
It reads query and passage together, which is why it can tell "mentions the
topic" from "answers the question".

**The context window is visible and yours to manage.** Long RAG conversations
fill the window, something falls out, and the assistant starts answering as
though a document it cited three turns ago never existed. No error is raised.
Ragoogle shows what is in the window, what it costs, and — before the next
turn — exactly what would be pushed out, so you can drop something else instead.

---

## Repository layout

```
apps/
  api/              FastAPI: HTTP adapters and the composition root
  frontend/         React 19 + MUI chat client
  observability/    Three.js architecture view
packages/
  ragoogle-core/    domain + application. Standard library only, by design
  ragoogle-infra/   adapters: pgvector, Drive, Voyage, Claude — the only layer
                    permitted a vendor SDK
contracts/          openapi.json — committed, and the frontend's codegen input
docs/adr/           every architectural decision, in MADR format
infra/              Terraform for Azure, AWS and GCP
tools/              ADR generator, quality gates, OpenAPI export
```

The layering rule is mechanical, not cultural: `tools/quality/layering.py` walks
the AST of `ragoogle-core` and fails on any non-stdlib import, naming the port
the SDK belongs behind. It runs on every edit via a hook, so a violation blocks
at authoring time rather than in review.

---

## Development

```bash
uv sync --all-packages
pnpm install --dir apps/frontend
pnpm install --dir apps/observability

docker compose up -d postgres
export RAGOOGLE_TEST_DATABASE_URL=postgresql://ragoogle:ragoogle@localhost:5433/ragoogle
uv run alembic upgrade head

./tools/quality/check.sh      # every gate, in the order CI runs them
```

The gates: ADR index freshness · layering · OpenAPI contract freshness · ruff
lint and format · mypy `--strict` on all three Python packages · pytest at
**100% branch coverage** on the domain · Terraform fmt and validate across six
targets · frontend codegen freshness, `tsc`, ESLint and vitest · integration
tests against real Postgres.

Integration tests and the Terraform gate skip cleanly when Postgres or Docker
are absent, so a fresh checkout still gets a meaningful signal.

Postgres runs on **5433** to avoid colliding with a local install.

### Decisions

Every architectural choice with a defensible alternative is recorded in
[`docs/adr/`](docs/adr/).

```bash
python3 tools/adr/adr.py new "Title of the decision" --component rag-core
python3 tools/adr/adr.py list
```

The `component` field comes from a fixed vocabulary that matches the node names
in the architecture view — that is how a decision gets rendered against the
running component it constrains. `docs/adr/index.json` is generated; a hook
rebuilds it and blocks on invalid frontmatter, dangling ADR links, or a
`supersedes` without its matching `superseded_by`.

Some decisions worth reading first:

| | |
|---|---|
| [ADR-0002](docs/adr/0002-voyage-3-large-as-default-embedding-model.md) | Why Voyage `voyage-3-large` at 1024 dimensions |
| [ADR-0003](docs/adr/0003-dual-mode-google-drive-authentication.md) | Why a 403 is a skip with an audit record, never a run failure |
| [ADR-0004](docs/adr/0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md) | Why hybrid retrieval, and why RRF over weighted blending |
| [ADR-0008](docs/adr/0008-threejs-context-budget-with-user-directed-truncation.md) | Why the context window is visible and truncatable |
| [ADR-0012](docs/adr/0012-ts-rank-cd-rather-than-true-bm25.md) | Where ADR-0004 was imprecise, and why the correction is acceptable |

---

## Deploying

Terraform for three clouds, all satisfying one contract
([`infra/CONTRACT.md`](infra/CONTRACT.md)):

```bash
cd infra/envs/azure-dev          # or aws-dev, gcp-dev
cp terraform.tfvars.example terraform.tfvars
terraform init && terraform apply
```

Every module provisions managed Postgres with pgvector on a private endpoint, a
container runtime, a KMS-backed key for credential encryption, an object store,
and a telemetry sink — and emits the same outputs, so application configuration
is generated identically wherever it runs.

---

## Current limitations

Stated plainly, because a README that only lists what works is not much use:

- **No cross-encoder reranker adapter yet.** The port and configuration exist;
  retrieval currently degrades to fused RRF order and says so in each turn's
  `degraded` list rather than hiding it.
- **Google Drive is the only source adapter.** The `DocumentSource` port has no
  Drive-specific vocabulary, so a second provider is an adapter rather than a
  refactor — but nobody has written one.
- **Vendor calls are unexercised by CI.** The Voyage and Claude adapters are
  type-checked and unit-tested against fakes; no test makes a real API call.
- **Terraform is validated, not applied.** All six targets pass `validate`
  against real provider schemas. Nothing has been deployed to a live cloud.
