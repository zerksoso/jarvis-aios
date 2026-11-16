import { useEffect, useMemo, useState } from 'react';
import { Command, Cpu, Download, Menu, Radio, Wifi, WifiOff } from 'lucide-react';
import { cn } from '@/lib/cn';
import type { QuotaBucket, Quotas } from '@/lib/types';
import { useJarvis } from '@/store/useJarvis';
import { useMaj } from '@/store/useMaj';
import { useQuotas } from '@/store/useQuotas';

interface TopBarProps {
  onOpenPalette: () => void;
  onToggleMobileSidebar: () => void;
}

interface QuotaGaugeItem {
  key: string;
  label: string;
  pct: number | null;
  tooltip: string;
}

/**
 * Formate l'heure de réinitialisation ISO d'un bucket de quota en français lisible.
 */
function formatResetTime(resetsAt?: string | null): string {
  if (!resetsAt) return 'non communiquée';
  const d = new Date(resetsAt);
  if (Number.isNaN(d.getTime())) return resetsAt;
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/**
 * Convertit un bucket (`remaining` entre 0..1 ou 0..100) en pourcentage entier.
 */
function bucketToPct(bucket?: QuotaBucket): number | null {
  if (!bucket || typeof bucket.remaining !== 'number' || Number.isNaN(bucket.remaining)) {
    return null;
  }
  const raw = bucket.remaining <= 1 ? bucket.remaining * 100 : bucket.remaining;
  return Math.max(0, Math.min(100, Math.round(raw)));
}

/**
 * Construit les 5 jauges compactes de la barre supérieure à partir de `/api/quotas`.
 */
function buildQuotaItems(quotas: Quotas | null): QuotaGaugeItem[] {
  const buckets = quotas?.buckets ?? {};

  const fromBucket = (key: string, label: string): QuotaGaugeItem => {
    const b = buckets[key];
    const pct = bucketToPct(b);
    return {
      key,
      label,
      pct,
      tooltip:
        pct === null
          ? `${label} : donnée indisponible`
          : `${label} : ${pct} % restant · Remise à zéro : ${formatResetTime(b?.resetsAt)}`,
    };
  };

  // Estimation Claude Code CLI (soit bucket dédié, soit pourcentage/tokens estimés)
  const cliBucket = buckets['claude-cli'] ?? quotas?.claude_code_5h;
  let cliPct = bucketToPct(cliBucket);
  if (cliPct === null && typeof quotas?.claude_cli_estime_pct === 'number') {
    cliPct = Math.max(0, Math.min(100, Math.round(quotas.claude_cli_estime_pct)));
  }
  const cliTokens =
    typeof quotas?.claude_cli_5h_tokens === 'number'
      ? `${quotas.claude_cli_5h_tokens.toLocaleString('fr-FR')} tokens sur 5 h`
      : 'fenêtre glissante 5 h';
  const cliItem: QuotaGaugeItem = {
    key: 'claude-cli',
    label: 'Claude Code',
    pct: cliPct,
    tooltip:
      cliPct === null
        ? `Claude Code estimé : ${cliTokens}`
        : `Claude Code estimé : ${cliPct} % (${cliTokens})`,
  };

  return [
    fromBucket('gemini-5h', 'Gemini 5 h'),
    fromBucket('gemini-weekly', 'Gemini hebdo'),
    fromBucket('gemini-argon-wtu-weekly', 'Argon hebdo'),
    fromBucket('3p-5h', 'Claude-AG 5 h'),
    cliItem,
  ];
}

/**
 * Barre supérieure HUD de la coque Jarvis AIOS.
 * Affiche la marque, l'horloge temps réel, les 5 jauges de quota,
 * les pastilles Overlay/WebSocket et l'accès rapide `Ctrl+K`.
 */
export function TopBar({ onOpenPalette, onToggleMobileSidebar }: TopBarProps) {
  const overlayConnecte = useJarvis((s) => s.overlay_connecte);
  const wsStatut = useJarvis((s) => s.ws_statut);
  const quotas = useQuotas((s) => s.quotas);
  const majDisponible = useMaj((s) => s.disponible);
  const majDerniere = useMaj((s) => s.derniere);
  const setMajModalOpen = useMaj((s) => s.setModalOpen);

  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(new Date());
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  const dateText = useMemo(
    () =>
      new Intl.DateTimeFormat('fr-FR', {
        weekday: 'short',
        day: '2-digit',
        month: 'short',
      }).format(now),
    [now],
  );

  const timeText = useMemo(
    () =>
      new Intl.DateTimeFormat('fr-FR', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }).format(now),
    [now],
  );

  const quotaItems = useMemo(() => buildQuotaItems(quotas), [quotas]);

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center justify-between gap-2 border-b border-white/10 bg-[#05070d]/90 px-3 backdrop-blur-md lg:px-5">
      <div className="flex min-w-0 items-center gap-2.5">
        <button
          type="button"
          onClick={onToggleMobileSidebar}
          aria-label="Ouvrir le menu latéral"
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-white/10 bg-[#0b1020] text-[#e2e8f0] transition hover:border-[var(--accent)]/50 lg:hidden"
        >
          <Menu className="h-4 w-4" />
        </button>

        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
            <Cpu className="h-4 w-4" />
          </div>
          <span className="font-display text-sm font-bold tracking-[0.18em] text-[#f8fafc] whitespace-nowrap">
            JARVIS <span className="text-[var(--accent)]">·</span> AIOS
          </span>
        </div>

        <div className="hidden items-center gap-2 border-l border-white/10 pl-3 font-mono text-xs text-[#94a3b8] sm:flex">
          <span className="capitalize">{dateText}</span>
          <span className="text-[#e2e8f0]">{timeText}</span>
        </div>
      </div>

      {/* Jauges de quota compactes (masquées sur très petit écran pour préserver le 375px) */}
      <div
        className="hidden items-center gap-3 xl:flex"
        aria-label="Quotas des modèles"
      >
        {quotaItems.map((item) => {
          const barColor =
            item.pct === null
              ? 'bg-slate-600'
              : item.pct <= 15
                ? 'bg-[#f87171]'
                : item.pct <= 40
                  ? 'bg-[#f59e0b]'
                  : 'bg-[var(--accent)]';
          return (
            <div
              key={item.key}
              title={item.tooltip}
              className="flex w-28 flex-col gap-1 rounded-lg border border-white/[0.06] bg-[#0b1020]/70 px-2 py-1"
            >
              <div className="flex items-center justify-between gap-1 text-[10px] leading-none">
                <span className="truncate text-[#94a3b8]">{item.label}</span>
                <span className="font-mono text-[#e2e8f0]">
                  {item.pct === null ? '—' : `${item.pct}%`}
                </span>
              </div>
              <div className="h-1 w-full overflow-hidden rounded-full bg-white/10">
                <div
                  className={cn('h-full rounded-full transition-all duration-300', barColor)}
                  style={{ width: `${item.pct ?? 0}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        {majDisponible && majDerniere?.version && (
          <button
            type="button"
            onClick={() => setMajModalOpen(true)}
            aria-label={`Mise à jour v${majDerniere.version.replace(/^v/i, '')} disponible`}
            title={`Mise à jour v${majDerniere.version.replace(/^v/i, '')} disponible`}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-2.5 py-1 text-[11px] font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25 whitespace-nowrap"
          >
            <Download className="h-3 w-3 shrink-0" />
            <span className="hidden sm:inline">
              Mise à jour v{majDerniere.version.replace(/^v/i, '')}
            </span>
            <span className="sm:hidden">
              v{majDerniere.version.replace(/^v/i, '')}
            </span>
          </button>
        )}

        <span
          title={overlayConnecte ? 'Overlay bureau connecté' : 'Overlay bureau déconnecté'}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
            overlayConnecte
              ? 'border-[#34d399]/35 bg-[#34d399]/10 text-[#34d399]'
              : 'border-white/10 bg-[#0b1020] text-[#94a3b8]',
          )}
        >
          <Radio className="h-3 w-3" />
          <span className="hidden md:inline">
            {overlayConnecte ? 'Overlay actif' : 'Overlay off'}
          </span>
        </span>

        <span
          title={
            wsStatut === 'connecte'
              ? 'WebSocket connecté'
              : wsStatut === 'connexion'
                ? 'Reconnexion WebSocket…'
                : 'WebSocket déconnecté'
          }
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium',
            wsStatut === 'connecte'
              ? 'border-[#34d399]/35 bg-[#34d399]/10 text-[#34d399]'
              : wsStatut === 'connexion'
                ? 'border-[#f59e0b]/35 bg-[#f59e0b]/10 text-[#f59e0b]'
                : 'border-[#f87171]/35 bg-[#f87171]/10 text-[#f87171]',
          )}
        >
          {wsStatut === 'deconnecte' ? (
            <WifiOff className="h-3 w-3" />
          ) : (
            <Wifi className="h-3 w-3" />
          )}
          <span className="hidden sm:inline">
            {wsStatut === 'connecte'
              ? 'WS'
              : wsStatut === 'connexion'
                ? 'Reconnexion'
                : 'Hors ligne'}
          </span>
        </span>

        <button
          type="button"
          onClick={onOpenPalette}
          aria-label="Ouvrir la palette de commandes (Ctrl+K)"
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-[#0b1020] px-2.5 py-1.5 text-xs text-[#e2e8f0] transition hover:border-[var(--accent)]/50 hover:text-[var(--accent)]"
        >
          <Command className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Actions</span>
          <kbd className="hidden rounded border border-white/15 bg-white/5 px-1 font-mono text-[10px] text-[#94a3b8] md:inline">
            Ctrl+K
          </kbd>
        </button>
      </div>
    </header>
  );
}
