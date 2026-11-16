import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Plus, Save, SlidersHorizontal, Trash2, X } from 'lucide-react';
import { AgentAvatar } from '@/components/AgentAvatar';
import { ChatPanel } from '@/components/ChatPanel';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { EFFORT_OPTIONS } from '@/lib/llm';
import type {
  Agent,
  AvatarForme,
  AvatarYeux,
  Effort,
  Skill,
} from '@/lib/types';
import { useAgents } from '@/store/useAgents';
import { useMoteurs } from '@/store/useMoteurs';
import { useToasts } from '@/store/useToasts';

type AgentTab =
  | 'identite'
  | 'instructions'
  | 'modeles'
  | 'souvenirs'
  | 'skills'
  | 'acces'
  | 'routines'
  | 'fichiers';

const TABS: Array<{ id: AgentTab; label: string }> = [
  { id: 'identite', label: 'Identité' },
  { id: 'instructions', label: 'Instructions' },
  { id: 'modeles', label: 'Modèles' },
  { id: 'souvenirs', label: 'Souvenirs' },
  { id: 'skills', label: 'Skills' },
  { id: 'acces', label: 'Accès' },
  { id: 'routines', label: 'Routines' },
  { id: 'fichiers', label: 'Fichiers' },
];

const KNOWN_OUTILS = [
  'cerveau.chercher',
  'cerveau.lire',
  'cerveau.ecrire',
  'memoriser',
  'web.chercher',
  'web.lire',
  'taches.lister',
  'taches.ajouter',
  'taches.cocher',
  'carte.afficher',
  'script.executer',
  'skill.creer',
  'agent.creer',
  'ecran.capturer',
  'ecran.fenetre_active',
  'ecran.trouver',
  'pc.action',
  'professeur.guider',
  'fichier.supprimer',
];

const FORMES: AvatarForme[] = ['sphere', 'cube', 'diamant', 'anneau', 'goutte'];
const YEUX: AvatarYeux[] = ['ronds', 'fentes', 'points', 'visiere'];

/**
 * Vue `#/agent/:id` : discussion streaming avec l'agent et panneau complet de configuration.
 */
export function AgentView() {
  const { id = '' } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const upsertAgent = useAgents((s) => s.upsertAgent);
  const removeAgent = useAgents((s) => s.removeAgent);
  const modeles = useMoteurs((s) => s.modeles);
  const addToast = useToasts((s) => s.addToast);

  const [draft, setDraft] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [activeTab, setActiveTab] = useState<AgentTab>('identite');
  const [panelOpen, setPanelOpen] = useState(true);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [newMemory, setNewMemory] = useState('');
  const [customOutil, setCustomOutil] = useState('');

  useEffect(() => {
    setConfirmDelete(false);
    setLoading(true);
    let cancelled = false;

    const cached = useAgents.getState().agents.find((a) => a.id === id);
    if (cached) {
      setDraft(structuredClone(cached));
    }

    void api
      .get<Agent>(`/api/agents/${encodeURIComponent(id)}`, { silent: true })
      .then((agent) => {
        if (!cancelled && agent) {
          setDraft(agent);
          useAgents.getState().upsertAgent(agent);
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

    void api
      .get<Skill[]>('/api/skills', { silent: true })
      .then((list) => {
        if (!cancelled && Array.isArray(list)) {
          setSkills(list);
        }
      })
      .catch(() => {
        // Liste de skills optionnelle
      });

    return () => {
      cancelled = true;
    };
  }, [id]);

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const updated = await api.put<Agent>(
        `/api/agents/${encodeURIComponent(draft.id)}`,
        draft,
      );
      const next = updated ?? draft;
      setDraft(next);
      upsertAgent(next);
      addToast(`Agent « ${next.nom} » enregistré.`, 'succes');
    } catch {
      // Erreur notifiée par toast global
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!draft || draft.id === 'jarvis') return;
    try {
      await api.del(`/api/agents/${encodeURIComponent(draft.id)}`);
      removeAgent(draft.id);
      addToast(`Agent « ${draft.nom} » supprimé.`, 'info');
      navigate('/accueil');
    } catch {
      // Erreur gérée par toast global
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
        <p className="text-sm text-[#94a3b8]">Agent « {id} » introuvable.</p>
        <Link
          to="/accueil"
          className="mt-3 inline-block text-xs text-[var(--accent)] hover:underline"
        >
          Retour au tableau de bord
        </Link>
      </div>
    );
  }

  const allOutils = Array.from(new Set([...KNOWN_OUTILS, ...draft.outils]));

  const toggleSkill = (nom: string) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const has = prev.skills_assignes.includes(nom);
      return {
        ...prev,
        skills_assignes: has
          ? prev.skills_assignes.filter((s) => s !== nom)
          : [...prev.skills_assignes, nom],
      };
    });
  };

  const toggleOutil = (outil: string) => {
    setDraft((prev) => {
      if (!prev) return prev;
      const has = prev.outils.includes(outil);
      return {
        ...prev,
        outils: has
          ? prev.outils.filter((o) => o !== outil)
          : [...prev.outils, outil],
      };
    });
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <AgentAvatar avatar={draft.avatar} size="md" />
          <div>
            <h1 className="font-display text-lg font-semibold text-[#f8fafc]">
              {draft.nom}
            </h1>
            <p className="text-xs text-[#94a3b8]">{draft.titre}</p>
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
          panelOpen && 'xl:grid-cols-[minmax(0,1fr)_390px]',
        )}
      >
        <ChatPanel
          type="agent"
          cibleId={draft.id}
          defaultModele={draft.modele_principal}
          defaultEffort={draft.effort_raisonnement}
          className="min-h-[520px]"
        />

        {panelOpen && (
          <aside
            className="hud-panel flex flex-col overflow-hidden"
            aria-label="Réglages de l'agent"
          >
            <div className="flex flex-wrap gap-1 border-b border-white/10 p-2.5">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    'rounded-lg px-2.5 py-1 text-[11px] font-medium transition',
                    activeTab === tab.id
                      ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                      : 'text-[#94a3b8] hover:bg-white/5 hover:text-[#e2e8f0]',
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {activeTab === 'identite' && (
                <div className="space-y-3">
                  <div className="flex items-center gap-4 rounded-xl border border-white/10 bg-[#05070d]/70 p-3">
                    <AgentAvatar avatar={draft.avatar} size="lg" />
                    <div className="min-w-0 flex-1 space-y-2">
                      <label className="block text-xs text-[#94a3b8]">
                        Nom
                        <input
                          type="text"
                          value={draft.nom}
                          onChange={(e) =>
                            setDraft({ ...draft, nom: e.target.value })
                          }
                          className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                        />
                      </label>
                      <label className="block text-xs text-[#94a3b8]">
                        Titre
                        <input
                          type="text"
                          value={draft.titre}
                          onChange={(e) =>
                            setDraft({ ...draft, titre: e.target.value })
                          }
                          className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                        />
                      </label>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <label className="block text-xs text-[#94a3b8]">
                      Forme
                      <select
                        value={draft.avatar.forme}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            avatar: {
                              ...draft.avatar,
                              forme: e.target.value as AvatarForme,
                            },
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        {FORMES.map((f) => (
                          <option key={f} value={f}>
                            {f}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="block text-xs text-[#94a3b8]">
                      Yeux
                      <select
                        value={draft.avatar.yeux}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            avatar: {
                              ...draft.avatar,
                              yeux: e.target.value as AvatarYeux,
                            },
                          })
                        }
                        className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                      >
                        {YEUX.map((y) => (
                          <option key={y} value={y}>
                            {y}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <label className="block text-xs text-[#94a3b8]">
                    Couleur de mascotte
                    <div className="mt-1.5 flex items-center gap-2">
                      <input
                        type="color"
                        value={draft.avatar.couleur}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            avatar: { ...draft.avatar, couleur: e.target.value },
                          })
                        }
                        className="h-8 w-12 cursor-pointer rounded border border-white/15 bg-transparent"
                      />
                      <span className="font-mono text-xs text-[#e2e8f0]">
                        {draft.avatar.couleur}
                      </span>
                    </div>
                  </label>
                </div>
              )}

              {activeTab === 'instructions' && (
                <label className="block text-xs text-[#94a3b8]">
                  Instructions système &amp; persona
                  <textarea
                    rows={12}
                    value={draft.instructions}
                    onChange={(e) =>
                      setDraft({ ...draft, instructions: e.target.value })
                    }
                    className="mt-1.5 w-full rounded-xl border border-white/10 bg-[#05070d] p-3 text-xs leading-relaxed text-[#e2e8f0]"
                  />
                </label>
              )}

              {activeTab === 'modeles' && (
                <div className="space-y-3">
                  <label className="block text-xs text-[#94a3b8]">
                    Modèle principal
                    <select
                      value={draft.modele_principal}
                      onChange={(e) =>
                        setDraft({ ...draft, modele_principal: e.target.value })
                      }
                      className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                    >
                      {modeles.length > 0 ? (
                        modeles.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.libelle}
                          </option>
                        ))
                      ) : (
                        <option value={draft.modele_principal}>
                          {draft.modele_principal}
                        </option>
                      )}
                    </select>
                  </label>

                  <label className="block text-xs text-[#94a3b8]">
                    Effort de raisonnement
                    <select
                      value={draft.effort_raisonnement}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          effort_raisonnement: e.target.value as Effort,
                        })
                      }
                      className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                    >
                      {EFFORT_OPTIONS.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="block text-xs text-[#94a3b8]">
                    Modèle de secours
                    <select
                      value={draft.modele_secours}
                      onChange={(e) =>
                        setDraft({ ...draft, modele_secours: e.target.value })
                      }
                      className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                    >
                      {modeles.length > 0 ? (
                        modeles.map((m) => (
                          <option key={m.id} value={m.id}>
                            {m.libelle}
                          </option>
                        ))
                      ) : (
                        <option value={draft.modele_secours}>
                          {draft.modele_secours}
                        </option>
                      )}
                    </select>
                  </label>
                </div>
              )}

              {activeTab === 'souvenirs' && (
                <div className="space-y-3">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={newMemory}
                      onChange={(e) => setNewMemory(e.target.value)}
                      placeholder="Ajouter un souvenir propre à cet agent…"
                      className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const clean = newMemory.trim();
                        if (!clean) return;
                        setDraft({
                          ...draft,
                          souvenirs: [...draft.souvenirs, clean],
                        });
                        setNewMemory('');
                      }}
                      aria-label="Ajouter le souvenir"
                      className="rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-2.5 text-[var(--accent)]"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>

                  {draft.souvenirs.length === 0 ? (
                    <p className="text-xs text-[#64748b]">Aucun souvenir propre.</p>
                  ) : (
                    <ul className="space-y-2">
                      {draft.souvenirs.map((mem, idx) => (
                        <li key={`${idx}-${mem.slice(0, 12)}`} className="flex gap-2">
                          <input
                            type="text"
                            value={mem}
                            onChange={(e) => {
                              const next = [...draft.souvenirs];
                              next[idx] = e.target.value;
                              setDraft({ ...draft, souvenirs: next });
                            }}
                            className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              setDraft({
                                ...draft,
                                souvenirs: draft.souvenirs.filter((_, i) => i !== idx),
                              })
                            }
                            aria-label="Supprimer ce souvenir"
                            className="rounded p-1.5 text-[#94a3b8] hover:bg-white/10 hover:text-[#f87171]"
                          >
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {activeTab === 'skills' && (
                <div className="space-y-2">
                  {skills.length === 0 ? (
                    <p className="text-xs text-[#64748b]">
                      Aucun skill disponible depuis `/api/skills`.
                    </p>
                  ) : (
                    skills.map((skill) => (
                      <label
                        key={skill.nom}
                        className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-white/[0.06] bg-[#05070d]/60 p-2.5 text-xs hover:bg-white/[0.04]"
                      >
                        <input
                          type="checkbox"
                          checked={draft.skills_assignes.includes(skill.nom)}
                          onChange={() => toggleSkill(skill.nom)}
                          className="mt-0.5 accent-[var(--accent)]"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="font-medium text-[#f8fafc]">{skill.nom}</div>
                          <div className="text-[11px] text-[#94a3b8]">
                            {skill.description}
                          </div>
                        </div>
                      </label>
                    ))
                  )}
                </div>
              )}

              {activeTab === 'acces' && (
                <div className="space-y-3">
                  <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-[#05070d]/60 p-3 text-xs">
                    <span>Accès au second cerveau</span>
                    <input
                      type="checkbox"
                      checked={draft.acces_second_cerveau}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          acces_second_cerveau: e.target.checked,
                        })
                      }
                      className="accent-[var(--accent)]"
                    />
                  </label>

                  <div>
                    <span className="hud-label block">Outils autorisés</span>
                    <div className="mt-2 grid max-h-56 grid-cols-1 gap-1.5 overflow-y-auto rounded-xl border border-white/10 bg-[#05070d]/60 p-2.5">
                      {allOutils.map((outil) => (
                        <label
                          key={outil}
                          className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 font-mono text-[11px] text-[#e2e8f0] hover:bg-white/5"
                        >
                          <input
                            type="checkbox"
                            checked={draft.outils.includes(outil)}
                            onChange={() => toggleOutil(outil)}
                            className="accent-[var(--accent)]"
                          />
                          <span className="truncate">{outil}</span>
                        </label>
                      ))}
                    </div>

                    <div className="mt-2 flex gap-2">
                      <input
                        type="text"
                        value={customOutil}
                        onChange={(e) => setCustomOutil(e.target.value)}
                        placeholder="Ajouter un nom d'outil…"
                        className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 font-mono text-xs text-[#e2e8f0]"
                      />
                      <button
                        type="button"
                        onClick={() => {
                          const clean = customOutil.trim();
                          if (!clean) return;
                          if (!draft.outils.includes(clean)) {
                            setDraft({ ...draft, outils: [...draft.outils, clean] });
                          }
                          setCustomOutil('');
                        }}
                        aria-label="Ajouter l'outil"
                        className="rounded-lg border border-white/15 px-2.5 text-xs text-[#e2e8f0] hover:border-[var(--accent)]"
                      >
                        +
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {activeTab === 'routines' && (
                <div className="space-y-2">
                  {draft.routines.length === 0 ? (
                    <p className="text-xs text-[#64748b]">
                      Aucune routine liée à cet agent.
                    </p>
                  ) : (
                    draft.routines.map((r) => (
                      <Link
                        key={r}
                        to="/routines"
                        className="block rounded-lg border border-white/10 bg-[#05070d]/60 px-3 py-2 font-mono text-xs text-[var(--accent)] hover:border-[var(--accent)]/50"
                      >
                        {r}
                      </Link>
                    ))
                  )}
                </div>
              )}

              {activeTab === 'fichiers' && (
                <div className="space-y-2">
                  {draft.fichiers_generes.length === 0 ? (
                    <p className="text-xs text-[#64748b]">
                      Aucun fichier généré enregistré.
                    </p>
                  ) : (
                    <ul className="space-y-1.5">
                      {draft.fichiers_generes.map((file) => (
                        <li
                          key={file}
                          className="truncate rounded-lg border border-white/10 bg-[#05070d]/60 px-3 py-2 font-mono text-[11px] text-[#cbd5e1]"
                          title={file}
                        >
                          {file}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 p-3">
              {draft.id !== 'jarvis' ? (
                confirmDelete ? (
                  <div className="flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => void handleDelete()}
                      className="rounded-lg border border-[#f87171]/60 bg-[#f87171]/20 px-2.5 py-1.5 text-xs font-medium text-[#f87171]"
                    >
                      Confirmer
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(false)}
                      className="rounded-lg border border-white/10 px-2 py-1.5 text-xs text-[#94a3b8]"
                    >
                      Annuler
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(true)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-[#f87171] hover:border-[#f87171]/50 hover:bg-[#f87171]/10"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Supprimer
                  </button>
                )
              ) : (
                <span className="font-mono text-[10px] text-[#64748b]">
                  Agent système protégé
                </span>
              )}

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
