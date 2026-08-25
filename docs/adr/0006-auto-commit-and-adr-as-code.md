---
id: 0006
title: Enforce a conventional-commit auto-commit hook and ADR-as-code
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: tooling
tags: [git, hooks, adr, governance, observability]
supersedes: []
superseded_by: []
---

# ADR-0006: Enforce a conventional-commit auto-commit hook and ADR-as-code

## Context

This platform is being built largely through agent-driven sessions. Two failure
modes follow from that and both are about losing information:

- **Uncommitted work accumulates.** A long session produces a large, unattributed
  diff with no intermediate points to inspect, bisect, or roll back to. The
  granularity of history collapses to the granularity of remembering to commit.
- **Decisions evaporate.** The reasoning behind a choice — the alternatives
  weighed, the constraint that ruled one out — exists in a conversation that is
  not part of the repository. Six months later the code shows *what* was chosen
  and nothing shows *why*, so the choice gets re-litigated or, worse, silently
  reversed by someone who never knew it was a choice.

## Decision

We will treat both as automation rather than discipline.

**Auto-commit.** A `Stop` hook commits completed work with a Conventional
Commits message. Type and scope are derived from the changed paths; an agent may
override the subject by writing `.claude/COMMIT_MSG`, which the hook consumes and
deletes. The hook commits locally and never pushes, refuses to run mid-rebase or
mid-merge, and blocks on credential-shaped paths (`.env`, `*.pem`, `*-key.json`,
`*credentials*.json`) reaching the index — an auto-committer that can commit a
secret is worse than no auto-committer. `RAGOOGLE_AUTOCOMMIT=0` disables it.

**ADR-as-code.** Decisions live as MADR-format markdown in `docs/adr/`, generated
and validated by `tools/adr/adr.py`. A `PostToolUse` hook regenerates
`docs/adr/index.json` whenever an ADR file changes, so the machine-readable index
cannot drift from the prose.

**ADRs surfaced to observability.** Every ADR carries a `component` in its
frontmatter drawn from a fixed vocabulary that matches the node names in the
observability topology graph. The Three.js application reads `index.json` and
renders decisions against the node they constrain, so selecting the `vectorstore`
node shows the decisions that made it what it is, alongside its live/offline
state. This is the point of the whole mechanism: architecture documentation
displayed against running architecture, rather than filed separately from it.

## Consequences

### Positive

- History gains fine-grained, conventionally-typed commits without anyone
  remembering to make them; `git log` becomes a usable record of the build.
- Decisions are reviewable in pull requests as diffs, like code.
- The observability app answers "why is this component like this?" in the same
  place it answers "is this component up?".

### Negative

- Commit subjects derived from paths are accurate but bland; the quality of
  history depends on the `COMMIT_MSG` override actually being used for
  substantive changes.
- Auto-commit will sometimes capture a genuinely intermediate state as a commit.
  Acceptable given the commits are local and never pushed, but it makes `main`
  noisier than hand-curated history.
- The fixed `component` vocabulary couples ADR frontmatter to the observability
  topology; adding a node means updating the validator.

### Neutral

- Commits are attributed with a `Co-Authored-By` trailer, so agent-authored
  changes are identifiable in history.

## Alternatives Considered

**Commit manually at meaningful checkpoints.** Produces better commit messages
and no spurious intermediate states. Rejected because it is exactly the
discipline that fails silently under long agent sessions — the failure mode has
no signal until the history is already gone.

**Generate the commit message with an LLM call in the hook.** Would fix the bland
subject line. Rejected for now: it puts a network call and a cost on the end of
every turn, and the `COMMIT_MSG` override achieves the same result when it
matters, without the tax when it does not.

**Keep ADRs in a wiki or Notion.** Better editing experience, worse everything
else: no review in PRs, no versioning alongside the code the decision governs,
and no path to rendering them in the observability app.
