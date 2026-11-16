/**
 * Fenêtre modale de mise à jour applicative.
 * Affiche les notes de version, indique s'il s'agit d'une mise à jour légère ou complète,
 * permet de reporter (« Plus tard ») ou d'installer (« Installer maintenant ») avec suivi
 * en direct de `/api/maj/progression` et message « Jarvis va redémarrer ».
 */

import { useEffect } from 'react';
import {
  AlertCircle,
  Download,
  LoaderCircle,
  Sparkles,
  X,
} from 'lucide-react';
import { MarkdownContent } from '@/components/MarkdownContent';
import { useMaj } from '@/store/useMaj';

const PROGRESSION_POLL_MS = 1000;

/**
 * Modale globale d'information et d'installation de mise à jour Jarvis AIOS.
 */
export function MajModal() {
  const modalOpen = useMaj((s) => s.modalOpen);
  const version = useMaj((s) => s.version);
  const derniere = useMaj((s) => s.derniere);
  const installing = useMaj((s) => s.installing);
  const progression = useMaj((s) => s.progression);
  const error = useMaj((s) => s.error);

  const setModalOpen = useMaj((s) => s.setModalOpen);
  const lancerInstallation = useMaj((s) => s.lancerInstallation);
  const rafraichirProgression = useMaj((s) => s.rafraichirProgression);

  // Fermeture à la touche Échap quand aucune installation active n'est en cours
  useEffect(() => {
    if (!modalOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !installing) {
        e.preventDefault();
        setModalOpen(false);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [modalOpen, installing, setModalOpen]);

  // Sondage /api/maj/progression toutes les secondes pendant l'installation
  useEffect(() => {
    if (!modalOpen || !installing) return;
    void rafraichirProgression();
    const timer = window.setInterval(() => {
      void rafraichirProgression();
    }, PROGRESSION_POLL_MS);
    return () => window.clearInterval(timer);
  }, [modalOpen, installing, rafraichirProgression]);

  if (!modalOpen || !derniere) return null;

  const targetVersion = derniere.version.replace(/^v/i, '');
  const currentVersion = version ? version.replace(/^v/i, '') : null;
  const pct = progression?.pourcent ?? 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#05070d]/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="maj-modal-title"
      onClick={() => {
        if (!installing) setModalOpen(false);
      }}
    >
      <div
        className="hud-panel w-full max-w-lg space-y-4 p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
              <Download className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="hud-label text-[var(--accent)]">
                  Nouvelle version
                </span>
                <span
                  className={
                    derniere.legere
                      ? 'inline-flex items-center gap-1 rounded-full border border-[#34d399]/40 bg-[#34d399]/10 px-2 py-0.5 text-[10px] font-medium text-[#34d399]'
                      : 'inline-flex items-center gap-1 rounded-full border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-2 py-0.5 text-[10px] font-medium text-[#fbbf24]'
                  }
                >
                  <Sparkles className="h-2.5 w-2.5" />
                  {derniere.legere
                    ? 'Mise à jour légère'
                    : 'Mise à jour complète'}
                </span>
              </div>
              <h2
                id="maj-modal-title"
                className="mt-1 font-display text-base font-semibold text-[#f8fafc]"
              >
                Mise à jour v{targetVersion}
              </h2>
              {currentVersion && (
                <p className="font-mono text-[11px] text-[#94a3b8]">
                  Version installée : v{currentVersion}
                </p>
              )}
            </div>
          </div>

          {!installing && (
            <button
              type="button"
              onClick={() => setModalOpen(false)}
              aria-label="Fermer la fenêtre de mise à jour"
              className="rounded p-1 text-[#94a3b8] transition hover:bg-white/10 hover:text-[#e2e8f0]"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <p className="text-xs leading-relaxed text-[#94a3b8]">
          {derniere.legere
            ? "Cette mise à jour légère met à jour uniquement l'application, sans réinstaller Python."
            : "Cette mise à jour complète lance l'installeur Jarvis AIOS pour actualiser l'environnement."}
        </p>

        <div className="max-h-60 overflow-y-auto rounded-xl border border-white/10 bg-[#05070d]/70 p-3.5">
          <MarkdownContent
            content={
              derniere.notes?.trim() ||
              'Améliorations générales et corrections de stabilité.'
            }
          />
        </div>

        {(installing || progression) && (
          <div className="space-y-2 rounded-xl border border-[var(--accent)]/35 bg-[var(--accent)]/10 p-3.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="flex items-center gap-2 font-medium text-[#f8fafc]">
                {installing && (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin text-[var(--accent)]" />
                )}
                {progression?.etape || 'Installation en cours…'}
              </span>
              <span className="font-mono text-[var(--accent)]">{pct}%</span>
            </div>
            <div
              className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full rounded-full bg-[var(--accent)] transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
            <p className="text-[11px] font-medium text-[#cbd5e1]">
              Jarvis va redémarrer automatiquement une fois l&apos;installation prête.
            </p>
          </div>
        )}

        {(error || progression?.erreur) && (
          <div
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-[#f87171]/40 bg-[#f87171]/10 p-3 text-xs text-[#fca5a5]"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-[#f87171]" />
            <span>{progression?.erreur || error}</span>
          </div>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2.5 pt-1">
          <button
            type="button"
            disabled={installing}
            onClick={() => setModalOpen(false)}
            className="rounded-xl border border-white/15 bg-white/5 px-4 py-2 text-xs font-medium text-[#e2e8f0] transition hover:border-white/30 disabled:opacity-50"
          >
            Plus tard
          </button>
          <button
            type="button"
            disabled={installing}
            onClick={() => void lancerInstallation()}
            className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-50"
          >
            {installing ? (
              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            {installing ? 'Installation…' : 'Installer maintenant'}
          </button>
        </div>
      </div>
    </div>
  );
}
