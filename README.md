# RAGDrive

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
     `drive.readonly`. Paste the JSON key and name the subject to impersonate;
     the corpus is whatever that person can see. Best for a shared drive.
   - **OAuth** — click **Connect Google Drive** and consent on Google's own
     screen. Works for personal Gmail, needs no admin, and fills in the
     principal for you. Requires a one-time Google Cloud Console setup — see
     `RAGOOGLE_GOOGLE_OAUTH_CLIENT_ID` in `.env.example` for the steps — and is
     unavailable (with a clear error, not a broken redirect) until that's done.
3. Either way, the credential is encrypted before it touches the database; no
   endpoint ever reads it back.
4. Click **Browse** to pick root folders from the connected Drive, or paste a
   folder id directly — the picker only reaches My Drive, so a Shared Drive
   folder needs the paste. Leaving it empty ingests the whole Drive.
5. Press ▶ to ingest. A source can be edited afterwards — reconnect Drive,
   rotate a credential, or change its folders — without deleting it and losing
   ingestion history.

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
RAGDrive shows what is in the window, what it costs, and — before the next
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

Vendor smoke tests are separate, because they cost money and need network:

```bash
uv run python tools/smoke/vendors.py     # real Voyage + Claude calls
```

They verify what stubs cannot: that the vendors agree with the shape the
adapters expect. Both bugs they have caught so far — a structured-output schema
the API rejects, and a rate limit the retry policy could not clear — were
invisible to every unit test.

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
| [ADR-0016](docs/adr/0016-oauth-consent-flow-and-drive-readonly-over-drive-file-for-dr.md) | Why `drive.readonly` over the narrower `drive.file`, and how credentials are decoupled from sources |

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

## Ingesting a local folder

Google Drive is not the only source. `LocalDirectorySource` implements the same
port, which is what makes ADR-0001's "not just Google Docs" claim checkable
rather than aspirational — a port with one implementation is a guess about a
boundary.

```bash
uv run python tools/ingest/local.py ./docs/adr --name "Architecture decisions"
```

Same use case, same chunker, same embedding provider, same vector store as a
Drive run. Only the `DocumentSource` differs. Unreadable files are skipped with
an audit record naming the OS user, exactly as a denied Drive folder is.

## Rate limits

One question costs two Voyage calls (embed + rerank), and ingestion costs one
per batch. Both are retried with backoff — but the backoff has a **delay floor**
rather than jittering from zero, because a vendor rate limit is usually a budget
over a *window*, and a retry inside a spent window cannot succeed. The reasoning
is in [ADR-0014](docs/adr/0014-backoff-with-a-delay-floor-for-windowed-rate-limits.md).

On a Voyage **free-tier** key (10,000 tokens/minute) a 50-candidate rerank
exceeds the budget in a single request, which no retry policy can rescue. Set
`RAGOOGLE_CANDIDATE_LIMIT=12` or add a payment method.

## Current limitations

Stated plainly, because a README that only lists what works is not much use:

- **Terraform is validated, not applied.** All six targets pass `validate`
  against real provider schemas. Nothing has been deployed to a live cloud.
- **Vendor calls are exercised on demand, not in CI.**
  `tools/smoke/vendors.py` makes real Voyage and Claude calls and passes, but it
  costs money and needs network, so it is not part of `check.sh`.
- **The reranker is hosted, not self-hosted.** Chunk text leaves your
  infrastructure to be reranked. For a confidential corpus that matters, and
  [ADR-0013](docs/adr/0013-hosted-reranker-with-a-self-hosted-escape-hatch.md)
  records the escape hatch.
- **One retrieval round per question.** The trace can express branching and the
  UI escalates to a graph view when it sees it, but no graph node currently
  re-queries after weak recall.
