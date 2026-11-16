import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Hash, Save, SlidersHorizontal, UserMinus, UserPlus } from 'lucide-react';
import { AgentAvatar } from '@/components/AgentAvatar';
import { ChatPanel } from '@/components/ChatPanel';
import { MarkdownContent } from '@/components/MarkdownContent';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import type { Canal } from '@/lib/types';
import { useAgents } from '@/store/useAgents';
import { useCanaux } from '@/store/useCanaux';
import { useToasts } from '@/store/useToasts';

/**
 * Vue `#/canal/:id` : chat multi-agents avec distinction par couleur, synthèses mises en avant
 * et panneau droit de configuration des membres, du coordinateur et de la mémoire du canal.
 */
export function CanalView() {
  const { id = '' } = useParams<{ id: string }>();
  const upsertCanal = useCanaux((s) => s.upsertCanal);
  const agents = useAgents((s) => s.agents);
  const addToast = useToasts((s) => s.addToast);

  const [draft, setDraft] = useState<Canal | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [panelOpen, setPanelOpen] = useState(true);

  useEffect(() => {
    setLoading(true);
    let cancelled = false;
    const cached = useCanaux.getState().canaux.find((c) => c.id === id);
    if (cached) {
      setDraft(structuredClone(cached));
    }

    void api
      .get<Canal>(`/api/canaux/${encodeURIComponent(id)}`, { silent: true })
      .then((canal) => {
        if (!cancelled && canal) {
          setDraft(canal);
          useCanaux.getState().upsertCanal(canal);
        }
      })
      .catch(() => {
        if (!cancelled && !cached) {
          setDraft(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const updated = await api.put<Canal>(
        `/api/canaux/${encodeURIComponent(draft.id)}`,
        draft,
      );
      const next = updated ?? draft;
      setDraft(next);
      upsertCanal(next);
      addToast(`Canal #${next.nom} enregistré.`, 'succes');
    } catch {
      // Erreur notifiée par toast global
    } finally {
      setSaving(false);
    }
  };

  if (loading && !draft) {
    return (
      <div className="hud-panel p-6">
        <div className="h-6 w-48 animate-pulse rounded bg-white/10" />
        <div className="mt-4 h-64 animate-pulse rounded-xl bg-white/5" />
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="hud-panel p-6 text-center">
        <p className="text-sm text-[#94a3b8]">Canal « {id} » introuvable.</p>
        <Link
          to="/accueil"
          className="mt-3 inline-block text-xs text-[var(--accent)] hover:underline"
        >
          Retour au tableau de bord
        </Link>
      </div>
    );
  }

  const availableAgents = agents.filter((a) => !draft.membres.includes(a.id));

  const addMember = (agentId: string) => {
    if (draft.membres.includes(agentId)) return;
    setDraft({ ...draft, membres: [...draft.membres, agentId] });
  };

  const removeMember = (agentId: string) => {
    const nextMembers = draft.membres.filter((m) => m !== agentId);
    const nextCoord =
      draft.coordinateur === agentId ? (nextMembers[0] ?? '') : draft.coordinateur;
    setDraft({ ...draft, membres: nextMembers, coordinateur: nextCoord });
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
            <Hash className="h-4 w-4" />
          </div>
          <div>
            <h1 className="font-display text-lg font-semibold text-[#f8fafc]">
              {draft.nom}
            </h1>
            <p className="text-xs text-[#94a3b8]">
              {draft.description || `${draft.membres.length} membres`}
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => setPanelOpen((prev) => !prev)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-[#0b1020] px-3 py-1.5 text-xs text-[#e2e8f0] hover:border-[var(--accent)]/50"
        >
          <SlidersHorizontal className="h-3.5 w-3.5 text-[var(--accent)]" />
          {panelOpen ? 'Masquer réglages' : 'Afficher réglages'}
        </button>
      </div>

      <div
        className={cn(
          'grid min-h-0 flex-1 grid-cols-1 gap-4',
          panelOpen && 'xl:grid-cols-[minmax(0,1fr)_380px]',
        )}
      >
        <ChatPanel
          type="canal"
          cibleId={draft.id}
          multiAuthor
          className="min-h-[520px]"
        />

        {panelOpen && (
          <aside
            className="hud-panel flex flex-col overflow-hidden"
            aria-label="Réglages du canal"
          >
            <div className="border-b border-white/10 px-4 py-3">
              <span className="hud-label font-semibold">Configuration du canal</span>
            </div>

            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              <label className="block text-xs text-[#94a3b8]">
                Description
                <textarea
                  rows={2}
                  value={draft.description}
                  onChange={(e) =>
                    setDraft({ ...draft, description: e.target.value })
                  }
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] p-2.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs text-[#94a3b8]">
                  Coordinateur
                  <select
                    value={draft.coordinateur}
                    onChange={(e) =>
                      setDraft({ ...draft, coordinateur: e.target.value })
                    }
                    className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                  >
                    {draft.membres.map((mId) => {
                      const ag = agents.find((a) => a.id === mId);
                      return (
                        <option key={mId} value={mId}>
                          {ag?.nom ?? mId}
                        </option>
                      );
                    })}
                  </select>
                </label>

                <label className="block text-xs text-[#94a3b8]">
                  Max tours / agent
                  <input
                    type="number"
                    min={1}
                    max={5}
                    value={draft.max_tours_par_agent}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        max_tours_par_agent: Math.max(
                          1,
                          Math.min(5, Number(e.target.value) || 1),
                        ),
                      })
                    }
                    className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 font-mono text-xs text-[#e2e8f0]"
                  />
                </label>
              </div>

              <div>
                <span className="hud-label block">
                  Membres ({draft.membres.length})
                </span>
                <ul className="mt-2 space-y-1.5">
                  {draft.membres.map((mId) => {
                    const ag = agents.find((a) => a.id === mId);
                    return (
                      <li
                        key={mId}
                        className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.06] bg-[#05070d]/60 px-2.5 py-1.5 text-xs"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          {ag && <AgentAvatar avatar={ag.avatar} size="sm" />}
                          <span className="truncate text-[#e2e8f0]">
                            {ag?.nom ?? mId}
                          </span>
                          {draft.coordinateur === mId && (
                            <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-mono text-[10px] text-[var(--accent)]">
                              coord.
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          onClick={() => removeMember(mId)}
                          aria-label={`Retirer ${ag?.nom ?? mId}`}
                          className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#f87171]"
                        >
                          <UserMinus className="h-3.5 w-3.5" />
                        </button>
                      </li>
                    );
                  })}
                </ul>

                {availableAgents.length > 0 && (
                  <div className="mt-2">
                    <span className="text-[11px] text-[#64748b]">
                      Ajouter un agent :
                    </span>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {availableAgents.map((ag) => (
                        <button
                          key={ag.id}
                          type="button"
                          onClick={() => addMember(ag.id)}
                          className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-[#e2e8f0] hover:border-[var(--accent)]/50 hover:text-[var(--accent)]"
                        >
                          <UserPlus className="h-3 w-3" />
                          {ag.nom}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div>
                <span className="hud-label block">Mémoire du canal</span>
                <div className="mt-2 max-h-44 overflow-y-auto rounded-xl border border-white/10 bg-[#05070d]/70 p-3">
                  {draft.memoire_md?.trim() ? (
                    <MarkdownContent content={draft.memoire_md} className="text-xs" />
                  ) : (
                    <p className="text-xs text-[#64748b]">
                      Aucune note mémorisée pour ce canal.
                    </p>
                  )}
                </div>
              </div>

              <div>
                <span className="hud-label block">Routines liées</span>
                {draft.routines.length === 0 ? (
                  <p className="mt-1.5 text-xs text-[#64748b]">
                    Aucune routine associée.
                  </p>
                ) : (
                  <ul className="mt-1.5 space-y-1">
                    {draft.routines.map((r) => (
                      <li key={r}>
                        <Link
                          to="/routines"
                          className="block rounded-lg border border-white/10 bg-[#05070d]/60 px-2.5 py-1.5 font-mono text-xs text-[var(--accent)] hover:border-[var(--accent)]/40"
                        >
                          {r}
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            <div className="flex justify-end border-t border-white/10 p-3">
              <button
                type="button"
                disabled={saving}
                onClick={() => void handleSave()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-3.5 py-1.5 text-xs font-semibold text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50"
              >
                <Save className="h-3.5 w-3.5" />
                {saving ? 'Enregistrement…' : 'Enregistrer'}
              </button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
