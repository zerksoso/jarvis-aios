import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph3D, {
  type ForceGraphMethods,
  type LinkObject,
  type NodeObject,
} from 'react-force-graph-3d';
import {
  AdditiveBlending,
  Color,
  Group,
  Mesh,
  MeshBasicMaterial,
  SphereGeometry,
  Sprite,
  Vector2,
} from 'three';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import type { CerveauLien, CerveauNoeud } from '@/lib/types';
import {
  createLabelSprite,
  disposeLabelSprite,
  getEndpointId,
  mixHex,
} from './utils';

export interface ModeNeuroneProps {
  noeuds: CerveauNoeud[];
  liens: CerveauLien[];
  branchColorMap: Record<string, string>;
  highlightedIds: Set<string>;
  selectedId: string | null;
  hoveredId: string | null;
  onSelectNote: (id: string | null) => void;
  onHoverNote: (id: string | null) => void;
}

interface NeuroneNode extends CerveauNoeud {
  x?: number;
  y?: number;
  z?: number;
}

interface NeuroneLink {
  source: string | NodeObject<NeuroneNode>;
  target: string | NodeObject<NeuroneNode>;
}

interface NodeVisual {
  group: Group;
  core: Mesh;
  coreMat: MeshBasicMaterial;
  halo: Mesh;
  haloMat: MeshBasicMaterial;
  baseRadius: number;
  baseColorHex: string;
  actif: boolean;
}

const DEFAULT_COLOR = '#94a3b8';

/**
 * Mode « Neurone 3D » basé sur `react-force-graph-3d` avec post-traitement `UnrealBloomPass`.
 *
 * Fonctionnalités :
 * - Nœuds en sphères lumineuses (rayon ∝ √poids, couleur de branche, opacité 35 % si inactif).
 * - Liens fins colorés par la branche source + particules directionnelles lentes sur les liens du nœud actif.
 * - Étiquette canvas flottante au survol, approche caméra douce (800 ms) au clic.
 * - Surbrillance pulsante pour les résultats de recherche et atténuation des autres nœuds.
 * - Conservation des positions/caméra sur mise à jour live et pause sur onglet caché.
 */
export function ModeNeurone({
  noeuds,
  liens,
  branchColorMap,
  highlightedIds,
  selectedId,
  hoveredId,
  onSelectNote,
  onHoverNote,
}: ModeNeuroneProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const fgRef = useRef<ForceGraphMethods<NeuroneNode, NeuroneLink> | undefined>(undefined);

  const [size, setSize] = useState<{ width: number; height: number }>({
    width: 0,
    height: 0,
  });

  // Géométries partagées pour tenir 60 i/s sur 500+ nœuds
  const sharedGeos = useMemo(
    () => ({
      core: new SphereGeometry(1, 20, 20),
      halo: new SphereGeometry(1, 16, 16),
    }),
    [],
  );

  // Suivi des visuels Three.js par id pour mise à jour sans reconstruire la simulation
  const visualsRef = useRef<Map<string, NodeVisual>>(new Map());
  const savedPositionsRef = useRef<Map<string, { x: number; y: number; z: number }>>(
    new Map(),
  );
  const hoverSpriteRef = useRef<{ nodeId: string; sprite: Sprite } | null>(null);
  const bloomPassRef = useRef<UnrealBloomPass | null>(null);

  const highlightedRef = useRef(highlightedIds);
  const selectedRef = useRef(selectedId);
  const hoveredRef = useRef(hoveredId);
  highlightedRef.current = highlightedIds;
  selectedRef.current = selectedId;
  hoveredRef.current = hoveredId;

  // Mesure réactive du conteneur via ResizeObserver
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const update = () => {
      const rect = el.getBoundingClientRect();
      const w = Math.max(Math.floor(rect.width), 200);
      const h = Math.max(Math.floor(rect.height), 200);
      setSize((prev) => (prev.width === w && prev.height === h ? prev : { width: w, height: h }));
      if (bloomPassRef.current) {
        bloomPassRef.current.resolution.set(w, h);
      }
    };

    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => {
      observer.disconnect();
    };
  }, []);

  // Données de graphe mémorisées en conservant les positions existantes lors des mises à jour `cerveau_maj`
  const graphData = useMemo(() => {
    const validIds = new Set<string>();
    const nodes: NodeObject<NeuroneNode>[] = noeuds.map((n) => {
      validIds.add(n.id);
      const prev = savedPositionsRef.current.get(n.id);
      return prev
        ? { ...n, x: prev.x, y: prev.y, z: prev.z }
        : { ...n };
    });

    const links: LinkObject<NeuroneNode, NeuroneLink>[] = liens
      .filter((l) => validIds.has(l.source) && validIds.has(l.cible))
      .map((l) => ({ source: l.source, target: l.cible }));

    return { nodes, links };
  }, [noeuds, liens]);

  const nodeBranchMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const n of noeuds) {
      map.set(n.id, n.branche);
    }
    return map;
  }, [noeuds]);

  // Purge les matériaux GPU des nœuds supprimés lors d'une mise à jour live ou d'un filtrage
  useEffect(() => {
    const activeIds = new Set(noeuds.map((n) => n.id));
    for (const [id, visual] of visualsRef.current.entries()) {
      if (!activeIds.has(id)) {
        if (hoverSpriteRef.current?.nodeId === id) {
          hoverSpriteRef.current.sprite.removeFromParent();
          disposeLabelSprite(hoverSpriteRef.current.sprite);
          hoverSpriteRef.current = null;
        }
        visual.coreMat.dispose();
        visual.haloMat.dispose();
        visualsRef.current.delete(id);
      }
    }
  }, [noeuds]);

  // Enregistre les positions calculées par d3-force pour les préserver au prochain `cerveau_maj`
  const persistNodePositions = useCallback(() => {
    for (const n of graphData.nodes) {
      if (typeof n.x === 'number' && typeof n.y === 'number' && typeof n.z === 'number') {
        savedPositionsRef.current.set(n.id, { x: n.x, y: n.y, z: n.z });
      }
    }
  }, [graphData.nodes]);

  // Configuration initiale du renderer transparent, du bloom léger et nettoyage complet au démontage
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || size.width === 0) return;

    const renderer = fg.renderer();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);

    if (!bloomPassRef.current) {
      const bloomPass = new UnrealBloomPass(
        new Vector2(size.width, size.height),
        0.42,
        0.38,
        0.24,
      );
      fg.postProcessingComposer().addPass(bloomPass);
      bloomPassRef.current = bloomPass;
    }
  }, [size.width, size.height]);

  // Libération globale des ressources GPU au démontage uniquement
  useEffect(() => {
    const visualsMap = visualsRef.current;
    return () => {
      if (hoverSpriteRef.current) {
        hoverSpriteRef.current.sprite.removeFromParent();
        disposeLabelSprite(hoverSpriteRef.current.sprite);
        hoverSpriteRef.current = null;
      }
      for (const visual of visualsMap.values()) {
        visual.coreMat.dispose();
        visual.haloMat.dispose();
      }
      visualsMap.clear();
      sharedGeos.core.dispose();
      sharedGeos.halo.dispose();
      if (bloomPassRef.current) {
        bloomPassRef.current.dispose();
        bloomPassRef.current = null;
      }
    };
  }, [sharedGeos]);

  // Met à jour les apparences des nœuds (sélection, survol, recherche) sans recréer les géométries
  const applyVisualStates = useCallback(
    (timeSec: number) => {
      const searchActive = highlightedRef.current.size > 0;
      const selId = selectedRef.current;
      const hovId = hoveredRef.current;

      for (const [id, v] of visualsRef.current.entries()) {
        const isMatched = highlightedRef.current.has(id);
        const isSelected = selId === id;
        const isHovered = hovId === id;

        let opacity = v.actif ? 0.94 : 0.35;
        let scaleFactor = 1;
        let haloOpacity = v.actif ? 0.22 : 0.08;
        let haloScale = 1.65;

        if (searchActive) {
          if (isMatched) {
            const pulse = 1 + 0.18 * Math.sin(timeSec * 5.2);
            opacity = 1;
            scaleFactor = 1.42 * pulse;
            haloOpacity = 0.55;
            haloScale = 2.25 * pulse;
          } else if (!isSelected && !isHovered) {
            opacity = 0.14;
            scaleFactor = 0.82;
            haloOpacity = 0.02;
          }
        }

        if (isSelected) {
          opacity = 1;
          scaleFactor = Math.max(scaleFactor, 1.32);
          haloOpacity = Math.max(haloOpacity, 0.58);
          haloScale = Math.max(haloScale, 2.35);
        } else if (isHovered) {
          opacity = 1;
          scaleFactor = Math.max(scaleFactor, 1.24);
          haloOpacity = Math.max(haloOpacity, 0.48);
          haloScale = Math.max(haloScale, 2.1);
        }

        const r = v.baseRadius * scaleFactor;
        v.core.scale.set(r, r, r);
        v.coreMat.opacity = opacity;
        v.halo.scale.set(v.baseRadius * haloScale, v.baseRadius * haloScale, v.baseRadius * haloScale);
        v.haloMat.opacity = haloOpacity;
      }
    },
    [],
  );

  // Applique immédiatement quand la sélection, le survol ou la recherche change
  useEffect(() => {
    applyVisualStates(performance.now() * 0.001);
  }, [applyVisualStates, highlightedIds, selectedId, hoveredId]);

  // Attache / détache le sprite texte sur le nœud survolé
  useEffect(() => {
    if (hoverSpriteRef.current) {
      hoverSpriteRef.current.sprite.removeFromParent();
      disposeLabelSprite(hoverSpriteRef.current.sprite);
      hoverSpriteRef.current = null;
    }

    if (!hoveredId) return;
    const visual = visualsRef.current.get(hoveredId);
    const node = noeuds.find((n) => n.id === hoveredId);
    if (!visual || !node) return;

    const color = branchColorMap[node.branche] ?? DEFAULT_COLOR;
    const sprite = createLabelSprite(node.titre, color, 8.5);
    sprite.position.set(0, visual.baseRadius * 1.9 + 5.5, 0);
    visual.group.add(sprite);
    hoverSpriteRef.current = { nodeId: hoveredId, sprite };
  }, [hoveredId, noeuds, branchColorMap]);

  // Boucle de pulsation pour la recherche + pause automatique si l'onglet est caché
  useEffect(() => {
    let rafId = 0;
    let paused = typeof document !== 'undefined' && document.hidden;
    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const tick = (now: number) => {
      if (paused) return;
      if (highlightedRef.current.size > 0 && !reducedMotion) {
        applyVisualStates(now * 0.001);
      }
      rafId = window.requestAnimationFrame(tick);
    };

    const syncVisibility = () => {
      if (document.hidden) {
        paused = true;
        if (rafId) {
          window.cancelAnimationFrame(rafId);
          rafId = 0;
        }
        fgRef.current?.pauseAnimation();
      } else {
        paused = false;
        fgRef.current?.resumeAnimation();
        if (!rafId) {
          rafId = window.requestAnimationFrame(tick);
        }
      }
    };

    document.addEventListener('visibilitychange', syncVisibility);
    if (paused) {
      fgRef.current?.pauseAnimation();
    } else {
      rafId = window.requestAnimationFrame(tick);
    }

    return () => {
      document.removeEventListener('visibilitychange', syncVisibility);
      if (rafId) window.cancelAnimationFrame(rafId);
    };
  }, [applyVisualStates]);

  // Fabrique ou met à jour l'objet Three.js d'un nœud
  const buildNodeObject = useCallback(
    (nodeObj: NodeObject<NeuroneNode>) => {
      const id = String(nodeObj.id ?? '');
      const existing = visualsRef.current.get(id);
      const colorHex = branchColorMap[nodeObj.branche] ?? DEFAULT_COLOR;
      const baseRadius = Math.min(13, 2.5 * Math.sqrt(Math.max(1, nodeObj.poids)));

      if (existing) {
        existing.baseRadius = baseRadius;
        existing.baseColorHex = colorHex;
        existing.actif = nodeObj.actif;
        existing.coreMat.color.set(colorHex);
        existing.haloMat.color.set(colorHex);
        return existing.group;
      }

      const group = new Group();
      const baseColor = new Color(colorHex);

      const coreMat = new MeshBasicMaterial({
        color: baseColor,
        transparent: true,
        opacity: nodeObj.actif ? 0.94 : 0.35,
      });
      const core = new Mesh(sharedGeos.core, coreMat);
      core.scale.set(baseRadius, baseRadius, baseRadius);
      group.add(core);

      const haloMat = new MeshBasicMaterial({
        color: baseColor,
        transparent: true,
        opacity: nodeObj.actif ? 0.22 : 0.08,
        blending: AdditiveBlending,
        depthWrite: false,
      });
      const halo = new Mesh(sharedGeos.halo, haloMat);
      halo.scale.set(baseRadius * 1.65, baseRadius * 1.65, baseRadius * 1.65);
      group.add(halo);

      visualsRef.current.set(id, {
        group,
        core,
        coreMat,
        halo,
        haloMat,
        baseRadius,
        baseColorHex: colorHex,
        actif: nodeObj.actif,
      });

      return group;
    },
    [branchColorMap, sharedGeos],
  );

  const activeFocusId = hoveredId ?? selectedId;

  const getLinkColor = useCallback(
    (link: LinkObject<NeuroneNode, NeuroneLink>): string => {
      const srcId = getEndpointId(link.source);
      const dstId = getEndpointId(link.target);
      const branche = nodeBranchMap.get(srcId) ?? 'autres';
      const baseColor = branchColorMap[branche] ?? DEFAULT_COLOR;

      const isFocusLink =
        activeFocusId !== null && (srcId === activeFocusId || dstId === activeFocusId);
      if (isFocusLink) {
        return baseColor;
      }

      if (highlightedIds.size > 0) {
        const isSearchLink = highlightedIds.has(srcId) || highlightedIds.has(dstId);
        return isSearchLink
          ? mixHex(baseColor, '#05070d', 0.25)
          : mixHex(baseColor, '#05070d', 0.88);
      }

      return mixHex(baseColor, '#05070d', 0.58);
    },
    [nodeBranchMap, branchColorMap, activeFocusId, highlightedIds],
  );

  const getLinkWidth = useCallback(
    (link: LinkObject<NeuroneNode, NeuroneLink>): number => {
      if (!activeFocusId) return 0.55;
      const srcId = getEndpointId(link.source);
      const dstId = getEndpointId(link.target);
      return srcId === activeFocusId || dstId === activeFocusId ? 1.45 : 0.45;
    },
    [activeFocusId],
  );

  const getLinkParticles = useCallback(
    (link: LinkObject<NeuroneNode, NeuroneLink>): number => {
      if (!activeFocusId) return 0;
      const srcId = getEndpointId(link.source);
      const dstId = getEndpointId(link.target);
      return srcId === activeFocusId || dstId === activeFocusId ? 3 : 0;
    },
    [activeFocusId],
  );

  const getLinkParticleColor = useCallback(
    (link: LinkObject<NeuroneNode, NeuroneLink>): string => {
      const srcId = getEndpointId(link.source);
      const branche = nodeBranchMap.get(srcId) ?? 'autres';
      return branchColorMap[branche] ?? DEFAULT_COLOR;
    },
    [nodeBranchMap, branchColorMap],
  );

  const handleNodeClick = useCallback(
    (node: NodeObject<NeuroneNode>) => {
      const id = String(node.id ?? '');
      if (!id) return;
      onSelectNote(id);

      const x = node.x ?? 0;
      const y = node.y ?? 0;
      const z = node.z ?? 0;
      const dist = Math.hypot(x, y, z);
      const distance = 95;

      if (dist > 0.001) {
        const distRatio = 1 + distance / dist;
        fgRef.current?.cameraPosition(
          { x: x * distRatio, y: y * distRatio, z: z * distRatio },
          { x, y, z },
          800,
        );
      } else {
        fgRef.current?.cameraPosition(
          { x: 0, y: 0, z: distance },
          { x, y, z },
          800,
        );
      }
    },
    [onSelectNote],
  );

  const handleNodeHover = useCallback(
    (node: NodeObject<NeuroneNode> | null) => {
      onHoverNote(node ? String(node.id) : null);
    },
    [onHoverNote],
  );

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      aria-label="Graphe 3D Neurone du second cerveau"
    >
      {size.width > 0 && size.height > 0 && (
        <ForceGraph3D<NeuroneNode, NeuroneLink>
          ref={fgRef}
          width={size.width}
          height={size.height}
          graphData={graphData}
          backgroundColor="rgba(0,0,0,0)"
          showNavInfo={false}
          rendererConfig={{
            antialias: true,
            alpha: true,
            powerPreference: 'high-performance',
          }}
          nodeVal={(n) => Math.max(1, n.poids)}
          nodeThreeObject={buildNodeObject}
          linkColor={getLinkColor}
          linkWidth={getLinkWidth}
          linkOpacity={0.78}
          linkDirectionalParticles={getLinkParticles}
          linkDirectionalParticleSpeed={0.004}
          linkDirectionalParticleWidth={1.6}
          linkDirectionalParticleColor={getLinkParticleColor}
          onNodeClick={handleNodeClick}
          onNodeHover={handleNodeHover}
          onBackgroundClick={() => onSelectNote(null)}
          onEngineTick={persistNodePositions}
          onEngineStop={persistNodePositions}
        />
      )}
    </div>
  );
}
