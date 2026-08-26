---
id: 0017
title: Separate Claude/Voyage liveness and mark non-runtime topology nodes as reference-only
status: accepted
date: 2026-08-26
deciders: [Westley Cooper-Thorn]
component: observability
tags: [topology, three.js, health-check, ux]
supersedes: []
superseded_by: []
---

# ADR-0017: Separate Claude/Voyage liveness and mark non-runtime topology nodes as reference-only

## Context

Direct feedback on the topology view (ADR-0006), five points:

1. Nodes should be rounded-corner cubes, rendered as unfilled poly lines, not
   solid shapes.
2. The lines connecting nodes are too faint to read.
3. A node should be clickable through to where it actually runs (e.g.
   `localhost:5173`).
4. Claude and Voyage were one combined node ("Claude / Voyage") — they should
   check and render separately.
5. Why does the `infra` node render greyed out?

(4) and (5) share a root cause worth naming before the fix. `TOPOLOGY` in
`health.py` listed nine nodes, but the API only had a live check for six of
them: `frontend`/`observability` (pinged), `api`/`rag-core`/`ingestion`
(derived from the API's own health), and `vectorstore` (a real query). The
other three — `platform` ("Claude / Voyage"), `infra`, and `tooling` — fell
into the same `else: state = "unknown"` branch and rendered identically grey.
But they are grey for two *different* reasons that look the same and are not:
`platform` was permanently unknown only because nobody had wired a check for
it yet — a gap. `infra` and `tooling` are permanently unknown because they are
not runtime services at all: Terraform state and a local `check.sh` script
have no process to poll. One of these is fixable; the other was never going
to be anything but grey, and rendering it exactly like a service that might be
down is what prompted "why is this greyed out?" A grey circle with no further
information cannot tell a user which situation they're looking at.

Separately, the `platform` node id happened to collide with the ADR `component`
vocabulary value `platform`, which is tagged only on ADR-0001 (the monorepo
structure decision). Selecting "Claude / Voyage" therefore surfaced an ADR
about repository layout — accidentally wrong, not merely uninformative.

## Decision

**Split and check the vendors.** `platform` becomes two nodes, `anthropic` and
`voyage`, each pinged independently (reusing the existing `_ping` helper) at a
new `anthropic_ping_url` / `voyage_ping_url` setting. Neither vendor exposes an
unauthenticated health endpoint, so these point at each vendor's public
marketing site as the closest honest proxy for "is anything answering there" —
not a claim about API status or key validity, the same standard `_ping`
already holds `frontend`/`observability` to. `status.anthropic.com` was tried
first and rejected: it 301-redirects on first hit, and `_ping` treats a
redirect as `down` by design (a health probe silently redirected to e.g. a
login page is exactly the false positive that rule exists to catch), so it
read as permanently down regardless of Anthropic's actual status. Neither new
node id is forced into the ADR `component` vocabulary — Voyage-related
decisions are already tagged `rag-core`, where a real "RAG Core" node exists,
so `anthropic`/`voyage` simply carry no ADRs rather than an incorrect one.

**Give non-runtime nodes a real `checkable: bool` instead of a fake status.**
`ComponentNode` gains `checkable`, `false` for `infra` and `tooling`. The
frontend renders these with a dashed outline, an "outlined" chip reading
`reference` instead of a status word, and detail-panel copy that says
"reference only, no live status" rather than implying a check ran and came
back empty. This is the direct answer to "why is it greyed out": now it
doesn't render as an ambiguous unknown at all.

**A `url` per node, surfaced as a real link.** `ComponentNode` gains
`url: str | None`, resolved per node: the frontend/observability/api nodes get
their browser-reachable origin (`frontend_public_url` etc. — the
container-network `frontend_url` used for pinging is unreachable from an
actual browser, the same distinction ADR-0016 already drew for the OAuth
callback), the vendor nodes get their console URL, and anything with no
meaningful destination (Postgres, and the two non-checkable nodes) gets
`None`. The 3D click stays select-only — turning every click into an
immediate new tab would be surprising and irreversible per click — but the
link now appears both in the sidebar list (a small open-in-new icon next to
each row) and prominently in the detail panel once a node is selected.

**Nodes are rounded-corner wireframe boxes.** Every node kind now uses
`RoundedBoxGeometry` (proportioned per kind — flat-and-wide for a frontend,
tall for a datastore, small for external — so shape still hints at role now
that they're all boxes) rendered via `THREE.EdgesGeometry` as unfilled line
segments rather than a lit `MeshStandardMaterial` solid. An invisible solid
twin of the same geometry carries the raycast hit-test, since a bare
wireframe has almost no area to click precisely. `checkable: false` nodes get
a dashed line material instead of solid, reinforcing the "reference, not
polled" reading directly in the 3D view, not just the side panel.

**Dependency edges are fat lines, not 1px `THREE.Line`.** `linewidth` on
`LineBasicMaterial` is ignored by most WebGL backends (a long-standing
three.js/ANGLE limitation), which is why the old edges were unfixably thin no
matter what opacity was set. Edges now use the `Line2`/`LineMaterial` addon,
which triangulates actual screen-space width, at a brighter colour and full
opacity.

**The camera is user-driven.** `OrbitControls` replaces the fixed camera plus
ambient auto-sway — rotate-drag, scroll-to-zoom, with damping. The old sway
is dropped entirely rather than kept as an idle-only animation: fighting a
user's drag with ambient motion is worse than no motion, and interactivity was
the actual ask.

## Consequences

### Positive

- "Why is infra greyed out" has a real answer now, visible in the UI itself
  rather than requiring an explanation: a dashed reference node reads
  differently from a solid unknown one at a glance.
- Claude and Voyage each report real status instead of a permanent unknown,
  and selecting either shows the node's own name, not an unrelated ADR about
  repository structure.
- A node is one click away from where it actually lives.
- The graph is interactively explorable rather than a fixed diorama.

### Negative

- Vendor liveness is a proxy (marketing site reachability), not a real status
  signal — a vendor's actual API could be degraded while their homepage is
  fine, or vice versa. Explicitly a "closest honest proxy," not a claim of
  more precision than it has.
- Every `/topology` poll now makes two additional outbound HTTPS requests to
  third-party domains, on a 3-second poll interval. Bounded by the existing
  2-second `PING_TIMEOUT_SECONDS` and run concurrently with the other pings,
  but it is real, continuous external traffic this feature did not generate
  before.
- All nodes sharing one geometry type (a box) trades away the previous
  per-kind primitive distinction (sphere/cylinder/octahedron/box) for size and
  proportion instead — a smaller signal, leaned on more heavily now.

### Neutral

- `checkable` is a new field every `ComponentNode` consumer must handle
  (both frontend apps' generated/hand-written types updated here). A third
  future non-runtime node gets the same treatment for free.

## Alternatives Considered

**Give `infra`/`tooling` a synthetic "ok" status instead of tracking
checkability separately.** Would stop them looking broken. Rejected: it's a
fabricated claim ("this is fine") standing in for "there is nothing to check,"
which is exactly the kind of guessed status ADR-0006 already rules out for
pings — dishonest in the same way a green light would be if the API had never
actually looked.

**Ping Claude/Voyage's actual API endpoints instead of their marketing
sites.** Would be a more direct signal if either exposed a status route
answering without auth; neither does (both return non-2xx to an
unauthenticated GET), so this would need a per-source-config API key just to
answer "is anything there," which is a much larger commitment (spending a
customer's Voyage/Anthropic budget, per poll, forever) for a liveness check
that a public page can approximate for free.

**Keep the ambient auto-rotate, layered under OrbitControls.** Three.js
doesn't stop `autoRotate` during a manual drag without extra event wiring, so
this would fight the user's own rotation. Dropped rather than fought.
