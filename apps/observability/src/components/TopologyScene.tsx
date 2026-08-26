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
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import type { ComponentNode, NodeStatus } from '@/api/topology';

// The same Wes Anderson family as App.tsx's MUI theme and the chat app's
// palette (apps/frontend/src/theme.ts): sage, mustard, brick red, warm taupe --
// dusty and desaturated rather than the stock traffic-light red/amber/green.
const STATUS_COLOURS: Record<NodeStatus, number> = {
  ok: 0x8fbf7a,
  degraded: 0xe3b23c,
  down: 0xd2603a,
  unknown: 0x6e5f4c,
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
    scene.fog = new THREE.Fog(0x241417, 12, 30); // velvet maroon, matching the MUI background
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, -0.3, 12.5);
    camera.lookAt(0, -0.3, 0);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x241417, 1);
    mount.appendChild(renderer.domElement);

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

    // Edges first so nodes draw over them.
    for (const node of nodes) {
      const from = positions.get(node.id);
      if (!from) continue;
      for (const dependency of node.depends_on) {
        const to = positions.get(dependency);
        if (!to) continue;
        const geometry = new THREE.BufferGeometry().setFromPoints([from, to]);
        const line = new THREE.Line(
          geometry,
          new THREE.LineBasicMaterial({
            color: 0x5a4038, // dusty maroon-brown, dim against the velvet background
            transparent: true,
            opacity: 0.6,
          }),
        );
        group.add(line);
      }
    }

    const pulsing: { mesh: THREE.Mesh; rate: number; base: number }[] = [];
    const pickable: THREE.Mesh[] = [];

    for (const node of nodes) {
      const position = positions.get(node.id);
      if (!position) continue;

      // Shape encodes kind, so the graph is readable in a screenshot or by
      // someone who cannot distinguish the status colours.
      const geometry =
        node.kind === 'datastore'
          ? new THREE.CylinderGeometry(0.5, 0.5, 0.7, 24)
          : node.kind === 'external'
            ? new THREE.OctahedronGeometry(0.55)
            : node.kind === 'frontend'
              ? new THREE.BoxGeometry(0.9, 0.7, 0.7)
              : new THREE.SphereGeometry(0.55, 28, 20);

      const colour = STATUS_COLOURS[node.status];
      const material = new THREE.MeshStandardMaterial({
        color: colour,
        emissive: colour,
        emissiveIntensity: node.status === 'unknown' ? 0.05 : 0.35,
        roughness: 0.4,
        metalness: 0.15,
      });

      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(position);
      mesh.userData = { nodeId: node.id };
      group.add(mesh);
      pickable.push(mesh);

      const rate = STATUS_PULSE[node.status];
      if (rate > 0 && !reduceMotion) {
        pulsing.push({ mesh, rate, base: 0.35 });
      }

      const label = makeLabel(node.label, node.status === 'unknown' ? '#C9B79E' : '#F2E8D5');
      label.position.set(position.x, position.y - 0.82, position.z);
      group.add(label);

      if (node.id === selectedId) {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(0.85, 0.03, 12, 40),
          new THREE.MeshBasicMaterial({ color: 0x6fb3ac }), // dusty teal, matches MUI primary
        );
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
      for (const { mesh, rate, base } of pulsing) {
        const material = mesh.material as THREE.MeshStandardMaterial;
        material.emissiveIntensity =
          base + 0.45 * (0.5 + 0.5 * Math.sin(elapsed * rate * Math.PI * 2));
      }
      if (!reduceMotion) {
        // A gentle sway gives depth cues; kept small so labels stay legible.
        group.rotation.y = Math.sin(elapsed * 0.1) * 0.06;
      }
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
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener('click', onClick);
      group.traverse((object) => {
        if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
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
