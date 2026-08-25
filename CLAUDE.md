# Ragoogle

RAG chat platform over Google Drive (and other document sources), with a
selectable Claude model, pgvector retrieval, and a live architecture
observability app.

## Architecture

DDD hexagonal monorepo — see [ADR-0001](docs/adr/0001-ddd-hexagonal-monorepo-for-ragoogle.md).
The layering rule is enforced mechanically: the domain layer imports only the
standard library, the application layer imports domain and ports, and **only
adapters may import a vendor SDK**. If you need a vendor SDK in the domain
layer, the design is wrong, not the rule.

## Quality gates

```bash
./tools/quality/check.sh   # every gate, in the order CI runs them
```

ADR index freshness · layering · ruff lint · ruff format · mypy --strict ·
pytest at **100% branch coverage** (`--cov-fail-under=100`, not a target).

Integration tests need a live Postgres and skip without one:

```bash
docker compose up -d postgres
export RAGOOGLE_TEST_DATABASE_URL=postgresql://ragoogle:ragoogle@localhost:5433/ragoogle
uv run --python 3.12 --no-project --with alembic --with "psycopg[binary]" \
  --with pgvector --with sqlalchemy alembic upgrade head
./tools/quality/check.sh
```

They verify what no fake can: the HNSW index is cosine, the tsvector trigger
fires without the application, CHECK constraints reject bad rows, and cascades
reach chunks. Postgres runs on **5433** to avoid colliding with a local install.

`tools/quality/layering.py` walks the AST of `ragoogle-core` and fails on any
non-stdlib import, naming the port the SDK belongs behind. A `PostToolUse` hook
runs it on every edit under `packages/ragoogle-core/`, so a violation blocks at
authoring time rather than in CI.

Python is **3.12** (`.python-version`), managed by uv. `uv sync` for the full
workspace; the gates run on ephemeral `uv run --no-project` envs so they work on
a clean checkout.

## Decision records

All architectural decisions live in `docs/adr/` in MADR format.

```bash
python3 tools/adr/adr.py new "Title of the decision" --component rag-core
python3 tools/adr/adr.py index          # regenerate docs/adr/index.json
python3 tools/adr/adr.py index --check  # CI gate: fail if stale or invalid
python3 tools/adr/adr.py list
```

`docs/adr/index.json` is generated — never hand-edit it. A `PostToolUse` hook
rebuilds it whenever an ADR changes, and blocks on invalid frontmatter, dangling
ADR links, or a `supersedes` without the matching `superseded_by`.

**Make a new ADR whenever a choice has a defensible alternative.** The
`component` field must come from the fixed vocabulary in `adr.py`, because it
maps onto nodes in the observability topology graph — that is how decisions get
rendered against the running component they constrain.

## Commits

A `Stop` hook auto-commits completed work with a Conventional Commits message
(see [ADR-0006](docs/adr/0006-auto-commit-and-adr-as-code.md)). Type and scope
are derived from the changed paths.

**Write `.claude/COMMIT_MSG` before finishing any substantive piece of work.**
First line is the subject (`type(scope): summary`), the rest is the body. The
hook consumes and deletes it. Without it you get an accurate but bland
`feat(api): add 4 files` — fine for incidental changes, poor for real ones.

The hook never pushes, refuses to run mid-rebase/merge, and aborts if a
credential-shaped path (`.env`, `*.pem`, `*-key.json`, `*credentials*.json`)
is present in the working tree. Set `RAGOOGLE_AUTOCOMMIT=0` to disable.

## Settled decisions

Do not re-litigate these without a superseding ADR:

- **Embeddings**: Voyage `voyage-3-large` @ 1024 dims, behind an
  `EmbeddingProvider` port ([ADR-0002](docs/adr/0002-voyage-3-large-as-default-embedding-model.md))
- **Drive auth**: both service-account DWD and OAuth, per source; 403 is a
  *skip with an audit record*, never a run failure ([ADR-0003](docs/adr/0003-dual-mode-google-drive-authentication.md))
- **Retrieval**: dense + BM25 → RRF (k=60) → cross-encoder rerank ([ADR-0004](docs/adr/0004-hybrid-retrieval-with-rrf-and-cross-encoder-rerank.md))
- **Session context**: server-authoritative, Redux is a hydrated projection —
  the client proposes, the server disposes ([ADR-0007](docs/adr/0007-client-cached-session-context-hydrated-at-inception.md))
