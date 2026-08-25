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

const STATUS_COLOURS: Record<NodeStatus, number> = {
  ok: 0x10b981,
  degraded: 0xf59e0b,
  down: 0xef4444,
  unknown: 0x4b5563,
};

/** Pulses per second. Healthy is still; trouble draws the eye. */
const STATUS_PULSE: Record<NodeStatus, number> = {
  ok: 0,
  degraded: 1.2,
  down: 2.6,
  unknown: 0,
};

const TIER_Y: Record<string, number> = {
  frontend: 2.4,
  service: 0,
  datastore: -2.4,
  external: -2.4,
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
    scene.fog = new THREE.Fog(0x0b0f19, 12, 30);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(0, 0.5, 11);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true });
    } catch {
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x0b0f19, 1);
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
      // Datastores and externals share a tier, so offset externals forward to
      // keep them visually distinct without a fourth row.
      const z = tier === 'external' ? -1.6 : 0;
      const span = Math.max(members.length - 1, 1);
      members.forEach((node, i) => {
        const x = members.length === 1 ? 0 : (i / span - 0.5) * 8;
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
            color: 0x334155,
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

      if (node.id === selectedId) {
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(0.85, 0.03, 12, 40),
          new THREE.MeshBasicMaterial({ color: 0x818cf8 }),
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
        group.rotation.y = Math.sin(elapsed * 0.12) * 0.22;
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
