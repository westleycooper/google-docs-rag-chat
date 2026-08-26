---
id: 0016
title: OAuth consent flow and drive.readonly over drive.file for Drive sources
status: accepted
date: 2026-08-26
deciders: [Westley Cooper-Thorn]
component: ingestion
tags: [oauth, google-drive, credentials, security]
supersedes: []
superseded_by: []
---

# ADR-0016: OAuth consent flow and drive.readonly over drive.file for Drive sources

## Context

[ADR-0003](0003-dual-mode-google-drive-authentication.md) named `drive.readonly`
as the OAuth scope and defined the domain-level `AuthMode.OAUTH` concept, but no
browser-facing consent flow existed to produce a token in the first place: the
only way to get a source into `oauth` mode was to paste a refresh token you'd
somehow already obtained elsewhere. There was also no way to browse a Drive to
pick `root_folder_ids` — a user had to already know a folder's opaque id — and
no way to edit a source once created, only delete and recreate it.

A real "Connect Google Drive" button raises a scope question ADR-0003 asserted
but never argued for. Google's own guidance is to prefer the narrowest scope
available, and for an app that only touches files it creates, that is
`drive.file`. RAGDrive is not that app: it ingests a folder tree the user
*already has* in their Drive, chosen after the fact from a folder picker. Google
resolves `drive.file` against two different capability sets depending on how a
file entered the app's grant:

- Files the user creates or explicitly opens through this app's own UI (e.g. via
  the Google Picker) — full read access.
- Every other pre-existing file in the Drive — invisible. `drive.file` does not
  degrade to "ask the user each time"; it simply cannot see files it wasn't
  handed.

Recursive ingestion of an existing folder tree is exactly the case `drive.file`
excludes. The only way to reach pre-existing files under it is the Google
Picker JS widget, and the Picker selects individual files or single folders one
click at a time — it has no notion of "grant everything under this folder,
including what gets added to it later," which is what a `root_folder_ids`
ingestion root means. Re-granting through the Picker on every folder change, or
falling back to `drive.readonly` anyway the first time ingestion 403s on a file
the grant never covered, defeats the reason to pick the narrower scope.

Separately, credential storage was coupled to `SourceConfig`: the only write
path was `PUT /sources/{id}/credential`, which requires a source row to already
exist. A folder picker needs a working credential *before* the user has
anything to save — you cannot browse a Drive to choose its ingestion root
without first proving you can read it.

## Decision

We keep `drive.readonly` as the sole scope for both auth modes (`SCOPES` in
`google_oauth.py` is `DRIVE_SCOPES + ("openid", "email")`) and build the
missing pieces around it:

**Server-driven OAuth, not client-side.** `GET /oauth/google/start` builds the
Google authorization URL with `access_type=offline` and `prompt=consent` —
offline so a refresh token is issued at all, forced consent because Google
otherwise silently omits the refresh token on a repeat authorization for the
same client/user pair — and 307-redirects the browser there directly. CSRF
state does not need server-side session storage: a nonce goes into an HttpOnly
cookie, and `{nonce, return_path, editing_source_id}` round-trips through
Google unmodified as the base64-encoded `state` parameter. `GET
/oauth/google/callback` compares the two, exchanges the code, and redirects
back into the frontend carrying `oauth_status`, `credential_ref`, and
`principal` as query params — the frontend has no OAuth-specific state beyond
reading those on mount.

**Credentials decoupled from sources.** `POST /credentials` stores a secret
under a server-generated reference with no source association. The OAuth
callback uses it to mint a `credential_ref` the moment the token exchange
succeeds — before any `SourceConfig` exists — and the new `POST
/sources/browse-folders` endpoint accepts any `credential_ref` plus
`auth_mode`/`principal` to list a folder's immediate subfolders. The
source-scoped `PUT /sources/{id}/credential` stays for rotating an existing
source's credential in place; the two endpoints serve different lifecycle
moments; a source's `credential_ref` field is the only link between them.

**Editing.** `PUT /sources/{id}` lets a source's configuration change without
delete-and-recreate, so reconnecting Drive or rotating a credential on an
already-ingesting source doesn't orphan its ingestion history.

**Folder browsing has a floor, and a manual escape hatch stays.**
`GoogleDriveSource.list_folders` lists immediate children only (breadcrumb
navigation drives the recursion client-side), one page of up to 100 folders,
and only reaches `'root' in parents` — Shared Drives aren't enumerable through
it. `root_folder_ids` still accepts a hand-typed id regardless, so a Shared
Drive folder unreachable by the picker is one paste away rather than blocked.

## Consequences

### Positive

- "Connect Google Drive" is a real consent screen now, not a token pasted in
  from somewhere else — the gap the user actually hit.
- A folder can be picked by browsing instead of typing an opaque id blind.
- Sources are editable in place; reconnecting or rotating credentials doesn't
  require deleting ingestion history.
- One scope, one consent screen, one set of 403-handling code paths for both
  auth modes — no `drive.file`/`drive.readonly` split to reason about per
  source.

### Negative

- `drive.readonly` is broader than Google's own least-privilege guidance
  recommends, and broader than what the user proposed — see Alternatives.
  Anyone auditing the OAuth consent screen sees "See all your Drive files," not
  a scoped grant, which is a legitimately worse story to tell a security-
  conscious user.
- The folder picker cannot reach Shared Drives; usable only for My Drive until
  someone extends `list_folders` with `driveId`/`corpora` support.
- Reconnecting Drive from the edit dialog always mints a fresh
  `credential_ref` (the callback has no notion of "rotate this specific
  source's credential") rather than updating the existing one in place, so the
  old credential row is left orphaned in the store with no cleanup mechanism.
- Requires a real Google Cloud Console project with the Drive API enabled and
  the exact redirect URI registered — external setup this repository cannot do
  on the user's behalf.

### Neutral

- The `state` param is unencrypted base64 JSON, not signed — this is fine
  because the only thing it authorizes is *reading back its own contents*
  (return path, nonce, editing source id); the nonce-vs-cookie comparison is
  what actually blocks CSRF, so tampering with the visible payload cannot
  forge a valid callback.

## Alternatives Considered

**`drive.file`, as the user proposed and Google's own guidance recommends
first.** The right default for an app that only manages files it creates.
Rejected for this system specifically because ingestion's entire job is
reading a folder tree the user already has, chosen after the fact through a
folder picker — precisely the case `drive.file` cannot see into without the
Google Picker widget, and the Picker's one-click, no-recursion grant model
doesn't fit "everything under this folder, including future additions." Worth
revisiting if Drive sources ever move to a picker-driven "attach these specific
files" model instead of folder-root ingestion — a real alternative, not a
theoretical one, so a future ADR superseding this one is the right way to make
that switch if the ingestion model changes.

**Google Picker API for folder selection, keeping `drive.file`.** Would let
`drive.file` work for the picked folder alone. Rejected because the Picker
grants file-by-file/folder-by-folder, not a subtree, so newly added files
under an already-selected folder would silently fall outside the grant — the
opposite of what "ingest this folder" should mean, and a much subtler failure
than a 403 skip record because nothing would ever mark it as skipped.

**Client-side OAuth (Google's JS SDK, implicit or PKCE-in-browser).** Skips a
server round trip. Rejected: refresh tokens must never reach the browser, and
this platform needs a refresh token — not just a short-lived access token — to
run ingestion later without the user present.

**Session-backed CSRF state instead of a signed round-trip.** More
conventional. Rejected to avoid a server-side session store for a flow that
only needs to survive one redirect round trip; the cookie-plus-echoed-state
pair gives the same guarantee without one.
