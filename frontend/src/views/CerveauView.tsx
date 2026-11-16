import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  Brain,
  CircleDot,
  Globe,
  Info,
  Network,
  RefreshCw,
  Search,
  X,
} from 'lucide-react';
import { cn } from '@/lib/cn';
import { ModeAnneaux } from './cerveau/ModeAnneaux';
import { ModeNeurone } from './cerveau/ModeNeurone';
import { ModeOrbe } from './cerveau/ModeOrbe';
import { NotePanel } from './cerveau/NotePanel';
import { useGraphe, type StatutFiltre } from './cerveau/useGraphe';
import { parseSearchSnippet } from './cerveau/utils';

type CerveauMode = 'orbe' | 'neurone' | 'anneaux';

const MODE_STORAGE_KEY = 'jarvis-cerveau-mode';

const MODE_OPTIONS: Array<{
  id: CerveauMode;
  label: string;
  icon: typeof Globe;
}> = [
  { id: 'orbe', label: 'Orbe 3D', icon: Globe },
  { id: 'neurone', label: 'Neurone 3D', icon: Network },
  { id: 'anneaux', label: 'Anneaux', icon: CircleDot },
];

const STATUT_OPTIONS: Array<{ id: StatutFiltre; label: string }> = [
  { id: 'tous', label: 'Tous' },
  { id: 'actif', label: 'Actifs' },
  { id: 'inactif', label: 'Inactifs' },
];

/**
 * Détermine le mode initial de visualisation : préférence enregistrée dans `localStorage`,
 * sinon `anneaux` par défaut sur mobile (< 640 px) et `neurone` sur desktop.
 */
function readInitialMode(): CerveauMode {
  if (typeof window !== 'undefined') {
    try {
      const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
      if (saved === 'orbe' || saved === 'neurone' || saved === 'anneaux') {
        return saved;
      }
    } catch {
      // Ignore les erreurs d'accès stockage en navigation restreinte
    }
    if (window.innerWidth < 640) {
      return 'anneaux';
    }
  }
  return 'neurone';
}

/**
 * Vue `#/cerveau` : Second cerveau interactif en 3 modes (Orbe 3D, Neurone 3D, Anneaux),
 * avec barre d'outils responsive, filtres par branche/statut, recherche FTS5 instantanée,
 * légende HUD, compteur temps réel et panneau latéral / tiroir mobile de lecture de note.
 */
export default function CerveauView() {
  const [searchParams, setSearchParams] = useSearchParams();
  const qParam = searchParams.get('q') ?? '';

  const [mode, setMode] = useState<CerveauMode>(readInitialMode);
  const [searchOpen, setSearchOpen] = useState(() => qParam.trim().length > 0);
  const [activeResultIdx, setActiveResultIdx] = useState(0);
  const [legendOpen, setLegendOpen] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 768 : true,
  );

  const searchContainerRef = useRef<HTMLDivElement | null>(null);
  const modeButtonsRef = useRef<Array<HTMLButtonElement | null>>([]);

  const {
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
  } = useGraphe(qParam);

  // Synchronise la recherche si l'utilisateur déclenche une nouvelle requête via la barre latérale (`?q=`)
  const prevQParamRef = useRef(qParam);
  useEffect(() => {
    if (qParam !== prevQParamRef.current) {
      prevQParamRef.current = qParam;
      setSearchQuery(qParam);
      if (qParam.trim()) {
        setSearchOpen(true);
      }
    }
  }, [qParam, setSearchQuery]);

  // Réinitialise l'index clavier de la liste de résultats lorsqu'ils changent
  useEffect(() => {
    setActiveResultIdx(0);
  }, [searchResults]);

  // Ferme le menu déroulant de recherche sur clic extérieur
  useEffect(() => {
    const handleMouseDown = (e: MouseEvent) => {
      if (
        searchContainerRef.current &&
        e.target instanceof Node &&
        !searchContainerRef.current.contains(e.target)
      ) {
        setSearchOpen(false);
      }
    };
    document.addEventListener('mousedown', handleMouseDown);
    return () => {
      document.removeEventListener('mousedown', handleMouseDown);
    };
  }, []);

  // Ferme le panneau de note avec Échap
  useEffect(() => {
    if (!selectedId) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !searchOpen) {
        selectNote(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [selectedId, searchOpen, selectNote]);

  const handleChangeMode = (nextMode: CerveauMode) => {
    setMode(nextMode);
    setHoveredId(null);
    try {
      window.localStorage.setItem(MODE_STORAGE_KEY, nextMode);
    } catch {
      // Ignore les erreurs localStorage
    }
  };

  const handleModeKeyDown = (
    e: React.KeyboardEvent<HTMLButtonElement>,
    idx: number,
  ) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const delta = e.key === 'ArrowRight' ? 1 : -1;
    const nextIdx = (idx + delta + MODE_OPTIONS.length) % MODE_OPTIONS.length;
    const nextMode = MODE_OPTIONS[nextIdx].id;
    handleChangeMode(nextMode);
    modeButtonsRef.current[nextIdx]?.focus();
  };

  const handleSelectSearchResult = (noteId: string) => {
    selectNote(noteId);
    setSearchOpen(false);
  };

  const handleClearSearch = () => {
    setSearchQuery('');
    setSearchOpen(false);
    if (searchParams.has('q')) {
      const next = new URLSearchParams(searchParams);
      next.delete('q');
      setSearchParams(next, { replace: true });
    }
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      setSearchOpen(false);
      return;
    }
    if (!searchOpen && (e.key === 'ArrowDown' || e.key === 'Enter')) {
      if (searchQuery.trim()) setSearchOpen(true);
      return;
    }
    if (searchResults.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveResultIdx((i) => Math.min(searchResults.length - 1, i + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveResultIdx((i) => Math.max(0, i - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const target = searchResults[activeResultIdx];
      if (target) {
        handleSelectSearchResult(target.id);
      }
    }
  };

  const allBranchesChecked = useMemo(
    () => branches.length > 0 && branches.every((b) => activeBranches.has(b.id)),
    [branches, activeBranches],
  );

  const showDropdown = searchOpen && searchQuery.trim().length > 0;

  return (
    <div className="-m-3 sm:-m-4 lg:-m-5 relative flex h-[calc(100dvh-3.5rem)] flex-col overflow-hidden bg-[#05070d]">
      {/* Barre d'outils supérieure */}
      <header className="relative z-20 shrink-0 border-b border-white/10 bg-[#0b1020]/90 px-3 py-2.5 backdrop-blur-xl sm:px-4">
        {/* Ligne 1 : Sélecteur de mode + Recherche instantanée */}
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-2 overflow-x-auto pb-0.5 md:pb-0">
            <div
              role="group"
              aria-label="Mode de visualisation du second cerveau"
              className="inline-flex shrink-0 items-center rounded-xl border border-white/10 bg-[#05070d]/85 p-1"
            >
              {MODE_OPTIONS.map((opt, idx) => {
                const Icon = opt.icon;
                const active = mode === opt.id;
                return (
                  <button
                    key={opt.id}
                    ref={(el) => {
                      modeButtonsRef.current[idx] = el;
                    }}
                    type="button"
                    aria-pressed={active}
                    onClick={() => handleChangeMode(opt.id)}
                    onKeyDown={(e) => handleModeKeyDown(e, idx)}
                    className={cn(
                      'inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs font-medium transition',
                      active
                        ? 'border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[#f8fafc] shadow-sm'
                        : 'border border-transparent text-[#94a3b8] hover:text-[#e2e8f0]',
                    )}
                  >
                    <Icon className="h-3.5 w-3.5 text-[var(--accent)]" />
                    <span>{opt.label}</span>
                  </button>
                );
              })}
            </div>

            {/* Filtre statut compact */}
            <div
              role="group"
              aria-label="Filtrer par statut des notes"
              className="inline-flex shrink-0 items-center rounded-xl border border-white/10 bg-[#05070d]/85 p-1"
            >
              {STATUT_OPTIONS.map((opt) => {
                const active = statutFiltre === opt.id;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setStatutFiltre(opt.id)}
                    className={cn(
                      'whitespace-nowrap rounded-lg px-2.5 py-1 text-[11px] font-medium transition',
                      active
                        ? 'bg-white/12 text-[#f8fafc]'
                        : 'text-[#94a3b8] hover:text-[#e2e8f0]',
                    )}
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Champ de recherche instantanée + liste déroulante */}
          <div ref={searchContainerRef} className="relative w-full md:max-w-sm">
            <Search className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-[#64748b]" />
            <input
              type="search"
              value={searchQuery}
              onFocus={() => {
                if (searchQuery.trim()) setSearchOpen(true);
              }}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                setSearchOpen(e.target.value.trim().length > 0);
              }}
              onKeyDown={handleSearchKeyDown}
              placeholder="Rechercher une note, un tag, un concept…"
              aria-label="Rechercher dans le second cerveau"
              aria-expanded={showDropdown}
              aria-controls="cerveau-search-results"
              className="w-full rounded-xl border border-white/10 bg-[#05070d]/90 py-1.5 pr-8 pl-8 text-xs text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[var(--accent)]/60 focus:outline-none"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={handleClearSearch}
                aria-label="Effacer la recherche"
                className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-0.5 text-[#94a3b8] hover:bg-white/10 hover:text-[#f8fafc]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}

            {showDropdown && (
              <div
                id="cerveau-search-results"
                role="listbox"
                aria-label="Résultats de recherche"
                className="absolute right-0 left-0 z-40 mt-1.5 max-h-72 overflow-y-auto rounded-xl border border-white/15 bg-[#0b1020]/98 p-1.5 shadow-2xl backdrop-blur-xl"
              >
                {searchLoading ? (
                  <p className="px-3 py-2 text-xs text-[#94a3b8]">
                    Recherche en cours…
                  </p>
                ) : searchResults.length === 0 ? (
                  <p className="px-3 py-2 text-xs text-[#94a3b8]">
                    Aucune note trouvée pour « {searchQuery.trim()} ».
                  </p>
                ) : (
                  searchResults.map((item, idx) => {
                    const bColor = branchColorMap[item.branche] ?? '#94a3b8';
                    const bLabel = branchLabelMap[item.branche] ?? item.branche;
                    const segments = parseSearchSnippet(item.extrait);
                    const isActiveItem = idx === activeResultIdx;

                    return (
                      <button
                        key={item.id}
                        type="button"
                        role="option"
                        aria-selected={isActiveItem}
                        onClick={() => handleSelectSearchResult(item.id)}
                        className={cn(
                          'flex w-full flex-col gap-1 rounded-lg px-3 py-2 text-left transition',
                          isActiveItem
                            ? 'bg-[var(--accent)]/15 text-[#f8fafc]'
                            : 'hover:bg-white/[0.06] text-[#e2e8f0]',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-xs font-medium text-[#f8fafc]">
                            {item.titre}
                          </span>
                          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] text-[#cbd5e1]">
                            <span
                              className="h-1.5 w-1.5 rounded-full"
                              style={{ backgroundColor: bColor }}
                            />
                            {bLabel}
                          </span>
                        </div>
                        {segments.length > 0 && (
                          <p className="line-clamp-2 text-[11px] leading-relaxed text-[#94a3b8]">
                            {segments.map((seg, sIdx) =>
                              seg.mark ? (
                                <mark
                                  key={sIdx}
                                  className="rounded bg-[var(--accent)]/30 px-0.5 font-medium text-[#f8fafc]"
                                >
                                  {seg.text}
                                </mark>
                              ) : (
                                <span key={sIdx}>{seg.text}</span>
                              ),
                            )}
                          </p>
                        )}
                      </button>
                    );
                  })
                )}
              </div>
            )}
          </div>
        </div>

        {/* Ligne 2 : Pastilles de branches en défilement horizontal (375 px mobile friendly) */}
        <div
          className="mt-2 flex items-center gap-1.5 overflow-x-auto pb-0.5"
          role="group"
          aria-label="Filtrer par branche"
        >
          {!allBranchesChecked && branches.length > 0 && (
            <button
              type="button"
              onClick={selectAllBranches}
              className="shrink-0 whitespace-nowrap rounded-full border border-[var(--accent)]/40 bg-[var(--accent)]/12 px-2.5 py-1 text-[11px] font-medium text-[var(--accent)] transition hover:bg-[var(--accent)]/20"
            >
              Toutes
            </button>
          )}
          {branches.map((b) => {
            const checked = activeBranches.has(b.id);
            return (
              <button
                key={b.id}
                type="button"
                aria-pressed={checked}
                onClick={() => toggleBranch(b.id)}
                className={cn(
                  'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-[11px] transition',
                  checked
                    ? 'border-white/20 bg-white/[0.07] text-[#f8fafc]'
                    : 'border-white/5 bg-transparent text-[#64748b] opacity-65 hover:opacity-100',
                )}
              >
                <span
                  className="h-2 w-2 shrink-0 rounded-full"
                  style={{ backgroundColor: b.couleur }}
                />
                <span>{b.libelle}</span>
                <span className="font-mono text-[10px] text-[#94a3b8]">
                  {b.nb}
                </span>
              </button>
            );
          })}
        </div>
      </header>

      {/* Zone de rendu plein écran + panneau de lecture de note */}
      <div className="relative flex min-h-0 flex-1 overflow-hidden">
        <div className="relative min-w-0 flex-1 overflow-hidden">
          {loadingGraphe ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
              <Brain className="h-9 w-9 animate-pulse text-[var(--accent)]" />
              <p className="text-xs text-[#94a3b8]">
                Chargement de la cartographie du second cerveau…
              </p>
            </div>
          ) : erreurGraphe ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
              <AlertTriangle className="h-8 w-8 text-[#f87171]" />
              <p className="text-sm text-[#e2e8f0]">{erreurGraphe}</p>
              <button
                type="button"
                onClick={() => void reloadGraphe()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Réessayer
              </button>
            </div>
          ) : filteredNoeuds.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
              <Brain className="h-8 w-8 text-[#64748b]" />
              <p className="text-sm text-[#cbd5e1]">
                {allNoeuds.length === 0
                  ? 'Aucune note indexée dans le second cerveau.'
                  : 'Aucune note ne correspond aux filtres actifs.'}
              </p>
              {allNoeuds.length > 0 && !allBranchesChecked && (
                <button
                  type="button"
                  onClick={selectAllBranches}
                  className="mt-1 rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-xs text-[#e2e8f0] hover:bg-white/10"
                >
                  Réafficher toutes les branches
                </button>
              )}
            </div>
          ) : (
            <>
              {mode === 'neurone' && (
                <ModeNeurone
                  noeuds={filteredNoeuds}
                  liens={filteredLiens}
                  branchColorMap={branchColorMap}
                  highlightedIds={highlightedIds}
                  selectedId={selectedId}
                  hoveredId={hoveredId}
                  onSelectNote={selectNote}
                  onHoverNote={setHoveredId}
                />
              )}
              {mode === 'orbe' && (
                <ModeOrbe
                  branches={branches}
                  noeuds={filteredNoeuds}
                  liens={filteredLiens}
                  branchColorMap={branchColorMap}
                  highlightedIds={highlightedIds}
                  selectedId={selectedId}
                  hoveredId={hoveredId}
                  onSelectNote={selectNote}
                  onHoverNote={setHoveredId}
                />
              )}
              {mode === 'anneaux' && (
                <ModeAnneaux
                  branches={branches}
                  noeuds={filteredNoeuds}
                  liens={filteredLiens}
                  branchColorMap={branchColorMap}
                  highlightedIds={highlightedIds}
                  selectedId={selectedId}
                  hoveredId={hoveredId}
                  onSelectNote={selectNote}
                  onHoverNote={setHoveredId}
                />
              )}
            </>
          )}

          {/* Compteur HUD flottant : N notes · M liens · K liens cassés */}
          {graphe && (
            <div
              aria-live="polite"
              className="pointer-events-none absolute top-3 left-3 z-10 flex flex-wrap items-center gap-2 rounded-full border border-white/10 bg-[#0b1020]/85 px-3 py-1 font-mono text-[11px] text-[#cbd5e1] backdrop-blur-md"
            >
              <span>
                {filteredNoeuds.length} note{filteredNoeuds.length > 1 ? 's' : ''}
              </span>
              <span className="text-[#64748b]">·</span>
              <span>
                {filteredLiens.length} lien{filteredLiens.length > 1 ? 's' : ''}
              </span>
              <span className="text-[#64748b]">·</span>
              <span
                className={
                  filteredLiensCasses.length > 0 ? 'text-[#f59e0b]' : undefined
                }
              >
                {filteredLiensCasses.length}{' '}
                {filteredLiensCasses.length > 1 ? 'liens cassés' : 'lien cassé'}
              </span>
              {highlightedIds.size > 0 && (
                <>
                  <span className="text-[#64748b]">·</span>
                  <span className="text-[var(--accent)]">
                    {highlightedIds.size} trouvée{highlightedIds.size > 1 ? 's' : ''}
                  </span>
                </>
              )}
            </div>
          )}

          {/* Légende repliable en bas à gauche */}
          <div className="absolute bottom-3 left-3 z-10 max-w-[calc(100%-5.5rem)] sm:max-w-md">
            {legendOpen ? (
              <div className="rounded-xl border border-white/10 bg-[#0b1020]/90 px-3 py-2 text-[11px] text-[#94a3b8] backdrop-blur-md">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium text-[#e2e8f0]">Légende</span>
                  <button
                    type="button"
                    onClick={() => setLegendOpen(false)}
                    aria-label="Masquer la légende"
                    className="rounded p-0.5 text-[#94a3b8] hover:bg-white/10 hover:text-[#f8fafc]"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-full bg-[var(--accent)]" />
                    Actif (&lt; 30 j)
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span className="h-2.5 w-2.5 rounded-full bg-[var(--accent)] opacity-35" />
                    Inactif (35 %)
                  </span>
                  <span>Taille ∝ √liens</span>
                  <span className="hidden sm:inline">
                    Glisser : tourner · Molette : zoom · Clic : lire
                  </span>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setLegendOpen(true)}
                aria-label="Afficher la légende"
                className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-[#0b1020]/85 px-2.5 py-1 text-[11px] text-[#cbd5e1] backdrop-blur-md hover:border-[var(--accent)]/45 hover:text-[#f8fafc]"
              >
                <Info className="h-3.5 w-3.5 text-[var(--accent)]" />
                Légende
              </button>
            )}
          </div>
        </div>

        {/* Panneau latéral droit (desktop) / tiroir bas (mobile) */}
        <NotePanel
          selectedId={selectedId}
          noteDetail={noteDetail}
          loading={loadingNote}
          erreur={erreurNote}
          allNoeuds={allNoeuds}
          noeudsById={noeudsById}
          branchColorMap={branchColorMap}
          branchLabelMap={branchLabelMap}
          onClose={() => selectNote(null)}
          onSelectNote={(id) => selectNote(id)}
          onOpenObsidian={(id) => void openInObsidian(id)}
        />
      </div>
    </div>
  );
}
