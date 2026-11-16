import { useEffect, useState } from 'react';
import { ExternalLink, FileText, FolderKanban, Tag } from 'lucide-react';
import { MarkdownContent } from '@/components/MarkdownContent';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { NoteDetail, NoteResume } from '@/lib/types';
import { useToasts } from '@/store/useToasts';

/**
 * Vue `#/projets` : liste les notes de `01-Business-Projets` en cartes
 * et affiche la fiche Markdown sélectionnée avec ouverture directe dans Obsidian.
 */
export function ProjetsView() {
  const addToast = useToasts((s) => s.addToast);

  const [notes, setNotes] = useState<NoteResume[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NoteDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void api
      .get<NoteResume[]>('/api/cerveau/notes?branche=01-Business-Projets', {
        silent: true,
      })
      .then((list) => {
        if (!cancelled) {
          const items = Array.isArray(list) ? list : [];
          setNotes(items);
          if (items[0]) {
            setSelectedId(items[0].id);
          }
        }
      })
      .catch(() => {
        if (!cancelled) setNotes([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    void api
      .get<NoteDetail>(`/api/cerveau/note?id=${encodeURIComponent(selectedId)}`, {
        silent: true,
      })
      .then((res) => {
        if (!cancelled) setDetail(res ?? null);
      })
      .catch(() => {
        if (!cancelled) setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const handleOpenObsidian = async (id: string) => {
    try {
      await api.post('/api/cerveau/ouvrir', { id });
      addToast('Ouverture dans Obsidian demandée.', 'info');
    } catch {
      // Erreur notifiée par toast global
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-display text-xl font-semibold text-[#f8fafc]">
          Projets &amp; Business
        </h1>
        <p className="text-xs text-[#94a3b8]">
          Dossier <span className="font-mono text-[var(--accent)]">01-Business-Projets</span> du second cerveau.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_480px]">
        <section aria-label="Cartes projets">
          {loading ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {[1, 2, 3, 4].map((n) => (
                <div key={n} className="hud-panel h-36 animate-pulse" />
              ))}
            </div>
          ) : notes.length === 0 ? (
            <div className="hud-panel flex flex-col items-center justify-center p-10 text-center">
              <FolderKanban className="h-8 w-8 text-[#64748b]" />
              <p className="mt-3 text-sm text-[#94a3b8]">
                Aucun projet dans 01-Business-Projets
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {notes.map((note) => {
                const isSelected = selectedId === note.id;
                return (
                  <button
                    key={note.id}
                    type="button"
                    onClick={() => setSelectedId(note.id)}
                    className={cn(
                      'hud-panel flex flex-col justify-between p-4 text-left transition hover:border-[var(--accent)]/45',
                      isSelected && 'border-[var(--accent)]/60 bg-[#0b1020]',
                    )}
                  >
                    <div>
                      <div className="flex items-start justify-between gap-2">
                        <h2 className="font-display text-sm font-semibold text-[#f8fafc]">
                          {note.titre}
                        </h2>
                        <span
                          className={cn(
                            'shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px]',
                            note.statut === 'actif'
                              ? 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]'
                              : 'border-white/10 bg-white/5 text-[#94a3b8]',
                          )}
                        >
                          {note.statut}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-[#94a3b8]">
                        {note.extrait || 'Aucun extrait disponible.'}
                      </p>
                    </div>

                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-2.5">
                      <div className="flex flex-wrap gap-1">
                        {(note.tags ?? []).slice(0, 3).map((tag) => (
                          <span
                            key={tag}
                            className="inline-flex items-center gap-1 rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-[#cbd5e1]"
                          >
                            <Tag className="h-2.5 w-2.5 text-[var(--accent)]" />
                            {tag}
                          </span>
                        ))}
                      </div>
                      <span className="font-mono text-[10px] text-[#64748b]">
                        {note.date}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <aside
          className="hud-panel flex min-h-[420px] flex-col overflow-hidden"
          aria-label="Aperçu du projet"
        >
          {!selectedId ? (
            <div className="flex flex-1 flex-col items-center justify-center p-6 text-center text-xs text-[#64748b]">
              <FileText className="mb-2 h-7 w-7" />
              Sélectionnez une carte pour afficher son contenu Markdown.
            </div>
          ) : loadingDetail ? (
            <div className="p-5 space-y-3">
              <div className="h-6 w-2/3 animate-pulse rounded bg-white/10" />
              <div className="h-48 animate-pulse rounded-xl bg-white/5" />
            </div>
          ) : !detail ? (
            <div className="flex flex-1 items-center justify-center p-6 text-xs text-[#94a3b8]">
              Impossible de charger le contenu de la note.
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-3">
                <div className="min-w-0">
                  <h2 className="truncate font-display text-sm font-semibold text-[#f8fafc]">
                    {detail.titre}
                  </h2>
                  <p className="truncate font-mono text-[10px] text-[#64748b]">
                    {detail.id}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void handleOpenObsidian(detail.id)}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Ouvrir dans Obsidian
                </button>
              </div>
              <div className="flex-1 overflow-y-auto p-4">
                <MarkdownContent content={detail.contenu_md} />
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
