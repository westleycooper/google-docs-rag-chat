---
id: 0018
title: Icon badges, a soft gradient backdrop, and click-to-open for the topology graph
status: accepted
date: 2026-08-26
deciders: [Westley Cooper-Thorn]
component: observability
tags: [topology, three.js, icons, ux]
supersedes: []
superseded_by: []
---

# ADR-0018: Icon badges, a soft gradient backdrop, and click-to-open for the topology graph

## Context

Direct feedback on the topology graph, after [ADR-0017](0017-separate-claude-voyage-liveness-and-mark-non-runtime-topolog.md)
had already taken it through unfilled wireframes, filled Platonic solids, and
back to filled -- a Platonic solid still only encoded *kind* (four shapes for
four kinds), not *what a specific node is*. The request was explicit and
concrete: "models that represent the feature" -- a browser for the two
frontend-kind nodes, something server-like for the API, a brain for Claude --
plus a gradient backdrop ("it needs to look good"), and the per-node URLs
(already reachable via a link in the sidebar and detail panel, per ADR-0017)
made clickable directly on the node. Two more requests arrived mid-session: a
labelled box around the nodes that are actually docker-compose containers, and
the selection ring made thicker and a distinct dark orange.

Shape-per-kind was reaching its limit as a signal. Four kinds sharing one of
four abstract polyhedra still requires a legend or a guess; an icon is
self-describing. And the underlying nine nodes are not four uniform
categories -- `api`, `rag-core`, and `ingestion` are all "service"-kind but do
materially different things, a distinction shape-per-*kind* could never
express and icon-per-*node* can.

## Decision

**Icon badges replace 3D primitives.** Every node is a billboarded circular
sprite: a status-tinted fill, a darker ring (dashed when `checkable: false`,
matching the existing "reference, not polled" treatment), and a real MUI icon
rasterised on top -- not a hand-drawn approximation. Rasterising a professionally
designed icon is more implementation than sketching a shape from canvas
primitives, but the alternative was inventing brain/cloud/compass/server
glyphs by eye with no way to preview the result in this session; a maintained
icon set removes that risk entirely. The icon is rendered via
`react-dom/server`'s `renderToStaticMarkup` to a static SVG string, base64-encoded
into a data URI, loaded into an `Image`, and drawn onto the badge's canvas
once it resolves -- fully offline (no font CDN, no network dependency), reusing
`@mui/icons-material`, which was already a dependency. The mapping is
per **node id**, not kind: `frontend`/`observability` both get the browser icon
(`Web`) since both literally are one; `api` gets `Dns` (a server-rack glyph);
`rag-core` gets `Hub`; `ingestion` gets `Input`; `vectorstore` gets `Storage`;
`anthropic` gets `Psychology` (the literal "brain" icon requested);
`voyage` gets `Explore` (a compass, fitting both the name and the
embeddings-search job); `infra` gets `Cloud`; `tooling` gets `Build`.

Every node is now the same size (`BADGE_WORLD_SIZE`) -- size and shape used to
hint at kind; the icon does that job now, so varying size would only be
noise. Being sprites, every element that sits near a badge -- the selection
ring, previously a flat 3D ring or line-loop -- had to become a sprite too, or
it would visibly tilt away from the flat, always-camera-facing icon as
`OrbitControls` orbits the scene.

**A soft radial gradient backdrop.** `scene.background` is a `CanvasTexture`
painted with a light radial gradient (near-white centre, a faint cool-grey
edge) rather than the flat `SCENE_BACKGROUND` fill from ADR-0017. `scene.fog`
now matches the gradient's edge tone so distant elements fade into it rather
than a mismatched flat colour. This is *not* a reversal of ADR-0017's "no
gradients" rule for the page chrome -- that rule was specifically about the
MUI theme's flat surfaces (AppBar, body), which stay exactly as flat as
Console (Light) specifies. A soft depth cue behind a 3D scene, requested
explicitly and scoped to the canvas alone, is a different layer entirely.

**Clicking a node opens its URL.** ADR-0017 deliberately kept the 3D click
select-only, reasoning that turning every click into a new tab would be
surprising per click. That reasoning is explicitly overridden here: selecting
and opening now happen together on a single click, when the node has a `url`.
`window.open` is called synchronously inside the real DOM `click` handler, so
it is a direct result of the user's gesture and popup blockers do not
interfere -- the same guarantee any other click-to-navigate control relies on.

**A dashed Docker-blue box around the actual containers.** Built from the
specific positions of `frontend`, `observability`, `api`, and `vectorstore`
-- the nodes with a real service entry in `docker-compose.yml` -- not from a
whole tier, since two of those four share a tier with nodes that are *not*
containers (`rag-core` and `ingestion` are code paths inside the `api`
process; `infra`/`tooling` aren't containers at all). A "Docker" label sits at
the box's top-left corner. Dashed rather than solid, so it reads as an
annotation grouping existing nodes rather than a node or edge of its own.

**The selection ring is thicker and dark orange (`#CC5500`).** Explicit
request. Distinct from every status colour and from the connector/Docker-box
colours, so "this is selected" never reads as "this is unhealthy" or "this is
containerised."

## Consequences

### Positive

- A node's kind, and often its specific identity, reads at a glance without
  colour, a legend, or reading the label -- the icon alone carries it.
- The scene finally looks considered rather than a bare functional 3D
  scatter plot: a gradient backdrop, a container-grouping annotation, and a
  distinct selection colour are all cheap, additive polish once the badge
  system exists to hang them on.
- One click both inspects a node (ADR-0006's core promise: decisions
  rendered against the running component) and reaches the running thing
  itself.

### Negative

- Every node's icon depends on an async SVG rasterisation step; a badge is a
  plain tinted disc for a frame or two before its glyph fills in, and
  because the whole scene rebuilds on every ~3-second topology poll (an
  existing, unchanged behaviour), this happens repeatedly rather than once.
  Cheap per icon (a tiny 24x24 SVG), but not free.
- `rag-core` (Hub) and `ingestion` (Input) and `infra` (Cloud) and `tooling`
  (Build) icons were chosen without the concrete visual anchor the user gave
  for browser/server/brain -- defensible, but a guess where those three
  were not.
- Click-to-open reverses ADR-0017's stated caution about surprise new tabs.
  Explicitly requested here, but worth remembering if it resurfaces as
  friction: the earlier concern was real, not invented.

### Neutral

- The selection ring, Docker box, and connector lines are three different
  visual languages for "this is a grouping/accent, not a node" (a thick
  circle, a dashed box, a solid fat line) -- deliberate, so none is mistaken
  for the others, but it means three colour/style conventions to keep
  straight rather than one.

## Alternatives Considered

**Hand-drawn canvas icon glyphs instead of rasterising real MUI icons.**
Considered first: fully synchronous, zero new import surface, no
`react-dom/server` dependency. Rejected because "it needs to look good" was
explicit, and inventing nine recognisable icon glyphs (a brain, specifically)
from canvas primitives with no way to preview the result in this session was
a real risk to visual quality that a maintained, professionally designed icon
set removes entirely.

**A ligature icon font (Material Symbols) loaded from Google Fonts, drawn via
`ctx.font`/`fillText` like the existing text labels.** Simpler than SVG
rasterisation -- no async image load, no `react-dom/server`. Rejected: it
requires a runtime font download, and this app's only other font
(`apps/observability/src/App.tsx`'s `fontFamily`) is a system-font stack with
no external dependency by design. A missing or blocked font would silently
degrade an icon to literal ligature text ("psychology") floating in a badge,
which is a worse failure mode than the async-load delay the SVG approach
already accepts.

**Keep abstract 3D shapes, add an icon as a small badge overlay instead of
replacing the shape.** Considered in an earlier round (a three-way choice
between better 3D shapes, icon sprites, and this hybrid) and passed over then
for "better 3D shapes." Not revisited here: the explicit follow-up request
was for icons *as* the node's representation, not an accent on top of one.
