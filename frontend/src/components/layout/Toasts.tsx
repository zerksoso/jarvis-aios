import { Link } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Info, X, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/cn';
import type { ToastItem } from '@/lib/types';
import { useToasts } from '@/store/useToasts';

const STYLE_BY_LEVEL: Record<
  ToastItem['niveau'],
  { border: string; iconColor: string; Icon: typeof Info }
> = {
  info: {
    border: 'border-[var(--accent)]/40',
    iconColor: 'text-[var(--accent)]',
    Icon: Info,
  },
  succes: {
    border: 'border-[#34d399]/40',
    iconColor: 'text-[#34d399]',
    Icon: CheckCircle2,
  },
  avertissement: {
    border: 'border-[#f59e0b]/45',
    iconColor: 'text-[#f59e0b]',
    Icon: AlertTriangle,
  },
  erreur: {
    border: 'border-[#f87171]/45',
    iconColor: 'text-[#f87171]',
    Icon: AlertCircle,
  },
};

/**
 * Pile de notifications toast en bas à droite de la fenêtre.
 */
export function Toasts() {
  const toasts = useToasts((s) => s.toasts);
  const removeToast = useToasts((s) => s.removeToast);

  if (toasts.length === 0) return null;

  return (
    <div
      className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-full max-w-sm flex-col gap-2 px-2 sm:px-0"
      aria-live="polite"
      aria-label="Notifications"
    >
      {toasts.map((toast) => {
        const cfg = STYLE_BY_LEVEL[toast.niveau];
        const IconComponent = cfg.Icon;
        return (
          <div
            key={toast.id}
            role="status"
            className={cn(
              'hud-glass pointer-events-auto flex items-start gap-3 px-3.5 py-2.5 shadow-xl',
              cfg.border,
            )}
          >
            <IconComponent className={cn('mt-0.5 h-4 w-4 shrink-0', cfg.iconColor)} />
            <div className="min-w-0 flex-1 space-y-1.5">
              <p className="text-xs leading-relaxed text-[#e2e8f0]">
                {toast.texte}
              </p>
              {toast.action && toast.action.to && (
                <Link
                  to={toast.action.to}
                  onClick={() => removeToast(toast.id)}
                  className="inline-flex items-center rounded-md border border-[var(--accent)]/40 bg-[var(--accent)]/15 px-2 py-0.5 text-[11px] font-medium text-[var(--accent)] transition hover:bg-[var(--accent)]/25"
                >
                  {toast.action.label}
                </Link>
              )}
              {toast.action && !toast.action.to && toast.action.onClick && (
                <button
                  type="button"
                  onClick={() => {
                    toast.action?.onClick?.();
                    removeToast(toast.id);
                  }}
                  className="inline-flex items-center rounded-md border border-[var(--accent)]/40 bg-[var(--accent)]/15 px-2 py-0.5 text-[11px] font-medium text-[var(--accent)] transition hover:bg-[var(--accent)]/25"
                >
                  {toast.action.label}
                </button>
              )}
            </div>
            <button
              type="button"
              onClick={() => removeToast(toast.id)}
              aria-label="Fermer la notification"
              className="rounded p-0.5 text-[#94a3b8] transition hover:bg-white/10 hover:text-[#e2e8f0]"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
