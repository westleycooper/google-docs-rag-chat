/**
 * The live architecture graph (ADR-0006).
 *
 * Nodes are laid out in tiers by kind — frontends at the top, services in the
 * middle, datastores and external vendors at the bottom — so the shape of the
 * system is legible before any colour is read. Dependency edges connect them.
 *
 * Status is carried by colour *and* by pulse rate, not colour alone: roughly
 * one in twelve men has a red-green colour vision deficiency, and an
 * architecture dashboard whose only failure signal is "the red one" is unusable
 * for them.
 *
 * Nodes render as filled low-poly shapes with a darker edge outline on top --
 * solid enough that status colour reads clearly at a glance, with the outline
 * keeping each shape's silhouette crisp against its neighbours. The fill mesh
 * carries the raycast hit-test directly; no separate invisible proxy is
 * needed once the shape is solid rather than a bare line.
 *
 * The camera is user-driven (OrbitControls) rather than auto-rotating: once a
 * viewer can grab the scene themselves, ambient motion only fights their drag.
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import type { ComponentNode, NodeStatus } from '@/api/topology';

// Matches App.tsx's MUI theme and the chat app's default preset, Console
// (Light) (apps/frontend/src/theme.ts CONSOLE_LIGHT.statusColours) -- kept in
// hex here for the same reason App.tsx duplicates its palette instead of
// importing it (see the comment there): the WebGL canvas is a separate
// rendering context the MUI theme object cannot reach into anyway.
const STATUS_COLOURS: Record<NodeStatus, number> = {
  ok: 0x3f7d52,
  degraded: 0xb8860b,
  down: 0xb4322f,
  unknown: 0x7a8790,
};

// The page background (CONSOLE_LIGHT.background.default) -- the renderer's
// clear colour and the fog both match it exactly, so the canvas blends into
// the surrounding page rather than sitting in its own dark box.
const SCENE_BACKGROUND = 0xf7f7f5;

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

const SELECTION_COLOUR = 0x1c2b33; // CONSOLE_LIGHT.primary -- a dark ring is the strong outline on a light page

/** Shrinks every primitive below by the same factor -- "the same shapes as
 * before, a bit smaller." */
const NODE_SCALE = 0.8;

interface NodeShape {
  geometry: THREE.BufferGeometry;
  /** Half the shape's vertical extent, for label placement below it. */
  halfHeight: number;
  /** Selection-ring radius, sized to sit just outside the shape. */
  ringRadius: number;
}

/** Per-kind primitives, so shape still hints at role (readable even without
 * colour) -- box and sphere replaced with two more Platonic solids alongside
 * the octahedron external already used. Every shape here has real dihedral
 * angles between adjacent faces, so `THREE.EdgesGeometry` (silhouette edges
 * only) reads cleanly on all of them; a smooth sphere or a box's flat faces
 * used to need special-casing (a box's edges are fine, but a sphere has none
 * an edge-angle threshold will ever catch), which no longer applies now that
 * every shape is faceted by construction. For any `new THREE.XGeometry(radius)`
 * Platonic solid, `radius` is exactly the circumradius -- the same distance
 * from centre to vertex in every direction -- so using it directly as
 * `halfHeight` is exact, not an approximation. */
const shapeFor = (kind: string): NodeShape => {
  if (kind === 'datastore') {
    const radius = 0.5 * NODE_SCALE;
    const height = 0.7 * NODE_SCALE;
    return {
      geometry: new THREE.CylinderGeometry(radius, radius, height, 24),
      halfHeight: height / 2,
      ringRadius: radius * 1.5,
    };
  }
  if (kind === 'external') {
    const radius = 0.55 * NODE_SCALE;
    return {
      geometry: new THREE.OctahedronGeometry(radius),
      halfHeight: radius,
      ringRadius: radius * 1.5,
    };
  }
  if (kind === 'frontend') {
    const radius = 0.5 * NODE_SCALE;
    return {
      geometry: new THREE.DodecahedronGeometry(radius),
      halfHeight: radius,
      ringRadius: radius * 1.5,
    };
  }
  // service
  const radius = 0.55 * NODE_SCALE;
  return {
    geometry: new THREE.IcosahedronGeometry(radius),
    halfHeight: radius,
    ringRadius: radius * 1.5,
  };
};

/**
 * A node label as a sprite.
 *
 * Canvas-texture sprites rather than a text geometry: they always face the
 * camera, need no font loading, and stay crisp because the texture is drawn at
 * device resolution. An unlabelled architecture graph is decorative -- you
 * cannot tell which sphere just went red.
 */
const makeLabel = (text: string, colour: string): THREE.Sprite => {
  const scale = 2;
  const font = `600 ${28 * scale}px Inter, -apple-system, sans-serif`;
  const measure = document.createElement('canvas').getContext('2d');
  if (measure) measure.font = font;
  const width = Math.ceil((measure?.measureText(text).width ?? text.length * 16) + 24 * scale);
  const height = 44 * scale;

  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.font = font;
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
    ctx.fillStyle = colour;
    ctx.fillText(text, width / 2, height / 2 + 2);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }),
  );
  // Sized in world units so a label stays proportional to its node rather
  // than to the canvas resolution.
  const worldHeight = 0.26;
  sprite.scale.set((width / height) * worldHeight, worldHeight, 1);
  return sprite;
};

/** A thin circular outline -- the selection indicator, kept line-art like
 * everything else rather than a filled ring mesh. */
const makeSelectionRing = (radius: number, colour: number): THREE.LineLoop => {
  const points: THREE.Vector3[] = [];
  const segments = 48;
  for (let i = 0; i <= segments; i++) {
    const angle = (i / segments) * Math.PI * 2;
    points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0));
  }
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.LineLoop(
    geometry,
    new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity: 0.9 }),
  );
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
    scene.fog = new THREE.Fog(SCENE_BACKGROUND, 12, 30);
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
    renderer.setClearColor(SCENE_BACKGROUND, 1);
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

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.0);
    key.position.set(5, 8, 6);
    scene.add(key);

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

    // Computed once per node up front rather than inline in each loop below:
    // the connector-edge loop needs every node's halfHeight to trim lines to
    // the shape's surface, and the node loop needs the rest of it, so both
    // read from the same map instead of building the geometry twice.
    const shapes = new Map<string, NodeShape>();
    for (const node of nodes) {
      shapes.set(node.id, shapeFor(node.kind));
    }

    // Fat lines need the viewport resolution in their material; every one
    // created gets tracked so a resize can update them all.
    const fatLineMaterials: LineMaterial[] = [];
    const resolution = new THREE.Vector2(width, height);

    // Edges first so nodes draw over them. Trimmed to stop at each node's
    // surface (approximated by its halfHeight, the same "how far this shape
    // extends from its centre" figure used to place the label) rather than
    // running center-to-center -- untrimmed, a line ran straight through the
    // middle of a node's wireframe cage, visible on both sides of it instead
    // of meeting its edge.
    for (const node of nodes) {
      const from = positions.get(node.id);
      const fromShape = shapes.get(node.id);
      if (!from || !fromShape) continue;
      for (const dependency of node.depends_on) {
        const to = positions.get(dependency);
        const toShape = shapes.get(dependency);
        if (!to || !toShape) continue;

        const direction = new THREE.Vector3().subVectors(to, from);
        const distance = direction.length();
        const trimmed = distance - fromShape.halfHeight - toShape.halfHeight;
        if (trimmed <= 0) continue; // shapes touch or overlap -- nothing to draw
        direction.normalize();
        const start = from.clone().addScaledVector(direction, fromShape.halfHeight);
        const end = to.clone().addScaledVector(direction, -toShape.halfHeight);

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

    const pulsing: { material: THREE.MeshStandardMaterial; rate: number; base: number }[] = [];
    const pickable: THREE.Mesh[] = [];

    for (const node of nodes) {
      const position = positions.get(node.id);
      const shape = shapes.get(node.id);
      if (!position || !shape) continue;

      const muted = !node.checkable || node.status === 'unknown';
      const { geometry, halfHeight, ringRadius } = shape;
      const colour = STATUS_COLOURS[node.status];

      const fillMaterial = new THREE.MeshStandardMaterial({
        color: colour,
        emissive: colour,
        emissiveIntensity: muted ? 0.08 : 0.35,
        roughness: 0.45,
        metalness: 0.12,
        transparent: !node.checkable,
        opacity: node.checkable ? 1 : 0.5,
      });
      const mesh = new THREE.Mesh(geometry, fillMaterial);
      mesh.position.copy(position);
      mesh.userData = { nodeId: node.id };
      group.add(mesh);
      pickable.push(mesh);

      // A darker edge outline on top of the fill keeps each shape's
      // silhouette crisp against its neighbours rather than relying on the
      // fill colour alone to separate one node from the next.
      const outlineColour = new THREE.Color(colour).multiplyScalar(0.55);
      const edges = new THREE.EdgesGeometry(geometry, 8);
      const outline = new THREE.LineSegments(
        edges,
        new THREE.LineBasicMaterial({ color: outlineColour, transparent: true, opacity: 0.9 }),
      );
      outline.position.copy(position);
      if (!node.checkable) {
        // A dashed outline reads as "reference only, nothing to poll" rather
        // than "unknown, might be broken" -- the same grey as a genuinely
        // unreachable node would otherwise be indistinguishable from a
        // structurally non-live one (this is the "why is infra greyed out"
        // question made visible instead of asked).
        outline.material = new THREE.LineDashedMaterial({
          color: outlineColour,
          transparent: true,
          opacity: 0.7,
          dashSize: 0.06,
          gapSize: 0.05,
        });
        outline.computeLineDistances();
      }
      group.add(outline);

      const rate = STATUS_PULSE[node.status];
      if (rate > 0 && !reduceMotion && node.checkable) {
        pulsing.push({ material: fillMaterial, rate, base: 0.35 });
      }

      const label = makeLabel(node.label, muted ? '#A8B0AE' : '#FFFFFF');
      label.position.set(position.x, position.y - halfHeight - 0.27, position.z);
      group.add(label);

      if (node.id === selectedId) {
        const ring = makeSelectionRing(ringRadius, SELECTION_COLOUR);
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
      if (node) selectRef.current(node);
    };
    renderer.domElement.addEventListener('click', onClick);

    const clock = new THREE.Clock();
    let frame = 0;
    const render = () => {
      const elapsed = clock.getElapsedTime();
      for (const { material, rate, base } of pulsing) {
        material.emissiveIntensity = base + 0.45 * (0.5 + 0.5 * Math.sin(elapsed * rate * Math.PI * 2));
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
      group.traverse((object) => {
        if (
          object instanceof THREE.Mesh ||
          object instanceof THREE.Line ||
          object instanceof THREE.LineSegments ||
          object instanceof Line2
        ) {
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
