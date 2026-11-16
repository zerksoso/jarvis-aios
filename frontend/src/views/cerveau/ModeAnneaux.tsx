import { useCallback, useEffect, useMemo, useRef } from 'react';
import { LocateFixed, ZoomIn, ZoomOut } from 'lucide-react';
import type { CerveauBranche, CerveauLien, CerveauNoeud } from '@/lib/types';

export interface ModeAnneauxProps {
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

interface RingLayout {
  branchId: string;
  libelle: string;
  couleur: string;
  radius: number;
  count: number;
}

interface NodeLayout {
  node: CerveauNoeud;
  x: number;
  y: number;
  radius: number;
  color: string;
}

interface ViewTransform {
  x: number;
  y: number;
  scale: number;
  initialized: boolean;
}

const DEFAULT_COLOR = '#94a3b8';
const BASE_RING_RADIUS = 74;
const RING_STEP = 50;

/**
 * Mode « Anneaux » en Canvas 2D haute densité (HiDPI).
 *
 * Fonctionnalités :
 * - Anneaux concentriques à plat, un par branche avec libellé et compteur.
 * - Nœuds disposés sur leur anneau par ordre chronologique de `date`.
 * - Liens en courbes de Bézier quadratiques incurvées vers le centre.
 * - Zoom à la molette vers le curseur, déplacement par glisser souris/tactile, boutons d'accessibilité.
 * - Surbrillance pulsante pour la recherche, pause automatique quand l'onglet est caché.
 */
export function ModeAnneaux({
  branches,
  noeuds,
  liens,
  branchColorMap,
  highlightedIds,
  selectedId,
  hoveredId,
  onSelectNote,
  onHoverNote,
}: ModeAnneauxProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const transformRef = useRef<ViewTransform>({
    x: 0,
    y: 0,
    scale: 1,
    initialized: false,
  });

  // Calcul déterministe de la géométrie des anneaux et des nœuds triés par date
  const layout = useMemo(() => {
    const byBranch = new Map<string, CerveauNoeud[]>();
    for (const n of noeuds) {
      const arr = byBranch.get(n.branche);
      if (arr) {
        arr.push(n);
      } else {
        byBranch.set(n.branche, [n]);
      }
    }

    const rings: RingLayout[] = [];
    const positionedNodes: NodeLayout[] = [];
    const nodeMap = new Map<string, NodeLayout>();

    let ringIndex = 0;
    for (const b of branches) {
      const list = byBranch.get(b.id);
      if (!list || list.length === 0) continue;

      const sorted = [...list].sort((a, c) => {
        const cmpDate = (a.date || '').localeCompare(c.date || '');
        return cmpDate !== 0 ? cmpDate : a.id.localeCompare(c.id);
      });

      const ringRadius = BASE_RING_RADIUS + ringIndex * RING_STEP;
      const color = branchColorMap[b.id] ?? b.couleur ?? DEFAULT_COLOR;

      rings.push({
        branchId: b.id,
        libelle: b.libelle,
        couleur: color,
        radius: ringRadius,
        count: sorted.length,
      });

      const gap = 0.28;
      const span = Math.PI * 2 - gap;
      const startAngle = -Math.PI / 2 + gap / 2;

      sorted.forEach((node, idx) => {
        const angle =
          sorted.length === 1
            ? 0
            : startAngle + ((idx + 0.5) / sorted.length) * span;
        const x = Math.cos(angle) * ringRadius;
        const y = Math.sin(angle) * ringRadius;
        const radius = Math.min(12.5, 3.6 + 1.75 * Math.sqrt(Math.max(1, node.poids)));

        const rec: NodeLayout = { node, x, y, radius, color };
        positionedNodes.push(rec);
        nodeMap.set(node.id, rec);
      });

      ringIndex += 1;
    }

    const maxRadius =
      rings.length > 0 ? rings[rings.length - 1].radius + 44 : BASE_RING_RADIUS;

    return { rings, positionedNodes, nodeMap, maxRadius };
  }, [branches, noeuds, branchColorMap]);

  const layoutRef = useRef(layout);
  const liensRef = useRef(liens);
  const highlightedRef = useRef(highlightedIds);
  const selectedRef = useRef(selectedId);
  const hoveredRef = useRef(hoveredId);
  const onSelectRef = useRef(onSelectNote);
  const onHoverRef = useRef(onHoverNote);

  layoutRef.current = layout;
  liensRef.current = liens;
  highlightedRef.current = highlightedIds;
  selectedRef.current = selectedId;
  hoveredRef.current = hoveredId;
  onSelectRef.current = onSelectNote;
  onHoverRef.current = onHoverNote;

  const fitView = useCallback(() => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const w = Math.max(rect.width, 200);
    const h = Math.max(rect.height, 200);
    const minDim = Math.min(w, h);
    const scale = Math.max(
      0.28,
      Math.min(1.2, (minDim * 0.43) / Math.max(1, layoutRef.current.maxRadius)),
    );
    transformRef.current = {
      x: w / 2,
      y: h / 2,
      scale,
      initialized: true,
    };
  }, []);

  const zoomBy = useCallback((factor: number) => {
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const tr = transformRef.current;
    const nextScale = Math.max(0.22, Math.min(5, tr.scale * factor));
    const ratio = nextScale / tr.scale;
    tr.x = cx - (cx - tr.x) * ratio;
    tr.y = cy - (cy - tr.y) * ratio;
    tr.scale = nextScale;
  }, []);

  // Boucle de dessin Canvas 2D + interactions souris/tactiles + pause sur onglet caché
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const reducedMotion =
      typeof window !== 'undefined' &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    let cssWidth = 200;
    let cssHeight = 200;

    const syncCanvasSize = () => {
      const rect = container.getBoundingClientRect();
      cssWidth = Math.max(Math.floor(rect.width), 200);
      cssHeight = Math.max(Math.floor(rect.height), 200);
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(cssWidth * dpr);
      canvas.height = Math.floor(cssHeight * dpr);
      canvas.style.width = `${cssWidth}px`;
      canvas.style.height = `${cssHeight}px`;

      if (!transformRef.current.initialized) {
        fitView();
      }
    };

    syncCanvasSize();
    const resizeObserver = new ResizeObserver(syncCanvasSize);
    resizeObserver.observe(container);

    const drawPill = (
      text: string,
      cx: number,
      cy: number,
      borderColor: string,
      fontSize: number,
      alpha = 0.94,
    ) => {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
      const metrics = ctx.measureText(text);
      const padX = 8;
      const boxW = metrics.width + padX * 2;
      const boxH = fontSize + 10;
      const x = cx - boxW / 2;
      const y = cy - boxH / 2;

      ctx.beginPath();
      ctx.roundRect(x, y, boxW, boxH, 6);
      ctx.fillStyle = 'rgba(5, 7, 13, 0.9)';
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = borderColor;
      ctx.stroke();

      ctx.fillStyle = '#f8fafc';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, cx, cy + 0.5);
      ctx.restore();
    };

    const renderFrame = (now: number) => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const tr = transformRef.current;
      const { rings, positionedNodes, nodeMap } = layoutRef.current;
      const currentLinks = liensRef.current;
      const matched = highlightedRef.current;
      const searchActive = matched.size > 0;
      const selId = selectedRef.current;
      const hovId = hoveredRef.current;
      const focusId = hovId ?? selId;
      const timeSec = now * 0.001;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssWidth, cssHeight);

      ctx.save();
      ctx.translate(tr.x, tr.y);
      ctx.scale(tr.scale, tr.scale);

      // 1. Noyau central discret
      ctx.save();
      ctx.beginPath();
      ctx.arc(0, 0, 8, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(34, 211, 238, 0.14)';
      ctx.fill();
      ctx.lineWidth = 1 / tr.scale;
      ctx.strokeStyle = 'rgba(34, 211, 238, 0.35)';
      ctx.stroke();
      ctx.restore();

      // 2. Anneaux concentriques des branches
      for (const ring of rings) {
        ctx.save();
        ctx.beginPath();
        ctx.arc(0, 0, ring.radius, 0, Math.PI * 2);
        ctx.strokeStyle = ring.couleur;
        ctx.globalAlpha = 0.24;
        ctx.lineWidth = 1.1 / tr.scale;
        ctx.setLineDash([4 / tr.scale, 4 / tr.scale]);
        ctx.stroke();
        ctx.restore();
      }

      // 3. Liens en courbes de Bézier quadratiques tirées vers le centre
      const focusLinks: Array<{ src: NodeLayout; dst: NodeLayout }> = [];
      for (const l of currentLinks) {
        const src = nodeMap.get(l.source);
        const dst = nodeMap.get(l.cible);
        if (!src || !dst) continue;

        const isFocus =
          focusId !== null && (l.source === focusId || l.cible === focusId);
        if (isFocus) {
          focusLinks.push({ src, dst });
          continue;
        }

        const isSearchMatch =
          searchActive && (matched.has(l.source) || matched.has(l.cible));

        ctx.save();
        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        const ctrlX = (src.x + dst.x) * 0.08;
        const ctrlY = (src.y + dst.y) * 0.08;
        ctx.quadraticCurveTo(ctrlX, ctrlY, dst.x, dst.y);
        ctx.strokeStyle = src.color;
        ctx.globalAlpha = searchActive ? (isSearchMatch ? 0.38 : 0.05) : 0.2;
        ctx.lineWidth = 0.9 / tr.scale;
        ctx.stroke();
        ctx.restore();
      }

      // Liens du nœud survolé/sélectionné tracés par-dessus
      for (const { src, dst } of focusLinks) {
        ctx.save();
        ctx.beginPath();
        ctx.moveTo(src.x, src.y);
        const ctrlX = (src.x + dst.x) * 0.08;
        const ctrlY = (src.y + dst.y) * 0.08;
        ctx.quadraticCurveTo(ctrlX, ctrlY, dst.x, dst.y);
        ctx.strokeStyle = src.color;
        ctx.globalAlpha = 0.88;
        ctx.lineWidth = 1.8 / tr.scale;
        ctx.stroke();
        ctx.restore();
      }

      // 4. Nœuds sur leurs anneaux
      for (const rec of positionedNodes) {
        const isMatched = matched.has(rec.node.id);
        const isSelected = selId === rec.node.id;
        const isHovered = hovId === rec.node.id;

        let alpha = rec.node.actif ? 0.94 : 0.35;
        let r = rec.radius;

        if (searchActive) {
          if (isMatched) {
            const pulse = reducedMotion ? 1 : 1 + 0.18 * Math.sin(timeSec * 5.2);
            alpha = 1;
            r = rec.radius * 1.42 * pulse;
          } else if (!isSelected && !isHovered) {
            alpha = 0.14;
            r = rec.radius * 0.82;
          }
        }

        if (isSelected || isHovered) {
          alpha = 1;
          r = Math.max(r, rec.radius * 1.32);
        }

        // Halo externe pour sélection, survol ou correspondance de recherche
        if (isMatched || isSelected || isHovered) {
          ctx.save();
          ctx.beginPath();
          ctx.arc(rec.x, rec.y, r * 1.75, 0, Math.PI * 2);
          ctx.fillStyle = rec.color;
          ctx.globalAlpha = isSelected ? 0.28 : 0.2;
          ctx.fill();
          ctx.restore();
        }

        ctx.save();
        ctx.beginPath();
        ctx.arc(rec.x, rec.y, r, 0, Math.PI * 2);
        ctx.fillStyle = rec.color;
        ctx.globalAlpha = alpha;
        ctx.fill();

        if (isSelected || isHovered) {
          ctx.lineWidth = 2 / tr.scale;
          ctx.strokeStyle = '#f8fafc';
          ctx.globalAlpha = 0.95;
          ctx.stroke();
        }
        ctx.restore();
      }

      ctx.restore();

      // 5. Libellés des anneaux en coordonnées écran pour rester toujours nets
      for (const ring of rings) {
        const screenX = tr.x;
        const screenY = tr.y - ring.radius * tr.scale;
        if (
          screenX > -120 &&
          screenX < cssWidth + 120 &&
          screenY > -30 &&
          screenY < cssHeight + 30
        ) {
          drawPill(
            `${ring.libelle} (${ring.count})`,
            screenX,
            screenY,
            ring.couleur,
            10.5,
            0.85,
          );
        }
      }

      // 6. Étiquette du nœud survolé
      if (hovId) {
        const rec = nodeMap.get(hovId);
        if (rec) {
          const sx = tr.x + rec.x * tr.scale;
          const sy = tr.y + rec.y * tr.scale - rec.radius * tr.scale - 18;
          const label =
            rec.node.titre.length > 40
              ? `${rec.node.titre.slice(0, 37)}…`
              : rec.node.titre;
          drawPill(label, sx, sy, rec.color, 11.5, 0.98);
        }
      }
    };

    const pickNodeAt = (clientX: number, clientY: number): string | null => {
      const rect = canvas.getBoundingClientRect();
      const sx = clientX - rect.left;
      const sy = clientY - rect.top;
      const tr = transformRef.current;
      const wx = (sx - tr.x) / tr.scale;
      const wy = (sy - tr.y) / tr.scale;

      let closestId: string | null = null;
      let closestDist = Infinity;

      for (const rec of layoutRef.current.positionedNodes) {
        const dist = Math.hypot(wx - rec.x, wy - rec.y);
        const hitRadius = rec.radius + 7 / tr.scale;
        if (dist <= hitRadius && dist < closestDist) {
          closestDist = dist;
          closestId = rec.node.id;
        }
      }
      return closestId;
    };

    let dragging = false;
    let dragStart = { x: 0, y: 0 };
    let dragPrev = { x: 0, y: 0 };
    let movedDistance = 0;

    const handlePointerDown = (e: PointerEvent) => {
      dragging = true;
      movedDistance = 0;
      dragStart = { x: e.clientX, y: e.clientY };
      dragPrev = { x: e.clientX, y: e.clientY };
      canvas.setPointerCapture(e.pointerId);
      canvas.style.cursor = 'grabbing';
    };

    const handlePointerMove = (e: PointerEvent) => {
      if (dragging) {
        const dx = e.clientX - dragPrev.x;
        const dy = e.clientY - dragPrev.y;
        dragPrev = { x: e.clientX, y: e.clientY };
        movedDistance = Math.hypot(e.clientX - dragStart.x, e.clientY - dragStart.y);
        transformRef.current.x += dx;
        transformRef.current.y += dy;
        return;
      }

      const hitId = pickNodeAt(e.clientX, e.clientY);
      canvas.style.cursor = hitId ? 'pointer' : 'grab';
      if (hitId !== hoveredRef.current) {
        onHoverRef.current(hitId);
      }
    };

    const handlePointerUp = (e: PointerEvent) => {
      if (!dragging) return;
      dragging = false;
      if (canvas.hasPointerCapture(e.pointerId)) {
        canvas.releasePointerCapture(e.pointerId);
      }

      if (movedDistance < 5) {
        const hitId = pickNodeAt(e.clientX, e.clientY);
        onSelectRef.current(hitId);
        canvas.style.cursor = hitId ? 'pointer' : 'grab';
      } else {
        canvas.style.cursor = hoveredRef.current ? 'pointer' : 'grab';
      }
    };

    const handlePointerLeave = () => {
      if (!dragging && hoveredRef.current !== null) {
        onHoverRef.current(null);
      }
    };

    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      const tr = transformRef.current;
      const nextScale = Math.max(0.22, Math.min(5, tr.scale * factor));
      const ratio = nextScale / tr.scale;

      tr.x = sx - (sx - tr.x) * ratio;
      tr.y = sy - (sy - tr.y) * ratio;
      tr.scale = nextScale;
    };

    canvas.style.cursor = 'grab';
    canvas.addEventListener('pointerdown', handlePointerDown);
    canvas.addEventListener('pointermove', handlePointerMove);
    canvas.addEventListener('pointerup', handlePointerUp);
    canvas.addEventListener('pointercancel', handlePointerUp);
    canvas.addEventListener('pointerleave', handlePointerLeave);
    canvas.addEventListener('wheel', handleWheel, { passive: false });

    let rafId = 0;
    let paused = typeof document !== 'undefined' && document.hidden;

    const tick = (now: number) => {
      if (paused) return;
      renderFrame(now);
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
        if (!rafId) {
          rafId = window.requestAnimationFrame(tick);
        }
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    if (!paused) {
      rafId = window.requestAnimationFrame(tick);
    } else {
      renderFrame(performance.now());
    }

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      resizeObserver.disconnect();
      if (rafId) window.cancelAnimationFrame(rafId);
      canvas.removeEventListener('pointerdown', handlePointerDown);
      canvas.removeEventListener('pointermove', handlePointerMove);
      canvas.removeEventListener('pointerup', handlePointerUp);
      canvas.removeEventListener('pointercancel', handlePointerUp);
      canvas.removeEventListener('pointerleave', handlePointerLeave);
      canvas.removeEventListener('wheel', handleWheel);
    };
  }, [fitView]);

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden touch-none"
      aria-label="Graphe en anneaux concentriques du second cerveau"
    >
      <canvas ref={canvasRef} className="block h-full w-full" />

      {/* Contrôles de zoom accessibles (clavier et tactile) */}
      <div className="absolute right-3 bottom-3 z-10 flex items-center gap-1 rounded-xl border border-white/10 bg-[#0b1020]/90 p-1 backdrop-blur-md">
        <button
          type="button"
          onClick={() => zoomBy(1.22)}
          aria-label="Zoomer sur les anneaux"
          title="Zoomer"
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-[#cbd5e1] transition hover:bg-white/10 hover:text-[#f8fafc]"
        >
          <ZoomIn className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => zoomBy(0.82)}
          aria-label="Dézoomer sur les anneaux"
          title="Dézoomer"
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-[#cbd5e1] transition hover:bg-white/10 hover:text-[#f8fafc]"
        >
          <ZoomOut className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={fitView}
          aria-label="Recentrer les anneaux"
          title="Recentrer"
          className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-[#cbd5e1] transition hover:bg-white/10 hover:text-[#f8fafc]"
        >
          <LocateFixed className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
