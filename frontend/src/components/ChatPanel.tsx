import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown, ArrowRightLeft, Send, Sparkles, User } from 'lucide-react';
import { AgentAvatar } from '@/components/AgentAvatar';
import { MarkdownContent } from '@/components/MarkdownContent';
import { cn } from '@/lib/cn';
import {
  EFFORT_OPTIONS,
  filterModelsByMoteur,
  formatHeureFr,
  getModelAvailability,
  MOTEUR_OPTIONS,
} from '@/lib/llm';
import type { Effort, MoteurId } from '@/lib/types';
import { useAgents } from '@/store/useAgents';
import {
  getConversationId,
  parseMessageMeta,
  useChats,
} from '@/store/useChats';
import { useMoteurs } from '@/store/useMoteurs';
import { useParametres } from '@/store/useParametres';
import { AUCUN_MESSAGE } from '@/store/useChats';

export interface ChatPanelProps {
  type: 'agent' | 'canal';
  cibleId: string;
  title?: string;
  subtitle?: string;
  defaultModele?: string;
  defaultEffort?: Effort;
  multiAuthor?: boolean;
  className?: string;
}

/**
 * Panneau de discussion réutilisable pour un agent individuel ou un canal multi-agents.
 * Gère l'historique REST, le streaming WebSocket, le défilement intelligent,
 * les badges moteur/modèle/bascules et la mise en valeur des synthèses de canal.
 */
export function ChatPanel({
  type,
  cibleId,
  title,
  subtitle,
  defaultModele,
  defaultEffort,
  multiAuthor = false,
  className,
}: ChatPanelProps) {
  const convId = getConversationId(type, cibleId);
  const agents = useAgents((s) => s.agents);
  const parametres = useParametres((s) => s.parametres);
  const modeles = useMoteurs((s) => s.modeles);
  const moteursStatut = useMoteurs((s) => s.moteursStatut);

  const messages = useChats((s) => s.messagesByConv[convId] ?? AUCUN_MESSAGE);
  const streaming = useChats((s) => s.streamingByConv[convId] ?? null);
  const typingAuthor = useChats((s) => s.typingByConv[convId] ?? null);
  const loading = useChats((s) => s.loadingByConv[convId] ?? false);
  const sending = useChats((s) => s.sendingByConv[convId] ?? false);
  const loadMessages = useChats((s) => s.loadMessages);
  const sendMessage = useChats((s) => s.sendMessage);

  const [moteur, setMoteur] = useState<MoteurId>(parametres.moteur);
  const [modele, setModele] = useState<string>(defaultModele ?? parametres.modele);
  const [effort, setEffort] = useState<Effort>(defaultEffort ?? parametres.effort);
  const [draft, setDraft] = useState('');
  const [autoScroll, setAutoScroll] = useState(true);

  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setMoteur(parametres.moteur);
    setModele(defaultModele ?? parametres.modele);
    setEffort(defaultEffort ?? parametres.effort);
  }, [cibleId, defaultModele, defaultEffort, parametres.moteur, parametres.modele, parametres.effort]);

  useEffect(() => {
    void loadMessages(type, cibleId);
  }, [type, cibleId, loadMessages]);

  const filteredModels = useMemo(
    () => filterModelsByMoteur(modeles, moteur),
    [modeles, moteur],
  );

  useEffect(() => {
    if (
      filteredModels.length > 0 &&
      modele &&
      !filteredModels.some((m) => m.id === modele)
    ) {
      setModele(filteredModels[0].id);
    }
  }, [filteredModels, modele]);

  const agentMap = useMemo(() => {
    const map = new Map<string, (typeof agents)[number]>();
    for (const a of agents) {
      map.set(a.id, a);
    }
    return map;
  }, [agents]);

  // Défilement automatique vers le bas sauf si l'utilisateur est remonté
  useEffect(() => {
    if (!autoScroll || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [messages.length, streaming?.contenu, autoScroll]);

  const handleScroll = () => {
    const el = listRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    setAutoScroll(distanceFromBottom < 48);
  };

  const scrollToBottom = () => {
    if (!listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
    setAutoScroll(true);
  };

  const handleSubmit = async () => {
    const clean = draft.trim();
    if (!clean || sending) return;
    setDraft('');
    setAutoScroll(true);
    try {
      await sendMessage({
        cible: type,
        id: cibleId,
        message: clean,
        moteur,
        modele,
        effort,
      });
    } catch {
      // Erreur affichée via toast global
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit();
    }
  };

  const typingAgentName = typingAuthor
    ? (agentMap.get(typingAuthor)?.nom ?? typingAuthor)
    : null;

  return (
    <div className={cn('hud-panel flex h-full min-h-[460px] flex-col overflow-hidden', className)}>
      {(title || subtitle) && (
        <div className="border-b border-white/10 px-4 py-3">
          {title && (
            <h1 className="font-display text-base font-semibold text-[#f8fafc]">
              {title}
            </h1>
          )}
          {subtitle && (
            <p className="mt-0.5 text-xs text-[#94a3b8]">{subtitle}</p>
          )}
        </div>
      )}

      {/* Liste des messages */}
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="relative flex-1 space-y-3 overflow-y-auto p-4"
      >
        {loading && messages.length === 0 ? (
          <div className="space-y-3">
            {[1, 2, 3].map((n) => (
              <div
                key={n}
                className="h-16 animate-pulse rounded-xl border border-white/5 bg-white/[0.03]"
              />
            ))}
          </div>
        ) : messages.length === 0 && !streaming ? (
          <div className="flex h-full min-h-[220px] items-center justify-center text-center text-xs text-[#64748b]">
            Aucun message pour le moment. Démarrez la conversation ci-dessous.
          </div>
        ) : (
          messages.map((msg) => {
            const isUser = msg.role === 'user' || msg.auteur === 'utilisateur';
            const isSystem = msg.role === 'system';
            const agent = !isUser ? agentMap.get(msg.auteur) : undefined;
            const meta = parseMessageMeta(msg.meta);
            const isSynthese = Boolean(meta.synthese);
            const bascules = Array.isArray(meta.bascules) ? meta.bascules : [];
            const agentColor = agent?.avatar.couleur || 'var(--accent)';

            return (
              <article
                key={String(msg.id)}
                className={cn(
                  'rounded-xl border px-3.5 py-3 transition',
                  isUser
                    ? 'ml-auto max-w-[88%] border-[var(--accent)]/30 bg-[var(--accent)]/10'
                    : isSystem
                      ? 'border-[#f87171]/30 bg-[#f87171]/10'
                      : isSynthese
                        ? 'border-[var(--accent)]/50 bg-[var(--accent)]/[0.07]'
                        : 'border-white/10 bg-[#05070d]/70',
                )}
                style={
                  multiAuthor && !isUser && !isSystem && !isSynthese
                    ? { borderLeftColor: agentColor, borderLeftWidth: '3px' }
                    : undefined
                }
              >
                <header className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    {isUser ? (
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
                        <User className="h-3.5 w-3.5" />
                      </span>
                    ) : agent ? (
                      <AgentAvatar avatar={agent.avatar} size="sm" />
                    ) : (
                      <span className="inline-flex h-6 w-6 items-center justify-center rounded-full border border-white/15 bg-white/5 font-mono text-[10px] text-[#94a3b8]">
                        AI
                      </span>
                    )}
                    <span
                      className="text-xs font-semibold"
                      style={
                        multiAuthor && !isUser && !isSystem
                          ? { color: agentColor }
                          : undefined
                      }
                    >
                      {isUser ? 'Vous' : (agent?.nom ?? msg.auteur)}
                    </span>
                    {isSynthese && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-[var(--accent)]/45 bg-[var(--accent)]/15 px-2 py-0.5 font-mono text-[10px] text-[var(--accent)]">
                        <Sparkles className="h-2.5 w-2.5" />
                        Synthèse
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-2 font-mono text-[10px] text-[#94a3b8]">
                    {(meta.moteur || meta.modele) && (
                      <span className="rounded border border-white/10 bg-white/[0.04] px-1.5 py-0.5 text-[#cbd5e1]">
                        {meta.modele || meta.moteur}
                      </span>
                    )}
                    {bascules.length > 0 && (
                      <span
                        title={`Bascules : ${bascules.join(' → ')}`}
                        className="inline-flex items-center gap-1 rounded border border-[#f59e0b]/40 bg-[#f59e0b]/10 px-1.5 py-0.5 text-[#f59e0b]"
                      >
                        <ArrowRightLeft className="h-2.5 w-2.5" />
                        {bascules.length}
                      </span>
                    )}
                    <span>{formatHeureFr(msg.cree_le)}</span>
                  </div>
                </header>

                <MarkdownContent content={msg.contenu} />
              </article>
            );
          })
        )}

        {/* Message en cours de streaming */}
        {streaming && (
          <article className="rounded-xl border border-[var(--accent)]/40 bg-[#05070d]/85 px-3.5 py-3">
            <header className="mb-1.5 flex items-center gap-2">
              {agentMap.get(streaming.auteur) ? (
                <AgentAvatar
                  avatar={agentMap.get(streaming.auteur)!.avatar}
                  size="sm"
                />
              ) : null}
              <span className="text-xs font-semibold text-[var(--accent)]">
                {agentMap.get(streaming.auteur)?.nom ?? (streaming.auteur || 'Assistant')}
              </span>
              <span className="font-mono text-[10px] text-[#94a3b8]">
                streaming…
              </span>
            </header>
            <MarkdownContent content={`${streaming.contenu}▍`} />
          </article>
        )}
      </div>

      {/* Bouton retour en bas si l'utilisateur a remonté le fil */}
      {!autoScroll && (
        <div className="flex justify-center border-t border-white/5 bg-[#05070d]/60 py-1">
          <button
            type="button"
            onClick={scrollToBottom}
            className="inline-flex items-center gap-1 rounded-full border border-white/15 bg-[#0b1020] px-3 py-1 text-[11px] text-[var(--accent)] hover:border-[var(--accent)]/50"
          >
            <ArrowDown className="h-3 w-3" />
            Revenir en bas
          </button>
        </div>
      )}

      {/* Indicateur de saisie et zone d'envoi */}
      <div className="border-t border-white/10 bg-[#05070d]/60 p-3 space-y-2">
        {typingAgentName && (
          <div className="font-mono text-[11px] text-[var(--accent)]">
            {typingAgentName} est en train d&apos;écrire…
          </div>
        )}

        <div className="flex flex-wrap items-end gap-2">
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Écrire un message (Entrée pour envoyer, Maj+Entrée pour nouvelle ligne)…"
            aria-label="Zone de saisie du message"
            className="min-w-0 flex-1 resize-y rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-sm text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[var(--accent)]/60 focus:outline-none"
          />
          <button
            type="button"
            disabled={sending || !draft.trim()}
            onClick={() => void handleSubmit()}
            aria-label="Envoyer le message"
            className="inline-flex h-10 shrink-0 items-center justify-center rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-4 text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11px] text-[#94a3b8]">
          <select
            aria-label="Moteur"
            value={moteur}
            onChange={(e) => setMoteur(e.target.value as MoteurId)}
            className="rounded-lg border border-white/10 bg-[#05070d] px-2 py-1 text-xs text-[#e2e8f0]"
          >
            {MOTEUR_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>

          <select
            aria-label="Modèle"
            value={modele}
            onChange={(e) => setModele(e.target.value)}
            className="max-w-full rounded-lg border border-white/10 bg-[#05070d] px-2 py-1 text-xs text-[#e2e8f0]"
          >
            {filteredModels.length === 0 ? (
              <option value={modele}>{modele || 'Par défaut'}</option>
            ) : (
              filteredModels.map((m) => {
                const avail = getModelAvailability(m, moteursStatut);
                return (
                  <option key={m.id} value={m.id}>
                    {avail.disponible
                      ? m.libelle
                      : `${m.libelle} (indisponible : ${avail.raison})`}
                  </option>
                );
              })
            )}
          </select>

          <select
            aria-label="Effort de raisonnement"
            value={effort}
            onChange={(e) => setEffort(e.target.value as Effort)}
            className="rounded-lg border border-white/10 bg-[#05070d] px-2 py-1 text-xs text-[#e2e8f0]"
          >
            {EFFORT_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
