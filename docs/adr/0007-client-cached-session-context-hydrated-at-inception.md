---
id: 0007
title: Server-authoritative session context, Redux-cached and hydrated at chat inception
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: frontend
tags: [redux, session, context, state-management]
supersedes: []
superseded_by: []
---

# ADR-0007: Server-authoritative session context, Redux-cached and hydrated at chat inception

## Context

A chat session needs persistent context: prior turns, the documents pinned into
the conversation, the retrieved chunks still in scope, and the selected Claude
model. The requirement as stated is that this be held in the frontend with Redux
and populated when a chat begins.

Redux is clearly right for the *client* half of that. It gives one predictable
store for the context panel, the message list, and the token-budget visualisation
(see [ADR-0008](0008-threejs-context-budget-with-user-directed-truncation.md)) to
read from, and it makes truncation an ordinary reducer rather than an ad-hoc
mutation scattered across components.

But there is a question hidden underneath, and it is not a state-management
question: **who owns the context that is actually sent to the model?**

Making the browser the owner has three consequences that are easy to miss. The
context becomes client-supplied input to an LLM call, so a modified store is a
prompt-injection surface. It does not survive a device change, a cleared cache,
or a second tab. And evaluation
(see [ADR-0010](0010-evaluation-as-a-first-class-bounded-context.md)) cannot
replay a conversation it never saw, which quietly removes the ability to debug a
bad answer after the fact.

## Decision

We will keep the **server authoritative** for session context and use Redux as a
**hydrated projection** of it.

- Context is persisted in Postgres, keyed by session, alongside the message
  history it belongs to. It is assembled server-side at prompt time.
- At chat inception the client issues one `GET /sessions/{id}/context` and
  hydrates the store. This is the "populate at inception" behaviour requested,
  and it is what makes a session resumable on another device or tab.
- Redux Toolkit holds the projection; the generated React Query hooks
  (see [ADR-0001](0001-ddd-hexagonal-monorepo-for-ragoogle.md)) own the server
  round-trips. RTK is the client's working copy and the source of truth for
  *nothing* that reaches the model.
- User edits to context — pinning a document, dropping a stale retrieval,
  truncating — apply optimistically in the store and are `PATCH`ed to the server,
  which re-validates and returns the canonical budget. The client proposes; the
  server disposes.
- `redux-persist` keeps the projection in session storage so a refresh is instant
  rather than blank, but the hydrate call still runs and the server's answer wins
  any disagreement.

## Consequences

### Positive

- Sessions resume across devices and tabs, because the context lives where the
  history lives.
- The prompt cannot be altered by editing browser state; the server rebuilds it
  from its own record on every turn.
- Conversations are replayable, which is what makes the evaluation context able
  to score real traffic rather than synthetic fixtures.
- The context panel, budget meter and truncation UI all read one store.

### Negative

- Every context mutation is a round-trip, so truncation has latency that a purely
  local store would not. Optimistic updates hide it, at the cost of reconciliation
  logic that must handle the server disagreeing.
- Two representations of context to keep in step, and a class of bug — projection
  drift — that a single-owner design would not have.

### Neutral

- Session storage rather than local storage for the persisted projection: context
  belongs to a session, and outliving the tab would be surprising.

## Alternatives Considered

**Redux as the sole owner, context posted from the client each turn.** Exactly
what was proposed, and materially simpler: no hydrate endpoint, no reconciliation,
no server-side assembly. Rejected on the three consequences above — the tampering
surface is the decisive one, since context is prompt content and this system will
hold corporate documents. Worth revisiting if Ragoogle is ever deployed as a
purely local single-user tool, where all three objections weaken at once.

**Server-only, no client store, re-fetch on every render.** No drift and no
duplication. Rejected because the context visualisation needs to animate against
local state at frame rate, and a network round-trip per interaction makes the
Three.js panel unusable.

**Redux with the server as a write-behind log.** Client owns, server records
asynchronously for evals. Rejected as the worst of both: it keeps the tampering
surface while adding the synchronisation cost.
