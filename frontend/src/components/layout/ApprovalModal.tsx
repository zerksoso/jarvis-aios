import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert } from 'lucide-react';
import { useApprobations } from '@/store/useApprobations';
import { useToasts } from '@/store/useToasts';

/**
 * Fenêtre modale bloquante affichée dès qu'une approbation utilisateur est en attente.
 * Affiche le résumé, le niveau de risque, le détail JSON repliable et les actions Autoriser/Refuser.
 */
export function ApprovalModal() {
  const enAttente = useApprobations((s) => s.enAttente);
  const submittingId = useApprobations((s) => s.submittingId);
  const decideApprobation = useApprobations((s) => s.decideApprobation);
  const addToast = useToasts((s) => s.addToast);

  const [detailsOpen, setDetailsOpen] = useState(false);

  const current = enAttente[0];
  if (!current) return null;

  const isSubmitting = submittingId === current.id;

  const handleDecision = async (decision: 'oui' | 'non') => {
    try {
      await decideApprobation(current.id, decision);
      addToast(
        decision === 'oui' ? 'Action autorisée.' : 'Action refusée.',
        decision === 'oui' ? 'succes' : 'info',
      );
      setDetailsOpen(false);
    } catch (err) {
      addToast(
        err instanceof Error ? err.message : 'Erreur lors de la décision',
        'erreur',
      );
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#05070d]/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
    >
      <div className="w-full max-w-lg rounded-[14px] border border-[#f59e0b]/50 bg-[#0b1020] p-5 shadow-2xl shadow-[#f59e0b]/10">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#f59e0b]/40 bg-[#f59e0b]/15 text-[#f59e0b]">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="hud-label text-[#f59e0b]">Accord requis</span>
              <span className="rounded-full border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-2 py-0.5 font-mono text-[11px] text-[#f59e0b]">
                {current.risque || 'irreversible'}
              </span>
              {enAttente.length > 1 && (
                <span className="font-mono text-[11px] text-[#94a3b8]">
                  +{enAttente.length - 1} en attente
                </span>
              )}
            </div>
            <h2
              id="approval-modal-title"
              className="mt-1 font-display text-base font-semibold text-[#f8fafc]"
            >
              Confirmation d&apos;action ({current.source})
            </h2>
          </div>
        </div>

        <p className="mt-4 rounded-xl border border-white/10 bg-[#05070d]/70 p-3.5 text-sm leading-relaxed text-[#e2e8f0]">
          {current.resume}
        </p>

        <div className="mt-3">
          <button
            type="button"
            onClick={() => setDetailsOpen((prev) => !prev)}
            className="inline-flex items-center gap-1.5 text-xs text-[#94a3b8] transition hover:text-[#e2e8f0]"
          >
            {detailsOpen ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
            Détail technique de l&apos;action
          </button>
          {detailsOpen && (
            <pre className="mt-2 max-h-48 overflow-auto rounded-lg border border-white/10 bg-[#05070d] p-3 font-mono text-xs text-[#94a3b8]">
              {JSON.stringify(current.action ?? {}, null, 2)}
            </pre>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-end gap-2.5">
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => void handleDecision('non')}
            className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-xs font-medium text-[#e2e8f0] transition hover:border-[#f87171]/50 hover:bg-[#f87171]/15 hover:text-[#f87171] disabled:opacity-50"
          >
            Refuser
          </button>
          <button
            type="button"
            disabled={isSubmitting}
            onClick={() => void handleDecision('oui')}
            className="rounded-lg border border-[#f59e0b]/60 bg-[#f59e0b]/20 px-4 py-2 text-xs font-semibold text-[#fbbf24] transition hover:bg-[#f59e0b]/30 disabled:opacity-50"
          >
            {isSubmitting ? 'Envoi…' : 'Autoriser'}
          </button>
        </div>
      </div>
    </div>
  );
}
