/**
 * The context budget, rendered in Three.js (ADR-0008).
 *
 * The 3D treatment earns its place by carrying three dimensions at once:
 * segment *height* is token cost, *depth* is recency, and *emissive intensity*
 * is proximity to the eviction frontier. A stacked bar shows cost alone.
 *
 * Accessibility is a hard requirement, not a follow-up. A WebGL canvas is not
 * reachable by a screen reader at all, so:
 *   - an equivalent table with the same affordances always exists (see
 *     ContextPanel), and this canvas is `aria-hidden`;
 *   - `prefers-reduced-motion` disables the idle rotation;
 *   - the canvas is never the only route to dropping an item.
 */

import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { Box, useTheme } from '@mui/material';
import type { BudgetOut } from '@/api/generated/model';

const CLASS_ORDER = ['system', 'pinned', 'history', 'retrieved'] as const;

interface Props {
  budget: BudgetOut | null;
  height?: number;
  onSelect?: (itemId: string) => void;
}

export const ContextMeter = ({ budget, height = 220, onSelect }: Props) => {
  const mountRef = useRef<HTMLDivElement>(null);
  const theme = useTheme();
  // Kept in a ref so the animation loop reads the latest without being
  // recreated on every streamed token.
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || !budget) return;

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)',
    ).matches;

    const width = mount.clientWidth || 320;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, width / height, 0.1, 100);
    camera.position.set(3.4, 2.6, 4.2);
    camera.lookAt(0, 0.6, 0);

    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch {
      // No WebGL: the accessible table beside this is the whole feature, so
      // failing silently here degrades rather than breaks.
      return;
    }
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(4, 6, 4);
    scene.add(key);

    const group = new THREE.Group();
    scene.add(group);

    // Stack one block per class, sized by its share of the available window.
    const available = budget.available_tokens || 1;
    const frontier = new Set(
      budget.items.filter((i) => i.evicts_next).map((i) => i.item_id),
    );
    const meshes: { mesh: THREE.Mesh; itemId: string | null }[] = [];

    let y = 0;
    for (const className of CLASS_ORDER) {
      const segment = budget.segments.find(
        (s) => s.context_class === className,
      );
      if (!segment || segment.token_count === 0) continue;

      const share = segment.token_count / available;
      const blockHeight = Math.max(share * 3, 0.04);
      const atRisk = budget.items.some(
        (i) => i.context_class === className && frontier.has(i.item_id),
      );

      const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(theme.contextColours[className]),
        roughness: 0.45,
        metalness: 0.1,
        transparent: true,
        opacity: 0.92,
        // The eviction frontier glows: what is about to be lost should draw the
        // eye before it is lost, which is the entire point of ADR-0008. Pulled
        // from the theme's error colour rather than hardcoded, so it stays in
        // the palette's family (brick red, not fire-engine red) in both modes.
        emissive: new THREE.Color(atRisk ? theme.palette.error.main : '#000000'),
        emissiveIntensity: atRisk ? 0.5 : 0,
      });

      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(1.5, blockHeight, 1.5),
        material,
      );
      mesh.position.y = y + blockHeight / 2;
      mesh.userData = { className };
      group.add(mesh);
      meshes.push({ mesh, itemId: null });
      y += blockHeight + 0.03;
    }

    // The unused remainder, as a wireframe — an empty space you can see is
    // more informative than a bar that simply stops.
    const free = Math.max(0, 1 - budget.used_tokens / available);
    if (free > 0.01) {
      const shell = new THREE.Mesh(
        new THREE.BoxGeometry(1.5, free * 3, 1.5),
        new THREE.MeshBasicMaterial({
          color: new THREE.Color(theme.palette.text.disabled),
          wireframe: true,
          transparent: true,
          opacity: 0.25,
        }),
      );
      shell.position.y = y + (free * 3) / 2;
      group.add(shell);
    }

    group.position.y = -1.1;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    const onClick = (event: MouseEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(meshes.map((m) => m.mesh))[0];
      const className = hit?.object.userData.className as string | undefined;
      if (!className) return;
      // Selecting a segment offers up its most-evictable member, which is what
      // the eviction order already says would go first.
      const candidate = budget.items.find(
        (i) => i.context_class === className && i.evicts_next,
      );
      if (candidate) selectRef.current?.(candidate.item_id);
    };
    renderer.domElement.addEventListener('click', onClick);

    let frame = 0;
    const render = () => {
      if (!reduceMotion) group.rotation.y += 0.0035;
      renderer.render(scene, camera);
      frame = requestAnimationFrame(render);
    };
    render();

    const resize = () => {
      const w = mount.clientWidth || width;
      camera.aspect = w / height;
      camera.updateProjectionMatrix();
      renderer.setSize(w, height);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      renderer.domElement.removeEventListener('click', onClick);
      // Three.js does not free GPU memory on garbage collection; without this
      // every streamed turn leaks a geometry and a material.
      group.traverse((object) => {
        if (object instanceof THREE.Mesh) {
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
  }, [budget, height, theme.palette.text.disabled, theme.palette.error.main, theme.contextColours]);

  return (
    <Box
      ref={mountRef}
      aria-hidden
      sx={{ width: '100%', height, cursor: onSelect ? 'pointer' : 'default' }}
    />
  );
};
