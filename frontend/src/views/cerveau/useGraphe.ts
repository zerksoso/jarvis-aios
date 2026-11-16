import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api } from '@/lib/api';
import type {
  CerveauBranche,
  CerveauLien,
  CerveauNoeud,
  CerveauRechercheItem,
  Graphe,
  NoteDetail,
} from '@/lib/types';
import { wsClient } from '@/lib/ws';
import { useToasts } from '@/store/useToasts';

/** Filtre d'activité des nœuds affiché dans la vue Second cerveau. */
export type StatutFiltre = 'tous' | 'actif' | 'inactif';

/** État et actions partagés entre `CerveauView` et les trois moteurs de rendu. */
export interface UseGrapheResult {
  graphe: Graphe | null;
  loadingGraphe: boolean;
  erreurGraphe: string | null;
  branches: CerveauBranche[];
  allNoeuds: CerveauNoeud[];
  filteredNoeuds: CerveauNoeud[];
  filteredLiens: CerveauLien[];
  filteredLiensCasses: CerveauLien[];
  noeudsById: Map<string, CerveauNoeud>;
  branchColorMap: Record<string, string>;
  branchLabelMap: Record<string, string>;
  activeBranches: Set<string>;
  statutFiltre: StatutFiltre;
  searchQuery: string;
  searchResults: CerveauRechercheItem[];
  searchLoading: boolean;
  highlightedIds: Set<string>;
  selectedId: string | null;
  hoveredId: string | null;
  noteDetail: NoteDetail | null;
  loadingNote: boolean;
  erreurNote: string | null;
  setStatutFiltre: (statut: StatutFiltre) => void;
  toggleBranch: (branchId: string) => void;
  selectAllBranches: () => void;
  setSearchQuery: (query: string) => void;
  selectNote: (id: string | null) => void;
  setHoveredId: (id: string | null) => void;
  reloadGraphe: () => Promise<void>;
  openInObsidian: (id: string) => Promise<void>;
}

const SEARCH_DEBOUNCE_MS = 150;

/**
 * Hook d'orchestration du graphe du second cerveau.
 *
 * Responsabilités :
 * - Chargement initial de `GET /api/cerveau/graphe` et rechargement temps réel sur événement WS `cerveau_maj`
 *   sans réinitialiser la sélection ni la caméra des moteurs de rendu.
 * - Filtrage par branches actives et par statut (`tous | actif | inactif`).
 * - Recherche instantanée FTS5 (`GET /api/cerveau/recherche`, debounce 150 ms) produisant `highlightedIds`.
 * - Chargement du détail de la note sélectionnée (`GET /api/cerveau/note`) et ouverture dans Obsidian.
 *
 * @param initialQuery - Requête initiale issue du paramètre `?q=` de l'URL.
 * @returns État filtré, sélections, résultats de recherche et callbacks d'interaction.
 */
export function useGraphe(initialQuery = ''): UseGrapheResult {
  const addToast = useToasts((s) => s.addToast);

  const [graphe, setGraphe] = useState<Graphe | null>(null);
  const [loadingGraphe, setLoadingGraphe] = useState(true);
  const [erreurGraphe, setErreurGraphe] = useState<string | null>(null);

  const [activeBranches, setActiveBranches] = useState<Set<string>>(() => new Set());
  const knownBranchesRef = useRef<Set<string>>(new Set());

  const [statutFiltre, setStatutFiltre] = useState<StatutFiltre>('tous');
  const [searchQuery, setSearchQuery] = useState(initialQuery);
  const [searchResults, setSearchResults] = useState<CerveauRechercheItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [noteDetail, setNoteDetail] = useState<NoteDetail | null>(null);
  const [loadingNote, setLoadingNote] = useState(false);
  const [erreurNote, setErreurNote] = useState<string | null>(null);
  const [noteRefreshTick, setNoteRefreshTick] = useState(0);
  const loadedNoteIdRef = useRef<string | null>(null);

  const isMountedRef = useRef(true);
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const loadGraphe = useCallback(async (isInitial: boolean) => {
    if (isInitial) {
      setLoadingGraphe(true);
    }
    setErreurGraphe(null);
    try {
      const data = await api.get<Graphe>('/api/cerveau/graphe', { silent: !isInitial });
      if (!isMountedRef.current) return;

      const normalized: Graphe = {
        branches: Array.isArray(data?.branches) ? data.branches : [],
        noeuds: Array.isArray(data?.noeuds) ? data.noeuds : [],
        liens: Array.isArray(data?.liens) ? data.liens : [],
        liens_casses: Array.isArray(data?.liens_casses) ? data.liens_casses : [],
      };
      setGraphe(normalized);

      // Conserve les filtres choisis par l'utilisateur lors d'une mise à jour live `cerveau_maj`,
      // tout en activant automatiquement les éventuelles nouvelles branches découvertes.
      const incomingIds = normalized.branches.map((b) => b.id);
      if (knownBranchesRef.current.size === 0) {
        knownBranchesRef.current = new Set(incomingIds);
        setActiveBranches(new Set(incomingIds));
      } else {
        const added = incomingIds.filter((id) => !knownBranchesRef.current.has(id));
        if (added.length > 0) {
          for (const id of added) {
            knownBranchesRef.current.add(id);
          }
          setActiveBranches((prev) => {
            const next = new Set(prev);
            for (const id of added) {
              next.add(id);
            }
            return next;
          });
        }
      }
    } catch (err) {
      if (!isMountedRef.current) return;
      const msg =
        err instanceof Error ? err.message : 'Impossible de charger le graphe du cerveau.';
      setErreurGraphe(msg);
    } finally {
      if (isMountedRef.current && isInitial) {
        setLoadingGraphe(false);
      }
    }
  }, []);

  // Chargement initial + abonnement WebSocket `cerveau_maj`
  useEffect(() => {
    void loadGraphe(true);

    const unsubscribe = wsClient.subscribe('cerveau_maj', () => {
      void loadGraphe(false);
      setNoteRefreshTick((t) => t + 1);
    });

    return () => {
      unsubscribe();
    };
  }, [loadGraphe]);

  const reloadGraphe = useCallback(async () => {
    await loadGraphe(false);
  }, [loadGraphe]);

  // Recherche FTS5 debounced à 150 ms
  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }

    let cancelled = false;
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      void api
        .get<CerveauRechercheItem[]>(
          `/api/cerveau/recherche?q=${encodeURIComponent(q)}&limite=30`,
          { silent: true },
        )
        .then((items) => {
          if (cancelled) return;
          setSearchResults(Array.isArray(items) ? items : []);
        })
        .catch(() => {
          if (cancelled) return;
          setSearchResults([]);
        })
        .finally(() => {
          if (!cancelled) {
            setSearchLoading(false);
          }
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchQuery, graphe]);

  // Chargement du détail de la note sélectionnée (et rafraîchissement sur `cerveau_maj`)
  useEffect(() => {
    if (!selectedId) {
      loadedNoteIdRef.current = null;
      setNoteDetail(null);
      setErreurNote(null);
      setLoadingNote(false);
      return;
    }

    let cancelled = false;
    if (loadedNoteIdRef.current !== selectedId) {
      setLoadingNote(true);
    }
    setErreurNote(null);

    void api
      .get<NoteDetail>(`/api/cerveau/note?id=${encodeURIComponent(selectedId)}`, {
        silent: true,
      })
      .then((detail) => {
        if (cancelled) return;
        loadedNoteIdRef.current = detail?.id ?? selectedId;
        setNoteDetail(detail ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        loadedNoteIdRef.current = null;
        setNoteDetail(null);
        setErreurNote(
          err instanceof Error ? err.message : 'Impossible de charger cette note.',
        );
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingNote(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [selectedId, noteRefreshTick]);

  const toggleBranch = useCallback((branchId: string) => {
    setActiveBranches((prev) => {
      const next = new Set(prev);
      if (next.has(branchId)) {
        next.delete(branchId);
      } else {
        next.add(branchId);
      }
      return next;
    });
  }, []);

  const selectAllBranches = useCallback(() => {
    if (!graphe) return;
    setActiveBranches(new Set(graphe.branches.map((b) => b.id)));
  }, [graphe]);

  const selectNote = useCallback((id: string | null) => {
    setSelectedId(id);
  }, []);

  const openInObsidian = useCallback(
    async (id: string) => {
      try {
        await api.post('/api/cerveau/ouvrir', { id });
        addToast('Ouverture dans Obsidian demandée.', 'info');
      } catch {
        // Erreur affichée par le gestionnaire global API
      }
    },
    [addToast],
  );

  const branches = useMemo(() => graphe?.branches ?? [], [graphe]);
  const allNoeuds = useMemo(() => graphe?.noeuds ?? [], [graphe]);

  const noeudsById = useMemo(() => {
    const map = new Map<string, CerveauNoeud>();
    for (const n of allNoeuds) {
      map.set(n.id, n);
    }
    return map;
  }, [allNoeuds]);

  const branchColorMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const b of branches) {
      map[b.id] = b.couleur;
    }
    return map;
  }, [branches]);

  const branchLabelMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const b of branches) {
      map[b.id] = b.libelle;
    }
    return map;
  }, [branches]);

  const filteredNoeuds = useMemo(() => {
    return allNoeuds.filter((node) => {
      if (!activeBranches.has(node.branche)) return false;
      if (statutFiltre === 'actif' && !node.actif) return false;
      if (statutFiltre === 'inactif' && node.actif) return false;
      return true;
    });
  }, [allNoeuds, activeBranches, statutFiltre]);

  const filteredNoeudIds = useMemo(() => {
    const set = new Set<string>();
    for (const n of filteredNoeuds) {
      set.add(n.id);
    }
    return set;
  }, [filteredNoeuds]);

  const filteredLiens = useMemo(() => {
    if (!graphe) return [];
    return graphe.liens.filter(
      (l) => filteredNoeudIds.has(l.source) && filteredNoeudIds.has(l.cible),
    );
  }, [graphe, filteredNoeudIds]);

  const filteredLiensCasses = useMemo(() => {
    if (!graphe) return [];
    return graphe.liens_casses.filter((l) => filteredNoeudIds.has(l.source));
  }, [graphe, filteredNoeudIds]);

  const highlightedIds = useMemo(() => {
    const set = new Set<string>();
    for (const r of searchResults) {
      set.add(r.id);
    }
    return set;
  }, [searchResults]);

  return {
    graphe,
    loadingGraphe,
    erreurGraphe,
    branches,
    allNoeuds,
    filteredNoeuds,
    filteredLiens,
    filteredLiensCasses,
    noeudsById,
    branchColorMap,
    branchLabelMap,
    activeBranches,
    statutFiltre,
    searchQuery,
    searchResults,
    searchLoading,
    highlightedIds,
    selectedId,
    hoveredId,
    noteDetail,
    loadingNote,
    erreurNote,
    setStatutFiltre,
    toggleBranch,
    selectAllBranches,
    setSearchQuery,
    selectNote,
    setHoveredId,
    reloadGraphe,
    openInObsidian,
  };
}
