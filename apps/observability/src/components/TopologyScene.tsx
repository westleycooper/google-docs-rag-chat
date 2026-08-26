/**
 * The live architecture graph (ADR-0006, ADR-0018).
 *
 * Nodes are laid out in tiers by kind — frontends at the top, services in the
 * middle, datastores and external vendors at the bottom — so the shape of the
 * system is legible before any colour is read. Dependency edges connect them.
 *
 * Status is carried by colour *and* by pulse, not colour alone: roughly one
 * in twelve men has a red-green colour vision deficiency, and an
 * architecture dashboard whose only failure signal is "the red one" is
 * unusable for them.
 *
 * Nodes render as icon badges (ADR-0018) rather than abstract 3D primitives:
 * a real MUI icon rasterised onto a status-tinted circular sprite, so what a
 * node *is* reads at a glance instead of needing colour or a legend to
 * decode a shape. Every visible element here is a billboarded sprite (always
 * facing the camera) rather than a lit 3D mesh, since a flat icon glyph only
 * reads correctly face-on.
 *
 * The camera is user-driven (OrbitControls) rather than auto-rotating: once a
 * viewer can grab the scene themselves, ambient motion only fights their drag.
 */

import { useEffect, useRef } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import BuildIcon from '@mui/icons-material/Build';
import CloudIcon from '@mui/icons-material/Cloud';
import DnsIcon from '@mui/icons-material/Dns';
import SortIcon from '@mui/icons-material/Sort';
import HubIcon from '@mui/icons-material/Hub';
import InputIcon from '@mui/icons-material/Input';
import PsychologyIcon from '@mui/icons-material/Psychology';
import StorageIcon from '@mui/icons-material/Storage';
import WebIcon from '@mui/icons-material/Web';
import type { ComponentType } from 'react';
import type { SvgIconProps } from '@mui/material';
import type { ComponentNode, NodeStatus } from '@/api/topology';

// Matches App.tsx's MUI theme and the chat app's default preset, Console
// (Light) (apps/frontend/src/theme.ts CONSOLE_LIGHT.statusColours) -- kept in
// hex here for the same reason App.tsx duplicates its palette instead of
// importing it (see the comment there): the WebGL canvas is a separate
// rendering context the MUI theme object cannot reach into anyway.
const STATUS_COLOURS: Record<NodeStatus, string> = {
  ok: '#3F7D52',
  degraded: '#B8860B',
  down: '#B4322F',
  unknown: '#7A8790',
};

/** Pulses per second. Healthy is still; trouble draws the eye. */
const STATUS_PULSE: Record<NodeStatus, number> = {
  ok: 0,
  degraded: 1.2,
  down: 2.6,
  unknown: 0,
};

const TIER_Y: Record<string, number> = {
  frontend: 2.7,
  service: 0.6,
  datastore: -1.5,
  external: -3.4,
};

const CONNECTOR_COLOUR = 0x4f7c78; // CONSOLE_LIGHT.secondary -- dark enough to read against a light background
const CONNECTOR_WIDTH_PX = 2.5;

// Explicitly requested: thicker than a standard outline, and a colour no
// status or connector already uses, so "selected" never reads as "unhealthy."
const SELECTION_COLOUR = '#CC5500';
const SELECTION_STROKE_FRACTION = 0.09;

const DOCKER_BLUE = 0x2496ed; // Docker's own brand colour
/** The topology nodes that correspond to an actual container in
 * docker-compose.yml, as opposed to a logical concept living inside one
 * (rag-core/ingestion are code paths inside the api process, not services of
 * their own) or something that isn't a container at all (the vendors,
 * infra, tooling). */
const DOCKERISED_NODE_IDS = ['frontend', 'observability', 'api', 'vectorstore'];

/** Every node is the same size now that an icon, not a shape, carries the
 * "what is this" signal -- varying size per kind would just be noise. */
const BADGE_WORLD_SIZE = 0.85;
const BADGE_RADIUS = BADGE_WORLD_SIZE / 2;
const BADGE_CANVAS_SIZE = 160;

type IconComponent = ComponentType<SvgIconProps>;

/** One icon per node id (not per kind): the two frontend-kind nodes are both
 * literally browsers, but the three service-kind nodes are different enough
 * pieces of the system that lumping them under one icon would erase exactly
 * the distinction shape used to carry. Falls back to a plain circle (no
 * icon) for anything not listed rather than guessing. */
const NODE_ICONS: Record<string, IconComponent> = {
  frontend: WebIcon,
  observability: WebIcon,
  api: DnsIcon,
  'rag-core': HubIcon,
  ingestion: InputIcon,
  vectorstore: StorageIcon,
  anthropic: PsychologyIcon,
  voyage: SortIcon, // Voyage's actual jobs -- embedding and reranking -- are an
  // ordering problem, not a wayfinding one; a compass read as "explore/search"
  // when what it does is score and sort candidates.
  infra: CloudIcon,
  tooling: BuildIcon,
};

/** What each node actually does, not just what it's called -- shown as a
 * second, smaller line under the label. Grounded in this project's own ADRs
 * rather than generic category names, so it stays specific to this system:
 * "Retrieval | RRF" for rag-core, not "Backend Service". */
const NODE_SUBLABELS: Record<string, string> = {
  frontend: 'Chat | Context Meter',
  observability: 'Live Topology | ADRs',
  api: 'REST | SSE',
  'rag-core': 'Retrieval | RRF',
  ingestion: 'Drive Sync | Chunking',
  vectorstore: 'Vectors | Full-text',
  anthropic: 'Chat | Eval Judge',
  voyage: 'Embeddings | Reranking',
  infra: 'Terraform | Multi-cloud',
  tooling: 'Quality Gates | ADRs',
};

/**
 * The `d` attribute(s) of a rasterised MUI icon's path(s), drawn directly via
 * `Path2D` rather than round-tripped through an `<img src="data:image/svg+xml...">`.
 * The image-based version produced XML that validated as well-formed
 * (checked with a real XML parser) but still didn't reliably decode as a
 * standalone SVG document in a real browser -- `<img>`'s SVG decoder has
 * requirements (namespace declarations and more) a bare well-formedness
 * check doesn't cover, and chasing each one individually was less reliable
 * than sidestepping the whole image-decode path. `Path2D` fed a raw `d`
 * string is well-supported and entirely synchronous: no `Image`, no
 * `onload`/`onerror`, no data URI, no async texture update. Cached at module
 * scope, keyed by icon component -- the path data never changes, and two
 * node ids share the Web icon.
 */
const ICON_PATH_CACHE = new Map<IconComponent, string[]>();
const iconPaths = (Icon: IconComponent): string[] => {
  const cached = ICON_PATH_CACHE.get(Icon);
  if (cached) return cached;
  const markup = renderToStaticMarkup(<Icon />);
  const paths = [...markup.matchAll(/<path[^>]*\sd="([^"]+)"/g)].map((m) => m[1] ?? '');
  ICON_PATH_CACHE.set(Icon, paths);
  return paths;
};

/**
 * A node's badge: a status-tinted circle with a darker ring (dashed when the
 * node isn't checkable) and the MUI icon for its id drawn on top, all in one
 * synchronous pass -- MUI icons use a 24x24 viewBox and (for every icon used
 * here) the SVG default nonzero fill rule, so scaling into a 24-unit box and
 * filling each path is all `Path2D` needs.
 */
const makeBadge = (node: ComponentNode, muted: boolean): THREE.Sprite => {
  const canvas = document.createElement('canvas');
  canvas.width = BADGE_CANVAS_SIZE;
  canvas.height = BADGE_CANVAS_SIZE;
  const ctx = canvas.getContext('2d');
  const cx = BADGE_CANVAS_SIZE / 2;
  const cy = BADGE_CANVAS_SIZE / 2;
  const r = BADGE_CANVAS_SIZE * 0.42;
  const colour = STATUS_COLOURS[node.status];

  if (ctx) {
    // A dark fill with the full-brightness status colour as the rim, rather
    // than the other way around -- a white icon needs a dark field to read
    // clearly against, and the brighter rim still carries the same status
    // signal at a glance.
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = new THREE.Color(colour).multiplyScalar(0.42).getStyle();
    ctx.fill();

    ctx.lineWidth = BADGE_CANVAS_SIZE * 0.045;
    ctx.strokeStyle = colour;
    if (!node.checkable) ctx.setLineDash([BADGE_CANVAS_SIZE * 0.06, BADGE_CANVAS_SIZE * 0.045]);
    ctx.stroke();
    ctx.setLineDash([]);

    const Icon = NODE_ICONS[node.id];
    if (Icon) {
      const iconSize = BADGE_CANVAS_SIZE * 0.46;
      ctx.save();
      ctx.translate(cx - iconSize / 2, cy - iconSize / 2);
      ctx.scale(iconSize / 24, iconSize / 24);
      ctx.fillStyle = muted ? '#EEF2F1' : '#FFFFFF';
      for (const d of iconPaths(Icon)) {
        ctx.fill(new Path2D(d));
      }
      ctx.restore();
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    opacity: node.checkable ? 1 : 0.6,
    depthTest: false,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(BADGE_WORLD_SIZE, BADGE_WORLD_SIZE, 1);
  return sprite;
};

// Reference used to convert a label canvas's pixel height into a world-space
// sprite height: a single-line label at SCALE was tuned to read well at
// SINGLE_LINE_WORLD_HEIGHT, so every label (one line or two) holds that same
// pixel-to-world ratio rather than a fixed world height squashing a taller,
// two-line canvas down to the same size as a one-line one.
const LABEL_CANVAS_SCALE = 2;
const SINGLE_LINE_HEIGHT_PX = 44 * LABEL_CANVAS_SCALE;
const SINGLE_LINE_WORLD_HEIGHT = 0.26;
const PX_PER_WORLD_UNIT = SINGLE_LINE_HEIGHT_PX / SINGLE_LINE_WORLD_HEIGHT;

/**
 * A node label as a sprite, with an optional smaller second line underneath
 * it naming what the node actually does (e.g. "Embeddings | Reranking")
 * rather than just what it's called.
 *
 * Canvas-texture sprites rather than a text geometry: they always face the
 * camera, need no font loading, and stay crisp because the texture is drawn at
 * device resolution. An unlabelled architecture graph is decorative -- you
 * cannot tell which badge just went red.
 */
const makeLabel = (text: string, colour: string, subtext?: string): THREE.Sprite => {
  const scale = LABEL_CANVAS_SCALE;
  const font = `600 ${26 * scale}px Inter, -apple-system, sans-serif`;
  const subFont = `500 ${18 * scale}px Inter, -apple-system, sans-serif`;

  const measure = document.createElement('canvas').getContext('2d');
  let textWidth = text.length * 16;
  let subtextWidth = 0;
  if (measure) {
    measure.font = font;
    textWidth = measure.measureText(text).width;
    if (subtext) {
      measure.font = subFont;
      subtextWidth = measure.measureText(subtext).width;
    }
  }
  const width = Math.ceil(Math.max(textWidth, subtextWidth) + 24 * scale);
  const mainLineHeight = 34 * scale;
  const subLineHeight = subtext ? 26 * scale : 0;
  const verticalPadding = subtext ? 12 * scale : 0;
  const height = subtext
    ? mainLineHeight + subLineHeight + verticalPadding
    : SINGLE_LINE_HEIGHT_PX;

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    // A rounded plate behind the text so a label crossing an edge line stays
    // readable.
    // Deliberately a dark plate regardless of the surrounding page theme --
    // it is a self-contained floating badge (like a map pin label), not a
    // themed surface, and a dark plate is the one choice that stays legible
    // whether the scene behind it is light or dark.
    ctx.fillStyle = 'rgba(11, 15, 25, 0.82)';
    ctx.beginPath();
    ctx.roundRect(0, 0, width, height, 10 * scale);
    ctx.fill();

    ctx.font = font;
    ctx.fillStyle = colour;
    ctx.fillText(text, width / 2, subtext ? mainLineHeight / 2 + 4 * scale : height / 2 + 2);

    if (subtext) {
      ctx.font = subFont;
      // A fixed, dimmer tone regardless of the main line's colour -- the
      // sub-label is supporting detail, not a second thing demanding equal
      // attention.
      ctx.fillStyle = '#A9B7B4';
      ctx.fillText(subtext, width / 2, mainLineHeight + subLineHeight / 2 + 2 * scale);
    }
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }),
  );
  // Anchored at top-centre rather than the sprite default (dead centre): the
  // label's position is set just below a badge, and a two-line label should
  // grow downward from that fixed point rather than needing its own
  // per-length vertical offset to keep its top edge clear of the badge.
  sprite.center.set(0.5, 1);
  const worldHeight = height / PX_PER_WORLD_UNIT;
  sprite.scale.set((width / height) * worldHeight, worldHeight, 1);
  return sprite;
};

/** The selection indicator: a thick ring sprite, sized just outside a badge.
 * A sprite rather than a 3D ring mesh/line -- everything a badge sits next to
 * needs to billboard the same way it does, or it visibly tilts away from the
 * flat icon as the camera orbits. */
const makeSelectionRing = (): THREE.Sprite => {
  const size = 200;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.strokeStyle = SELECTION_COLOUR;
    ctx.lineWidth = size * SELECTION_STROKE_FRACTION;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size * 0.4, 0, Math.PI * 2);
    ctx.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }),
  );
  sprite.scale.set(BADGE_WORLD_SIZE * 1.35, BADGE_WORLD_SIZE * 1.35, 1);
  return sprite;
};

/** A soft light radial backdrop for the scene, rather than a flat fill --
 * requested explicitly ("it needs to look good"), and kept subtle: a few
 * percent of tint from centre to edge, not the kind of gradient ADR-0017
 * ruled out for the page chrome. That decision was about the MUI theme's
 * flat, no-texture surfaces; a soft depth cue behind a 3D scene is a
 * different thing living in a different layer. */
const makeBackground = (): { texture: THREE.CanvasTexture; edgeColour: number } => {
  const size = 512;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  const centre = '#fcfcfb';
  const edge = '#e7ecec';
  if (ctx) {
    const gradient = ctx.createRadialGradient(
      size / 2,
      size * 0.4,
      0,
      size / 2,
      size / 2,
      size * 0.75,
    );
    gradient.addColorStop(0, centre);
    gradient.addColorStop(1, edge);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  const texture = new THREE.CanvasTexture(canvas);
  return { texture, edgeColour: 0xe7ecec };
};

interface Props {
  nodes: ComponentNode[];
  onSelect: (node: ComponentNode) => void;
  selectedId: string | null;
}

export const TopologyScene = ({ nodes, onSelect, selectedId }: Props) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || nodes.length === 0) return;

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;

    const width = mount.clientWidth || 800;
    const height = mount.clientHeight || 500;

    const scene = new THREE.Scene();
    const background = makeBackground();
    scene.background = background.texture;
    scene.fog = new THREE.Fog(background.edgeColour, 13, 30);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, -0.3, 12.5);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.style.cursor = 'grab';
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, -0.3, 0);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.minDistance = 4;
    controls.maxDistance = 24;
    controls.addEventListener('start', () => {
      renderer.domElement.style.cursor = 'grabbing';
    });
    controls.addEventListener('end', () => {
      renderer.domElement.style.cursor = 'grab';
    });
    controls.update();

    // Lay each tier out evenly across the x axis.
    const byTier = new Map<string, ComponentNode[]>();
    for (const node of nodes) {
      const tier = node.kind;
      byTier.set(tier, [...(byTier.get(tier) ?? []), node]);
    }

    const positions = new Map<string, THREE.Vector3>();
    for (const [tier, members] of byTier) {
      const y = TIER_Y[tier] ?? 0;
      const z = 0;
      const span = Math.max(members.length - 1, 1);
      members.forEach((node, i) => {
        const x = members.length === 1 ? 0 : (i / span - 0.5) * 8.4;
        positions.set(node.id, new THREE.Vector3(x, y, z));
      });
    }

    const group = new THREE.Group();
    scene.add(group);

    // Fat lines need the viewport resolution in their material; every one
    // created gets tracked so a resize can update them all.
    const fatLineMaterials: LineMaterial[] = [];
    const resolution = new THREE.Vector2(width, height);

    // Edges first so badges draw over them. Trimmed to stop at each node's
    // badge radius (uniform now that every node is the same size) rather
    // than running centre-to-centre, which would run a line straight through
    // the middle of a badge instead of meeting its edge.
    for (const node of nodes) {
      const from = positions.get(node.id);
      if (!from) continue;
      for (const dependency of node.depends_on) {
        const to = positions.get(dependency);
        if (!to) continue;

        const direction = new THREE.Vector3().subVectors(to, from);
        const distance = direction.length();
        const trimmed = distance - BADGE_RADIUS * 2;
        if (trimmed <= 0) continue; // badges touch or overlap -- nothing to draw
        direction.normalize();
        const start = from.clone().addScaledVector(direction, BADGE_RADIUS);
        const end = to.clone().addScaledVector(direction, -BADGE_RADIUS);

        const geometry = new LineGeometry();
        geometry.setPositions([start.x, start.y, start.z, end.x, end.y, end.z]);
        const material = new LineMaterial({
          color: CONNECTOR_COLOUR,
          linewidth: CONNECTOR_WIDTH_PX,
          transparent: true,
          opacity: 0.85,
          resolution,
          worldUnits: false,
        });
        fatLineMaterials.push(material);
        group.add(new Line2(geometry, material));
      }
    }

    // A dashed frame around the nodes that are actually docker-compose
    // containers (frontend, observability, api, vectorstore) -- rag-core and
    // ingestion live inside the api process rather than as containers of
    // their own, and the vendors/infra/tooling aren't containers at all, so
    // the box is built from those four positions specifically rather than a
    // whole tier.
    const dockerPositions = DOCKERISED_NODE_IDS.map((id) => positions.get(id)).filter(
      (p): p is THREE.Vector3 => p !== undefined,
    );
    if (dockerPositions.length > 0) {
      const padXZ = 0.7;
      const padY = 0.6;
      const min = dockerPositions.reduce(
        (acc, p) => acc.min(p),
        dockerPositions[0]!.clone(),
      );
      const max = dockerPositions.reduce(
        (acc, p) => acc.max(p),
        dockerPositions[0]!.clone(),
      );
      const boxWidth = max.x - min.x + padXZ * 2;
      const boxHeight = max.y - min.y + padY * 2;
      const boxDepth = max.z - min.z + padXZ * 2;
      const center = new THREE.Vector3(
        (min.x + max.x) / 2,
        (min.y + max.y) / 2,
        (min.z + max.z) / 2,
      );

      const boxGeometry = new THREE.BoxGeometry(boxWidth, boxHeight, boxDepth);
      const boxEdges = new THREE.EdgesGeometry(boxGeometry);
      const boxOutline = new THREE.LineSegments(
        boxEdges,
        new THREE.LineDashedMaterial({
          color: DOCKER_BLUE,
          transparent: true,
          opacity: 0.55,
          dashSize: 0.12,
          gapSize: 0.09,
        }),
      );
      boxOutline.position.copy(center);
      boxOutline.computeLineDistances();
      group.add(boxOutline);

      const dockerLabel = makeLabel('Docker', '#4FC3F7');
      // makeLabel's sprite is top-anchored, so this point is the label's top
      // edge, not its centre -- a little higher than the old centre-anchored
      // offset to land in roughly the same visual spot above the box.
      dockerLabel.position.set(center.x - boxWidth / 2 + 0.55, center.y + boxHeight / 2 + 0.35, center.z);
      group.add(dockerLabel);
    }

    const pulsing: { material: THREE.SpriteMaterial; rate: number; base: number }[] = [];
    const pickable: THREE.Sprite[] = [];

    for (const node of nodes) {
      const position = positions.get(node.id);
      if (!position) continue;

      const muted = !node.checkable || node.status === 'unknown';

      const badge = makeBadge(node, muted);
      badge.position.copy(position);
      badge.userData = { nodeId: node.id };
      group.add(badge);
      pickable.push(badge);

      const rate = STATUS_PULSE[node.status];
      if (rate > 0 && !reduceMotion && node.checkable) {
        pulsing.push({ material: badge.material, rate, base: 0.65 });
      }

      const label = makeLabel(node.label, muted ? '#A8B0AE' : '#FFFFFF', NODE_SUBLABELS[node.id]);
      // Top-anchored (see makeLabel), so this is the gap below the badge, not
      // a center offset -- a two-line label grows downward from here rather
      // than needing a taller offset of its own.
      label.position.set(position.x, position.y - BADGE_RADIUS - 0.14, position.z);
      group.add(label);

      if (node.id === selectedId) {
        const ring = makeSelectionRing();
        ring.position.copy(position);
        group.add(ring);
      }
    }

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    const onClick = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(pickable)[0];
      const nodeId = hit?.object.userData.nodeId as string | undefined;
      const node = nodes.find((n) => n.id === nodeId);
      if (!node) return;
      selectRef.current(node);
      // A direct result of the user's own click, not a delayed/async call --
      // popup blockers only stop window.open() calls that aren't traceable
      // to a real user gesture, which this always is.
      if (node.url) window.open(node.url, '_blank', 'noopener,noreferrer');
    };
    renderer.domElement.addEventListener('click', onClick);

    const clock = new THREE.Clock();
    let frame = 0;
    const render = () => {
      const elapsed = clock.getElapsedTime();
      for (const { material, rate, base } of pulsing) {
        material.opacity = base + 0.35 * (0.5 + 0.5 * Math.sin(elapsed * rate * Math.PI * 2));
      }
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(render);
    };
    render();

    const resize = () => {
      const w = mount.clientWidth || width;
      const h = mount.clientHeight || height;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
      resolution.set(w, h);
      for (const material of fatLineMaterials) material.resolution.copy(resolution);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.domElement.removeEventListener('click', onClick);
      background.texture.dispose();
      group.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line || object instanceof Line2) {
          object.geometry.dispose();
          const material = object.material;
          if (Array.isArray(material)) material.forEach((m) => m.dispose());
          else material.dispose();
        } else if (object instanceof THREE.Sprite) {
          // Sprites are not Meshes: without this branch every poll leaks a
          // canvas texture, and the topology polls every three seconds.
          object.material.map?.dispose();
          object.material.dispose();
        }
      });
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [nodes, selectedId]);

  return <div ref={mountRef} aria-hidden style={{ width: '100%', height: '100%' }} />;
};
