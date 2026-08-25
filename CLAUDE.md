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
