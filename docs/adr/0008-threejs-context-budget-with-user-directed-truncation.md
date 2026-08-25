---
id: 0008
title: Visualise the context budget in Three.js with user-directed truncation
status: accepted
date: 2026-08-25
deciders: [Westley Cooper-Thorn]
component: frontend
tags: [threejs, ux, context-window, tokens]
supersedes: []
superseded_by: []
---

# ADR-0008: Visualise the context budget in Three.js with user-directed truncation

## Context

Users of long RAG conversations hit a failure mode they cannot see coming: the
context window fills, something silently falls out of it, and the assistant
starts answering as though a document it cited three turns ago never existed.
The model gives no signal that this has happened. Claude Code's own context meter
exists precisely because knowing *how full* the window is, and being able to
decide *what leaves it*, turns an invisible failure into a managed one.

The requirement is to render this in Three.js and make it truncatable in the same
spirit.

Two things make this harder than a progress bar. Context is not one quantity —
it is system prompt, message history, retrieved chunks, and pinned documents,
each with a different cost and a very different claim on being kept. And the
composition changes on every turn as retrieval brings new chunks in.

## Decision

We will render the context budget as a **segmented volumetric meter** in
Three.js, sharing a WebGL context with the retrieval trace
(see [ADR-0009](0009-stream-and-visualise-the-retrieval-reasoning-trace.md)) and
reading from the Redux projection
(see [ADR-0007](0007-client-cached-session-context-hydrated-at-inception.md)).

- Each context class — system, history, retrieved chunks, pinned documents — is a
  distinct segment, sized by real token count from the server's tokeniser rather
  than a character-count estimate. Estimating here would make the meter confidently
  wrong at exactly the moment it matters.
- Individual chunks and documents are addressable within their segment: hovering
  identifies the source, and selecting one offers to drop or pin it. Truncation is
  therefore a *choice about a named thing*, not a slider over an opaque total.
- The meter shows the eviction frontier — which items the next turn would push
  out at the current fill — so the user acts before the loss, not after it.
- Dropping an item dispatches a Redux action, applies optimistically, and `PATCH`es
  the server, which returns the recomputed budget.

The 3D treatment earns its place by carrying more than one dimension at once:
segment volume is token cost, depth is recency, and colour is retrieval relevance.
A stacked bar can show cost alone.

Accessibility is a hard requirement, not a follow-up. The meter has an equivalent
accessible table with the same affordances, `prefers-reduced-motion` disables the
animated transitions, and the canvas is never the only route to dropping an item —
because a WebGL canvas is not reachable by a screen reader at all.

## Consequences

### Positive

- The most damaging silent failure in long RAG chats becomes visible and
  actionable before it costs an answer.
- Users can protect a document they know matters, rather than hoping the eviction
  heuristic agrees with them.
- The WebGL context is shared with the reasoning trace, so the cost is one
  renderer, not two.

### Negative

- Real token counts mean a tokeniser round-trip per context change, or a
  server-pushed count on each turn. Estimating client-side is cheaper and is the
  thing we have explicitly refused.
- A WebGL canvas in the main chat view carries a memory and battery cost that a
  DOM meter does not, and it must degrade gracefully where WebGL is unavailable.
- Two implementations of the same affordance — canvas and accessible table — to
  keep in step.

### Neutral

- Three.js is already a dependency for the observability app; this is a shared
  competence rather than a new one.

## Alternatives Considered

**A DOM progress bar with a token count.** Cheaper, accessible by default, and
covers "how full is it". Rejected because it cannot express which *item* is about
to be evicted, which is the part that lets a user act.

**Automatic truncation with no UI.** What most chat products do. Rejected as
precisely the silent failure this decision exists to remove — though it remains
the fallback behaviour when the user does not intervene.

**A full 3D graph of the whole context.** More expressive still. Rejected as
disproportionate for a panel that sits beside a chat window and must stay
legible at a glance.
