import { useEffect, useRef } from 'react';
import {
  AdditiveBlending,
  BufferGeometry,
  Color,
  Float32BufferAttribute,
  Group,
  IcosahedronGeometry,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Raycaster,
  Scene,
  SphereGeometry,
  Sprite,
  Vector2,
  Vector3,
  WebGLRenderer,
  WireframeGeometry,
} from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import type { CerveauBranche, CerveauLien, CerveauNoeud } from '@/lib/types';
import { createLabelSprite, disposeLabelSprite, hashString, mixHex } from './utils';

export interface ModeOrbeProps {
  branches: CerveauBranche[];
  noeuds: CerveauNoeud[];
  liens: CerveauLien[];
  branchColorMap: Record<string, string>;
  highlightedIds: Set<string>;
  selectedId: string | null;
  hoveredId: string | null;
  onSelectNote: (id: string | null) => void;
  onHoverNote: (id: string | null) => void;
}

interface OrbNodeRecord {
  node: CerveauNoeud;
  position: Vector3;
  baseRadius: number;
  colorHex: string;
  core: Mesh;
  coreMat: MeshBasicMaterial;
  halo: Mesh;
  haloMat: MeshBasicMaterial;
}

interface OrbLinkRecord {
  source: string;
  target: string;
  colorHex: string;
}

const ORB_RADIUS = 2.6;
const ARC_SEGMENTS = 18;
const DEFAULT_COLOR = '#94a3b8';

/**
 * Calcule une position déterministe sur la sphère de rayon `ORB_RADIUS`
 * dans un fuseau angulaire dédié à la branche du nœud.
 */
function computeOrbPosition(
  node: CerveauNoeud,
  branchIndex: number,
  branchCount: number,
): Vector3 {
  const safeCount = Math.max(1, branchCount);
  const sectorWidth = (Math.PI * 2) / safeCount;
  const usableWidth = sectorWidth * 0.82;
  const sectorCenter = branchIndex * sectorWidth;

  const hPhi = hashString(node.id);
  const hTheta = hashString(`${node.id}::lat`);

  const u = (hPhi % 10000) / 10000;
  const v = (hTheta % 10000) / 10000;

  const phi = sectorCenter + (u - 0.5) * usableWidth;
  // Évite les pôles extrêmes pour garder tous les nœuds lisibles
  const cosTheta = 0.84 - v * 1.68;
  const sinTheta = Math.sqrt(Math.max(0, 1 - cosTheta * cosTheta));

  return new Vector3(
    ORB_RADIUS * sinTheta * Math.cos(phi),
    ORB_RADIUS * cosTheta,
    ORB_RADIUS * sinTheta * Math.sin(phi),
  );
}

/**
 * Échantillonne un arc de grand cercle entre deux points de l'orbe, légèrement surélevé
 * au-dessus de la surface sphérique pour éviter l'occultation par la grille interne.
 */
function sampleGreatCircleArc(a: Vector3, b: Vector3, segments: number): Vector3[] {
  const ua = a.clone().normalize();
  const ub = b.clone().normalize();
  const dot = Math.max(-1, Math.min(1, ua.dot(ub)));
  const omega = Math.acos(dot);

  const points: Vector3[] = [];
  if (omega < 0.001) {
    for (let i = 0; i <= segments; i++) {
      const t = i / segments;
      points.push(a.clone().lerp(b, t));
    }
    return points;
  }

  // Cas quasi antipodal : axe de rotation orthogonal stable
  if (omega > Math.PI - 0.01) {
    const up = Math.abs(ua.y) < 0.9 ? new Vector3(0, 1, 0) : new Vector3(1, 0, 0);
    const axis = new Vector3().crossVectors(ua, up).normalize();
    for (let i = 0; i <= segments; i++) {
      const t = i / segments;
      const elevation = ORB_RADIUS * (1 + 0.1 * Math.sin(Math.PI * t));
      const p = ua.clone().applyAxisAngle(axis, Math.PI * t).multiplyScalar(elevation);
      points.push(p);
    }
    return points;
  }

  const sinOmega = Math.sin(omega);
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const wA = Math.sin((1 - t) * omega) / sinOmega;
    const wB = Math.sin(t * omega) / sinOmega;
    const elevation = ORB_RADIUS * (1 + 0.09 * Math.sin(Math.PI * t));
    const p = ua
      .clone()
      .multiplyScalar(wA)
      .addScaledVector(ub, wB)
      .normalize()
      .multiplyScalar(elevation);
    points.push(p);
  }
  return points;
}

/**
 * Mode « Orbe 3D » écrit en Three.js pur.
 *
 * Fonctionnalités :
 * - Nœuds répartis par fuseaux de branche avec positionnement déterministe par hachage.
 * - Arcs de grand cercle colorés par la branche source rendus en un appel `LineSegments`.
 * - Rotation lente (interrompue pendant le drag/survol), `OrbitControls` (rotation + zoom molette).
 * - Raycasting survol/clic, sprite canvas flottant, pulsation recherche, pause sur onglet caché.
 */
export function ModeOrbe({
  branches,
  noeuds,
  liens,
  branchColorMap,
  highlightedIds,
  selectedId,
  hoveredId,
  onSelectNote,
  onHoverNote,
}: ModeOrbeProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Refs synchrones pour accéder aux props fraîches dans la boucle RAF et les handlers DOM
  const highlightedRef = useRef(highlightedIds);
  const selectedRef = useRef(selectedId);
  const hoveredRef = useRef(hoveredId);
  const onSelectRef = useRef(onSelectNote);
  const onHoverRef = useRef(onHoverNote);

  highlightedRef.current = highlightedIds;
  selectedRef.current = selectedId;
  hoveredRef.current = hoveredId;
  onSelectRef.current = onSelectNote;
  onHoverRef.current = onHoverNote;

  const sceneRefs = useRef<{
    rootGroup: Group;
    nodesGroup: Group;
    linksGroup: Group;
    nodeRecords: Map<string, OrbNodeRecord>;
    hitMeshes: Mesh[];
    linkRecords: OrbLinkRecord[];
    linkGeometry: BufferGeometry | null;
    linkLine: LineSegments | null;
    hoverSprite: Sprite | null;
    sharedCoreGeo: SphereGeometry;
    sharedHaloGeo: SphereGeometry;
    updateVisuals: (elapsed: number) => void;
    updateLinkColors: () => void;
  } | null>(null);

  // 1. Initialisation unique du contexte WebGL, caméra et OrbitControls (caméra préservée sur cerveau_maj)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: 'high-performance',
      });
    } catch {
      return;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(dpr);
    renderer.setClearColor(0x000000, 0);

    const width = Math.max(container.clientWidth, 200);
    const height = Math.max(container.clientHeight, 200);
    renderer.setSize(width, height, false);
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.cursor = 'grab';
    container.appendChild(renderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(44, width / height, 0.1, 100);
    camera.position.set(0, 1.1, 7.6);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.enablePan = false;
    controls.minDistance = 3.2;
    controls.maxDistance = 15;
    controls.rotateSpeed = 0.75;
    controls.zoomSpeed = 0.9;

    const rootGroup = new Group();
    scene.add(rootGroup);

    // Sphère filaire translucide interne pour ancrer la lecture spatiale de l'orbe
    const shellIco = new IcosahedronGeometry(ORB_RADIUS * 0.96, 2);
    const shellWireGeo = new WireframeGeometry(shellIco);
    const shellMat = new LineBasicMaterial({
      color: new Color('#38bdf8'),
      transparent: true,
      opacity: 0.07,
      depthWrite: false,
    });
    const shellLines = new LineSegments(shellWireGeo, shellMat);
    rootGroup.add(shellLines);

    const nodesGroup = new Group();
    const linksGroup = new Group();
    rootGroup.add(linksGroup);
    rootGroup.add(nodesGroup);

    const sharedCoreGeo = new SphereGeometry(1, 18, 18);
    const sharedHaloGeo = new SphereGeometry(1, 14, 14);
    const nodeRecords = new Map<string, OrbNodeRecord>();
    const hitMeshes: Mesh[] = [];
    const linkRecords: OrbLinkRecord[] = [];

    const updateVisuals = (elapsed: number) => {
      const searchActive = highlightedRef.current.size > 0;
      const selId = selectedRef.current;
      const hovId = hoveredRef.current;

      for (const [id, rec] of nodeRecords.entries()) {
        const isMatched = highlightedRef.current.has(id);
        const isSelected = selId === id;
        const isHovered = hovId === id;

        let opacity = rec.node.actif ? 0.94 : 0.35;
        let scaleFactor = 1;
        let haloOpacity = rec.node.actif ? 0.2 : 0.06;
        let haloScale = 1.65;

        if (searchActive) {
          if (isMatched) {
            const pulse = reducedMotion ? 1 : 1 + 0.18 * Math.sin(elapsed * 5.2);
            opacity = 1;
            scaleFactor = 1.45 * pulse;
            haloOpacity = 0.55;
            haloScale = 2.3 * pulse;
          } else if (!isSelected && !isHovered) {
            opacity = 0.14;
            scaleFactor = 0.8;
            haloOpacity = 0.02;
          }
        }

        if (isSelected) {
          opacity = 1;
          scaleFactor = Math.max(scaleFactor, 1.35);
          haloOpacity = Math.max(haloOpacity, 0.62);
          haloScale = Math.max(haloScale, 2.45);
        } else if (isHovered) {
          opacity = 1;
          scaleFactor = Math.max(scaleFactor, 1.25);
          haloOpacity = Math.max(haloOpacity, 0.5);
          haloScale = Math.max(haloScale, 2.15);
        }

        const r = rec.baseRadius * scaleFactor;
        rec.core.scale.set(r, r, r);
        rec.coreMat.opacity = opacity;
        rec.halo.scale.set(
          rec.baseRadius * haloScale,
          rec.baseRadius * haloScale,
          rec.baseRadius * haloScale,
        );
        rec.haloMat.opacity = haloOpacity;
      }
    };

    const updateLinkColors = () => {
      const state = sceneRefs.current;
      if (!state || !state.linkGeometry) return;
      const colorAttr = state.linkGeometry.getAttribute('color');
      if (!(colorAttr instanceof Float32BufferAttribute)) return;

      const focusId = hoveredRef.current ?? selectedRef.current;
      const searchActive = highlightedRef.current.size > 0;
      const tmp = new Color();
      const vertsPerLink = ARC_SEGMENTS * 2;

      for (let i = 0; i < state.linkRecords.length; i++) {
        const link = state.linkRecords[i];
        const isFocus =
          focusId !== null && (link.source === focusId || link.target === focusId);

        let hex: string;
        if (isFocus) {
          hex = link.colorHex;
        } else if (searchActive) {
          const isMatch =
            highlightedRef.current.has(link.source) ||
            highlightedRef.current.has(link.target);
          hex = isMatch
            ? mixHex(link.colorHex, '#05070d', 0.25)
            : mixHex(link.colorHex, '#05070d', 0.9);
        } else {
          hex = mixHex(link.colorHex, '#05070d', 0.58);
        }

        tmp.set(hex);
        const offset = i * vertsPerLink * 3;
        for (let v = 0; v < vertsPerLink; v++) {
          colorAttr.array[offset + v * 3] = tmp.r;
          colorAttr.array[offset + v * 3 + 1] = tmp.g;
          colorAttr.array[offset + v * 3 + 2] = tmp.b;
        }
      }
      colorAttr.needsUpdate = true;
    };

    sceneRefs.current = {
      rootGroup,
      nodesGroup,
      linksGroup,
      nodeRecords,
      hitMeshes,
      linkRecords,
      linkGeometry: null,
      linkLine: null,
      hoverSprite: null,
      sharedCoreGeo,
      sharedHaloGeo,
      updateVisuals,
      updateLinkColors,
    };

    // Raycasting pour survol et clic (distingue le glisser OrbitControls d'un clic réel)
    const raycaster = new Raycaster();
    const pointerNdc = new Vector2();
    let isDragging = false;
    let pointerDownPos: { x: number; y: number } | null = null;

    const handleControlStart = () => {
      isDragging = true;
      renderer.domElement.style.cursor = 'grabbing';
    };
    const handleControlEnd = () => {
      isDragging = false;
      renderer.domElement.style.cursor = hoveredRef.current ? 'pointer' : 'grab';
    };
    controls.addEventListener('start', handleControlStart);
    controls.addEventListener('end', handleControlEnd);

    const pickNodeAt = (clientX: number, clientY: number): string | null => {
      const rect = renderer.domElement.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;
      pointerNdc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointerNdc.y = -((clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(pointerNdc, camera);
      const hits = raycaster.intersectObjects(hitMeshes, false);
      if (hits.length === 0) return null;
      const hitId = hits[0]?.object.userData.noteId;
      return typeof hitId === 'string' ? hitId : null;
    };

    const handlePointerMove = (e: PointerEvent) => {
      if (isDragging) return;
      const hitId = pickNodeAt(e.clientX, e.clientY);
      renderer.domElement.style.cursor = hitId ? 'pointer' : 'grab';
      if (hitId !== hoveredRef.current) {
        onHoverRef.current(hitId);
      }
    };

    const handlePointerLeave = () => {
      if (hoveredRef.current !== null) {
        onHoverRef.current(null);
      }
    };

    const handlePointerDown = (e: PointerEvent) => {
      pointerDownPos = { x: e.clientX, y: e.clientY };
    };

    const handlePointerUp = (e: PointerEvent) => {
      if (!pointerDownPos) return;
      const dist = Math.hypot(
        e.clientX - pointerDownPos.x,
        e.clientY - pointerDownPos.y,
      );
      pointerDownPos = null;
      if (dist >= 5) return;

      const hitId = pickNodeAt(e.clientX, e.clientY);
      onSelectRef.current(hitId);
    };

    renderer.domElement.addEventListener('pointermove', handlePointerMove);
    renderer.domElement.addEventListener('pointerleave', handlePointerLeave);
    renderer.domElement.addEventListener('pointerdown', handlePointerDown);
    renderer.domElement.addEventListener('pointerup', handlePointerUp);

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = Math.max(entry.contentRect.width, 200);
        const h = Math.max(entry.contentRect.height, 200);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
      }
    });
    resizeObserver.observe(container);

    let rafId = 0;
    let lastTs = performance.now();
    let elapsed = 0;
    let paused = typeof document !== 'undefined' && document.hidden;

    const tick = (now: number) => {
      if (paused) return;
      const dt = Math.min((now - lastTs) / 1000, 0.05);
      lastTs = now;
      elapsed += dt;

      if (!reducedMotion && !isDragging && hoveredRef.current === null) {
        rootGroup.rotation.y += dt * 0.07;
      }

      if (highlightedRef.current.size > 0 && !reducedMotion) {
        updateVisuals(elapsed);
      }

      controls.update();
      renderer.render(scene, camera);
      rafId = window.requestAnimationFrame(tick);
    };

    const startLoop = () => {
      if (rafId) window.cancelAnimationFrame(rafId);
      lastTs = performance.now();
      rafId = window.requestAnimationFrame(tick);
    };

    const handleVisibility = () => {
      if (document.hidden) {
        paused = true;
        if (rafId) {
          window.cancelAnimationFrame(rafId);
          rafId = 0;
        }
      } else {
        paused = false;
        startLoop();
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    if (!paused) {
      startLoop();
    } else {
      renderer.render(scene, camera);
    }

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      resizeObserver.disconnect();
      if (rafId) window.cancelAnimationFrame(rafId);

      controls.removeEventListener('start', handleControlStart);
      controls.removeEventListener('end', handleControlEnd);
      controls.dispose();

      renderer.domElement.removeEventListener('pointermove', handlePointerMove);
      renderer.domElement.removeEventListener('pointerleave', handlePointerLeave);
      renderer.domElement.removeEventListener('pointerdown', handlePointerDown);
      renderer.domElement.removeEventListener('pointerup', handlePointerUp);

      if (sceneRefs.current?.hoverSprite) {
        sceneRefs.current.hoverSprite.removeFromParent();
        disposeLabelSprite(sceneRefs.current.hoverSprite);
      }
      for (const rec of nodeRecords.values()) {
        rec.coreMat.dispose();
        rec.haloMat.dispose();
      }
      nodeRecords.clear();
      if (sceneRefs.current?.linkGeometry) {
        sceneRefs.current.linkGeometry.dispose();
      }
      if (sceneRefs.current?.linkLine) {
        const mat = sceneRefs.current.linkLine.material;
        if (mat instanceof LineBasicMaterial) mat.dispose();
      }
      sharedCoreGeo.dispose();
      sharedHaloGeo.dispose();
      shellIco.dispose();
      shellWireGeo.dispose();
      shellMat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
      sceneRefs.current = null;
    };
  }, []);

  // 2. Reconstruction des nœuds et des arcs de grand cercle lorsque les données filtrées changent
  useEffect(() => {
    const state = sceneRefs.current;
    if (!state) return;

    // Nettoie les précédents nœuds/liens sans toucher à la caméra ni aux OrbitControls
    if (state.hoverSprite) {
      state.hoverSprite.removeFromParent();
      disposeLabelSprite(state.hoverSprite);
      state.hoverSprite = null;
    }
    for (const rec of state.nodeRecords.values()) {
      state.nodesGroup.remove(rec.core);
      state.nodesGroup.remove(rec.halo);
      rec.coreMat.dispose();
      rec.haloMat.dispose();
    }
    state.nodeRecords.clear();
    state.hitMeshes.length = 0;

    if (state.linkLine) {
      state.linksGroup.remove(state.linkLine);
      const mat = state.linkLine.material;
      if (mat instanceof LineBasicMaterial) mat.dispose();
      state.linkLine = null;
    }
    if (state.linkGeometry) {
      state.linkGeometry.dispose();
      state.linkGeometry = null;
    }
    state.linkRecords.length = 0;

    // Ordre stable des fuseaux de branche
    const branchOrder = new Map<string, number>();
    branches.forEach((b, idx) => branchOrder.set(b.id, idx));
    const totalBranches = Math.max(1, branches.length);

    for (const node of noeuds) {
      const branchIdx = branchOrder.get(node.branche) ?? 0;
      const position = computeOrbPosition(node, branchIdx, totalBranches);
      const colorHex = branchColorMap[node.branche] ?? DEFAULT_COLOR;
      const baseRadius = Math.min(0.18, 0.048 * Math.sqrt(Math.max(1, node.poids)));
      const color = new Color(colorHex);

      const coreMat = new MeshBasicMaterial({
        color,
        transparent: true,
        opacity: node.actif ? 0.94 : 0.35,
      });
      const core = new Mesh(state.sharedCoreGeo, coreMat);
      core.position.copy(position);
      core.scale.set(baseRadius, baseRadius, baseRadius);
      core.userData.noteId = node.id;
      state.nodesGroup.add(core);
      state.hitMeshes.push(core);

      const haloMat = new MeshBasicMaterial({
        color,
        transparent: true,
        opacity: node.actif ? 0.2 : 0.06,
        blending: AdditiveBlending,
        depthWrite: false,
      });
      const halo = new Mesh(state.sharedHaloGeo, haloMat);
      halo.position.copy(position);
      halo.scale.set(baseRadius * 1.65, baseRadius * 1.65, baseRadius * 1.65);
      state.nodesGroup.add(halo);

      state.nodeRecords.set(node.id, {
        node,
        position,
        baseRadius,
        colorHex,
        core,
        coreMat,
        halo,
        haloMat,
      });
    }

    // Construction groupée des arcs de grand cercle (1 seul draw call LineSegments)
    const validLinks = liens.filter(
      (l) => state.nodeRecords.has(l.source) && state.nodeRecords.has(l.cible),
    );
    if (validLinks.length > 0) {
      const vertsPerLink = ARC_SEGMENTS * 2;
      const positions = new Float32Array(validLinks.length * vertsPerLink * 3);
      const colors = new Float32Array(validLinks.length * vertsPerLink * 3);

      validLinks.forEach((link, linkIdx) => {
        const src = state.nodeRecords.get(link.source);
        const dst = state.nodeRecords.get(link.cible);
        if (!src || !dst) return;

        state.linkRecords.push({
          source: link.source,
          target: link.cible,
          colorHex: src.colorHex,
        });

        const pts = sampleGreatCircleArc(src.position, dst.position, ARC_SEGMENTS);
        const baseOffset = linkIdx * vertsPerLink * 3;
        for (let s = 0; s < ARC_SEGMENTS; s++) {
          const p0 = pts[s];
          const p1 = pts[s + 1];
          const segOffset = baseOffset + s * 6;
          positions[segOffset] = p0.x;
          positions[segOffset + 1] = p0.y;
          positions[segOffset + 2] = p0.z;
          positions[segOffset + 3] = p1.x;
          positions[segOffset + 4] = p1.y;
          positions[segOffset + 5] = p1.z;
        }
      });

      const linkGeo = new BufferGeometry();
      linkGeo.setAttribute('position', new Float32BufferAttribute(positions, 3));
      linkGeo.setAttribute('color', new Float32BufferAttribute(colors, 3));

      const linkMat = new LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.78,
        depthWrite: false,
      });

      const line = new LineSegments(linkGeo, linkMat);
      state.linksGroup.add(line);
      state.linkGeometry = linkGeo;
      state.linkLine = line;
    }

    state.updateVisuals(performance.now() * 0.001);
    state.updateLinkColors();
  }, [branches, noeuds, liens, branchColorMap]);

  // 3. Réaction immédiate au survol, à la sélection et à la recherche
  useEffect(() => {
    const state = sceneRefs.current;
    if (!state) return;

    state.updateVisuals(performance.now() * 0.001);
    state.updateLinkColors();

    if (state.hoverSprite) {
      state.hoverSprite.removeFromParent();
      disposeLabelSprite(state.hoverSprite);
      state.hoverSprite = null;
    }

    if (hoveredId) {
      const rec = state.nodeRecords.get(hoveredId);
      if (rec) {
        const sprite = createLabelSprite(rec.node.titre, rec.colorHex, 0.34);
        const normal = rec.position.clone().normalize();
        sprite.position
          .copy(rec.position)
          .addScaledVector(normal, rec.baseRadius * 2 + 0.25);
        state.rootGroup.add(sprite);
        state.hoverSprite = sprite;
      }
    }
  }, [highlightedIds, selectedId, hoveredId]);

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      aria-label="Graphe 3D Orbe du second cerveau"
    />
  );
}
