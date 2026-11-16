import { useMemo } from 'react';
import {
  ArrowDownLeft,
  ArrowUpRight,
  Calendar,
  ExternalLink,
  FileText,
  Tag,
  X,
} from 'lucide-react';
import ReactMarkdown, {
  defaultUrlTransform,
  type Components,
} from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { cn } from '@/lib/cn';
import type { CerveauNoeud, NoteDetail } from '@/lib/types';
import {
  preprocessWikiLinks,
  resolveWikiTarget,
  stripFrontmatter,
} from './utils';

export interface NotePanelProps {
  selectedId: string | null;
  noteDetail: NoteDetail | null;
  loading: boolean;
  erreur: string | null;
  allNoeuds: CerveauNoeud[];
  noeudsById: Map<string, CerveauNoeud>;
  branchColorMap: Record<string, string>;
  branchLabelMap: Record<string, string>;
  onClose: () => void;
  onSelectNote: (id: string) => void;
  onOpenObsidian: (id: string) => void;
}

const REMARK_PLUGINS = [remarkGfm];

const STATUT_BADGE_CLASS: Record<string, string> = {
  actif: 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]',
  brouillon: 'border-[#f59e0b]/40 bg-[#f59e0b]/10 text-[#f59e0b]',
  archive: 'border-white/15 bg-white/5 text-[#94a3b8]',
};

/**
 * Panneau latéral droit (desktop) / tiroir bas (mobile) affichant la note sélectionnée :
 * métadonnées, rendu Markdown GFM avec `[[liens]]` cliquables, liens entrants/sortants
 * et bouton d'ouverture directe dans Obsidian.
 */
export function NotePanel({
  selectedId,
  noteDetail,
  loading,
  erreur,
  allNoeuds,
  noeudsById,
  branchColorMap,
  branchLabelMap,
  onClose,
  onSelectNote,
  onOpenObsidian,
}: NotePanelProps) {
  const preparedMarkdown = useMemo(() => {
    if (!noteDetail?.contenu_md) return '';
    return preprocessWikiLinks(stripFrontmatter(noteDetail.contenu_md));
  }, [noteDetail?.contenu_md]);

  const markdownComponents = useMemo<Components>(
    () => ({
      a: ({ href, children }) => {
        if (href?.startsWith('jarvis-note://')) {
          const rawTarget = decodeURIComponent(
            href.slice('jarvis-note://'.length),
          );
          const resolvedId = resolveWikiTarget(rawTarget, allNoeuds);
          if (resolvedId) {
            return (
              <button
                type="button"
                onClick={() => onSelectNote(resolvedId)}
                className="inline cursor-pointer font-medium text-[var(--accent)] underline decoration-[var(--accent)]/50 underline-offset-2 transition hover:decoration-[var(--accent)]"
              >
                {children}
              </button>
            );
          }
          return (
            <span
              title="Lien cassé : note introuvable dans le second cerveau"
              className="inline rounded bg-[#f87171]/10 px-1 text-[#f87171] line-through decoration-[#f87171]/50"
            >
              {children}
            </span>
          );
        }

        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [allNoeuds, onSelectNote],
  );

  if (!selectedId) {
    return null;
  }

  const branchColor = noteDetail
    ? branchColorMap[noteDetail.branche] ?? '#94a3b8'
    : '#94a3b8';
  const branchLabel = noteDetail
    ? branchLabelMap[noteDetail.branche] ?? noteDetail.branche
    : '';

  return (
    <>
      {/* Voile mobile pour fermer le tiroir bas au toucher */}
      <div
        className="fixed inset-0 z-20 bg-[#05070d]/55 backdrop-blur-xs sm:hidden"
        onClick={onClose}
        aria-hidden="true"
      />

      <aside
        aria-label="Lecture de la note"
        className={cn(
          'fixed inset-x-0 bottom-0 z-30 flex max-h-[72dvh] flex-col overflow-hidden rounded-t-2xl border-t border-white/15 bg-[#0b1020]/95 shadow-2xl backdrop-blur-xl',
          'sm:static sm:z-10 sm:h-full sm:max-h-none sm:w-[370px] sm:shrink-0 sm:rounded-none sm:border-t-0 sm:border-l sm:border-white/10 lg:w-[430px]',
        )}
      >
        {/* Poignée visuelle sur mobile */}
        <div className="flex justify-center pt-2 sm:hidden" aria-hidden="true">
          <div className="h-1 w-10 rounded-full bg-white/20" />
        </div>

        {/* En-tête du panneau */}
        <div className="flex items-start justify-between gap-2 border-b border-white/10 px-4 py-3">
          <div className="min-w-0 flex-1">
            {loading ? (
              <div className="h-5 w-44 animate-pulse rounded bg-white/10" />
            ) : (
              <>
                <h2 className="truncate font-display text-base font-semibold text-[#f8fafc]">
                  {noteDetail?.titre ?? noeudsById.get(selectedId)?.titre ?? selectedId}
                </h2>
                <p className="truncate font-mono text-[10px] text-[#64748b]">
                  {selectedId}
                </p>
              </>
            )}
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Fermer le panneau de note"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-white/10 text-[#94a3b8] transition hover:bg-white/10 hover:text-[#f8fafc]"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Corps scrollable */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="space-y-3">
              <div className="flex gap-2">
                <div className="h-5 w-24 animate-pulse rounded-full bg-white/10" />
                <div className="h-5 w-16 animate-pulse rounded-full bg-white/10" />
              </div>
              <div className="h-40 animate-pulse rounded-xl bg-white/5" />
            </div>
          ) : erreur || !noteDetail ? (
            <div className="flex flex-col items-center justify-center py-10 text-center text-xs text-[#94a3b8]">
              <FileText className="mb-2 h-7 w-7 text-[#64748b]" />
              <p>{erreur ?? 'Impossible de charger cette note.'}</p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* Métadonnées + Bouton Obsidian */}
              <div className="space-y-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-0.5 text-[11px] text-[#e2e8f0]">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: branchColor }}
                    />
                    {branchLabel}
                  </span>

                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 font-mono text-[10px]',
                      STATUT_BADGE_CLASS[noteDetail.statut] ?? STATUT_BADGE_CLASS.archive,
                    )}
                  >
                    {noteDetail.statut}
                  </span>

                  {noteDetail.date && (
                    <span className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono text-[10px] text-[#94a3b8]">
                      <Calendar className="h-2.5 w-2.5" />
                      {noteDetail.date}
                    </span>
                  )}
                </div>

                {noteDetail.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {noteDetail.tags.map((tag) => (
                      <span
                        key={tag}
                        className="inline-flex items-center gap-1 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-[#cbd5e1]"
                      >
                        <Tag className="h-2.5 w-2.5 text-[var(--accent)]" />
                        {tag}
                      </span>
                    ))}
                  </div>
                )}

                <button
                  type="button"
                  onClick={() => onOpenObsidian(noteDetail.id)}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-1.5 text-xs font-medium text-[var(--accent)] transition hover:bg-[var(--accent)]/25"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Ouvrir dans Obsidian
                </button>
              </div>

              {/* Contenu Markdown avec [[liens]] cliquables */}
              <div className="border-t border-white/10 pt-3">
                {preparedMarkdown.trim() ? (
                  <div className="jarvis-markdown">
                    <ReactMarkdown
                      remarkPlugins={REMARK_PLUGINS}
                      components={markdownComponents}
                      urlTransform={(url) =>
                        url.startsWith('jarvis-note://')
                          ? url
                          : defaultUrlTransform(url)
                      }
                    >
                      {preparedMarkdown}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <p className="text-xs italic text-[#64748b]">
                    Note vide (aucun contenu après l&apos;en-tête).
                  </p>
                )}
              </div>

              {/* Liens sortants & entrants cliquables */}
              {(noteDetail.liens_sortants.length > 0 ||
                noteDetail.liens_entrants.length > 0) && (
                <div className="space-y-3 border-t border-white/10 pt-3">
                  {noteDetail.liens_sortants.length > 0 && (
                    <div>
                      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-[#94a3b8]">
                        <ArrowUpRight className="h-3.5 w-3.5 text-[var(--accent)]" />
                        Liens sortants ({noteDetail.liens_sortants.length})
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {noteDetail.liens_sortants.map((targetId) => {
                          const targetNode = noeudsById.get(targetId);
                          const targetColor = targetNode
                            ? branchColorMap[targetNode.branche] ?? '#94a3b8'
                            : '#94a3b8';
                          return (
                            <button
                              key={targetId}
                              type="button"
                              onClick={() => onSelectNote(targetId)}
                              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1 text-left text-xs text-[#e2e8f0] transition hover:border-[var(--accent)]/50 hover:bg-[var(--accent)]/10"
                            >
                              <span
                                className="h-2 w-2 shrink-0 rounded-full"
                                style={{ backgroundColor: targetColor }}
                              />
                              <span className="truncate">
                                {targetNode?.titre ?? targetId}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {noteDetail.liens_entrants.length > 0 && (
                    <div>
                      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-[#94a3b8]">
                        <ArrowDownLeft className="h-3.5 w-3.5 text-[#34d399]" />
                        Liens entrants ({noteDetail.liens_entrants.length})
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {noteDetail.liens_entrants.map((sourceId) => {
                          const sourceNode = noeudsById.get(sourceId);
                          const sourceColor = sourceNode
                            ? branchColorMap[sourceNode.branche] ?? '#94a3b8'
                            : '#94a3b8';
                          return (
                            <button
                              key={sourceId}
                              type="button"
                              onClick={() => onSelectNote(sourceId)}
                              className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1 text-left text-xs text-[#e2e8f0] transition hover:border-[var(--accent)]/50 hover:bg-[var(--accent)]/10"
                            >
                              <span
                                className="h-2 w-2 shrink-0 rounded-full"
                                style={{ backgroundColor: sourceColor }}
                              />
                              <span className="truncate">
                                {sourceNode?.titre ?? sourceId}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
