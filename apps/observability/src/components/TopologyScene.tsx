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
 * Nodes render as unfilled wireframes rather than solid shapes — a plain
 * line-art look reads better against the scanline background than a lit,
 * filled mesh, and it means a node's silhouette never fights its status
 * colour for attention. A node still carries an invisible solid twin for
 * raycasting: a pure line has near-zero hit area, and picking one exactly
 * would be unreasonably fussy.
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

// The Retro Teal family from App.tsx's MUI theme and the chat app's default
// preset (apps/frontend/src/theme.ts): teal, amber, coral, muted teal-grey --
// dusty and desaturated rather than the stock traffic-light red/amber/green.
const STATUS_COLOURS: Record<NodeStatus, number> = {
  ok: 0x34d399,
  degraded: 0xfbbf24,
  down: 0xf97362,
  unknown: 0x3a5049,
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

const CONNECTOR_COLOUR = 0x5fd4c0; // bright enough to read against the near-black background
const CONNECTOR_WIDTH_PX = 2.5;

/** Shrinks every primitive below by the same factor -- "the same shapes as
 * before, a bit smaller." */
const NODE_SCALE = 0.8;

interface NodeShape {
  geometry: THREE.BufferGeometry;
  /** A sphere has no edges an EdgesGeometry threshold will ever catch (every
   * adjacent facet is near-coplanar) -- it needs every triangle edge drawn,
   * not just the sharp ones, or it renders as next to nothing. */
  wireframe: 'edges' | 'full';
  /** Half the shape's vertical extent, for label placement below it. */
  halfHeight: number;
  /** Selection-ring radius, sized to sit just outside the shape. */
  ringRadius: number;
}

/** The original per-kind primitives (shape encodes kind, readable even
 * without colour), just built small enough to render as a clean wireframe. */
const shapeFor = (kind: string): NodeShape => {
  if (kind === 'datastore') {
    const radius = 0.5 * NODE_SCALE;
    const height = 0.7 * NODE_SCALE;
    return {
      geometry: new THREE.CylinderGeometry(radius, radius, height, 24),
      wireframe: 'edges',
      halfHeight: height / 2,
      ringRadius: radius * 1.5,
    };
  }
  if (kind === 'external') {
    const radius = 0.55 * NODE_SCALE;
    return {
      geometry: new THREE.OctahedronGeometry(radius),
      wireframe: 'edges',
      halfHeight: radius,
      ringRadius: radius * 1.5,
    };
  }
  if (kind === 'frontend') {
    const w = 0.9 * NODE_SCALE;
    const h = 0.7 * NODE_SCALE;
    const d = 0.7 * NODE_SCALE;
    return {
      geometry: new THREE.BoxGeometry(w, h, d),
      wireframe: 'edges',
      halfHeight: h / 2,
      ringRadius: Math.max(w, d) * 0.9,
    };
  }
  // service
  const radius = 0.55 * NODE_SCALE;
  return {
    geometry: new THREE.SphereGeometry(radius, 28, 20),
    wireframe: 'full',
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
    scene.fog = new THREE.Fog(0x071613, 12, 30); // near-black teal, matching the MUI background
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
    renderer.setClearColor(0x071613, 1);
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

    // Fat lines need the viewport resolution in their material; every one
    // created gets tracked so a resize can update them all.
    const fatLineMaterials: LineMaterial[] = [];
    const resolution = new THREE.Vector2(width, height);

    // Edges first so nodes draw over them.
    for (const node of nodes) {
      const from = positions.get(node.id);
      if (!from) continue;
      for (const dependency of node.depends_on) {
        const to = positions.get(dependency);
        if (!to) continue;
        const geometry = new LineGeometry();
        geometry.setPositions([from.x, from.y, from.z, to.x, to.y, to.z]);
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

    const pulsing: { material: THREE.LineBasicMaterial; rate: number; base: number }[] = [];
    const pickable: THREE.Mesh[] = [];

    for (const node of nodes) {
      const position = positions.get(node.id);
      if (!position) continue;

      const muted = !node.checkable || node.status === 'unknown';
      const { geometry, wireframe: wireframeMode, halfHeight, ringRadius } = shapeFor(node.kind);

      // An invisible solid twin carries the raycast hit-test: a bare wireframe
      // has almost no area to click, and picking one precisely would be
      // needlessly fussy for something this small on screen.
      const hitTarget = new THREE.Mesh(
        geometry,
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
      );
      hitTarget.position.copy(position);
      hitTarget.userData = { nodeId: node.id };
      group.add(hitTarget);
      pickable.push(hitTarget);

      const colour = STATUS_COLOURS[node.status];
      const edges =
        wireframeMode === 'edges'
          ? new THREE.EdgesGeometry(geometry, 8)
          : new THREE.WireframeGeometry(geometry);
      const lineMaterial = new THREE.LineBasicMaterial({
        color: colour,
        transparent: true,
        opacity: node.checkable ? 0.95 : 0.5,
      });
      const wireframe = new THREE.LineSegments(edges, lineMaterial);
      wireframe.position.copy(position);
      if (!node.checkable) {
        // A dashed outline reads as "reference only, nothing to poll" rather
        // than "unknown, might be broken" -- the same grey as a genuinely
        // unreachable node would otherwise be indistinguishable from a
        // structurally non-live one (this is the "why is infra greyed out"
        // question made visible instead of asked).
        const dashedMaterial = new THREE.LineDashedMaterial({
          color: colour,
          transparent: true,
          opacity: 0.5,
          dashSize: 0.06,
          gapSize: 0.05,
        });
        wireframe.material = dashedMaterial;
        wireframe.computeLineDistances();
      }
      group.add(wireframe);

      const rate = STATUS_PULSE[node.status];
      if (rate > 0 && !reduceMotion && node.checkable) {
        pulsing.push({ material: lineMaterial, rate, base: 0.55 });
      }

      const label = makeLabel(node.label, muted ? '#8FBDB4' : '#E4FFFB');
      label.position.set(position.x, position.y - halfHeight - 0.27, position.z);
      group.add(label);

      if (node.id === selectedId) {
        const ring = makeSelectionRing(ringRadius, 0x2dd4bf); // bright teal, matches MUI primary
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
        material.opacity = base + 0.45 * (0.5 + 0.5 * Math.sin(elapsed * rate * Math.PI * 2));
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
