---
id: 0003
title: Support both service-account and OAuth authentication for Google Drive
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: ingestion
tags: [auth, google-drive, permissions, security]
supersedes: []
superseded_by: []
---

# ADR-0003: Support both service-account and OAuth authentication for Google Drive

## Context

RAGDrive must iterate a Google Drive, respecting permissions, and skip anything
it cannot read rather than failing the run. Two authentication models exist, and
they differ in more than credential plumbing — they change *whose* permissions
define the corpus:

- **Service account with domain-wide delegation.** A Workspace admin grants the
  service account the right to impersonate users. Ingestion runs as a chosen
  subject and sees that subject's Drive. Requires admin consent; unavailable to
  personal Gmail accounts.
- **OAuth 2.0 user consent.** The user authorises `drive.readonly` directly. Works
  for any account including personal Gmail, needs no admin, and the corpus is
  exactly what that user can see. Requires storing and refreshing a token.

The platform is meant to serve both a solo user pointing at their own Drive and
an organisation ingesting a shared drive. Picking one mode makes the other
population a second-class citizen.

## Decision

We will support **both modes, selectable per document source** in the
configuration area, behind a single `SourceCredential` port.

The permission model is the point of the abstraction, not the token format. Both
adapters resolve to the same domain concept: an *effective principal* whose
access defines the corpus boundary. Ingestion walks the folder tree as that
principal and treats a 403 on any node as a **skip with an audit record**, never
as a run failure — the record captures folder id, principal, timestamp, and
reason, and surfaces in the config UI so a user can see exactly what was excluded
and why. A silent skip would be indistinguishable from an empty folder, which is
the failure mode that makes RAG systems quietly wrong.

Credentials are encrypted at rest with envelope encryption, the data key held in
the target cloud's KMS. Service-account JSON and OAuth refresh tokens are both
long-lived credentials to a user's entire document corpus and get the same
treatment.

## Consequences

### Positive

- Both a solo Gmail user and a Workspace org are first-class from day one.
- Forcing two auth mechanisms through one port early proves the abstraction is
  real — the same pressure that will make the third source provider cheap.
- Explicit skip-audit records turn "permissions were respected" from a claim into
  something the user can inspect.

### Negative

- Two auth paths to build, test, and document, including an OAuth callback route
  and refresh-token rotation that the service-account path does not need.
- The config UI must explain a genuinely confusing distinction to users who may
  not know which one they have. Choosing wrong yields an empty or partial corpus.

### Neutral

- Per-source rather than per-install selection means one deployment can mix modes
  across sources; the credential store is keyed by source, not by tenant.

## Alternatives Considered

**Service account only.** Simplest single path and the right answer for pure
org deployments. Rejected because it excludes every personal Gmail user and
requires a Workspace admin to do anything at all — a hard stop for evaluation and
solo use.

**OAuth only.** Also a single path, works everywhere, no admin needed. Rejected
because org-wide ingestion would then depend on one employee's token, which
breaks the moment they change roles or leave, and scopes the corpus to whatever
that individual happens to have been shared on.
