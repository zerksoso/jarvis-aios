import { useCallback, useEffect, useState } from 'react';
import { Link, NavLink, useParams } from 'react-router-dom';
import {
  CheckCircle2,
  Cpu,
  Database,
  Download,
  Info,
  LoaderCircle,
  Palette,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  XCircle,
} from 'lucide-react';
import type { MajMode } from '@/api/maj';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import {
  EFFORT_OPTIONS,
  getModelAvailability,
  MOTEUR_OPTIONS,
} from '@/lib/llm';
import type { Effort, Memoire, MoteurId, Parametres } from '@/lib/types';
import { useMaj } from '@/store/useMaj';
import { useMoteurs } from '@/store/useMoteurs';
import { useParametres } from '@/store/useParametres';
import { useToasts } from '@/store/useToasts';

const ACCENT_PRESETS = [
  { hex: '#22d3ee', label: 'Cyan' },
  { hex: '#38bdf8', label: 'Azur' },
  { hex: '#a78bfa', label: 'Violet' },
  { hex: '#34d399', label: 'Émeraude' },
  { hex: '#f59e0b', label: 'Ambre' },
  { hex: '#f472b6', label: 'Magenta' },
];

const VOIX_OPTIONS = [
  { id: 'fr-FR-DeniseNeural', label: 'Denise (fr-FR-DeniseNeural)' },
  { id: 'fr-FR-HenriNeural', label: 'Henri (fr-FR-HenriNeural)' },
];

const MEMOIRE_TYPES: Array<{ id: Memoire['type']; label: string }> = [
  { id: 'preference', label: 'Préférence' },
  { id: 'correction', label: 'Correction' },
  { id: 'regle', label: 'Règle' },
  { id: 'fait', label: 'Fait' },
];

const MAJ_MODES: Array<{ id: MajMode; label: string; desc: string }> = [
  {
    id: 'demander',
    label: 'Demander avant d’installer',
    desc: 'Affiche une notification et attend ton accord.',
  },
  {
    id: 'auto',
    label: 'Automatique',
    desc: 'Télécharge et applique les mises à jour dès qu’elles sortent.',
  },
  {
    id: 'jamais',
    label: 'Jamais',
    desc: 'Désactive la vérification automatique (vérification manuelle possible).',
  },
];

/**
 * Formate l'horodatage ISO de la dernière vérification de mise à jour en français.
 */
function formatMajDate(iso: string | null): string {
  if (!iso) return 'Jamais';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat('fr-FR', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(d);
}

/**
 * Vue `#/parametres/:section` : Personnalisation, Mémoire, Modèles par défaut et Mises à jour.
 */
export function ParametresView() {
  const { section = 'personnalisation' } = useParams<{ section?: string }>();
  const activeSection =
    section === 'memoire' || section === 'modeles' || section === 'maj'
      ? section
      : 'personnalisation';

  const parametres = useParametres((s) => s.parametres);
  const saving = useParametres((s) => s.saving);
  const updateParametres = useParametres((s) => s.updateParametres);
  const modeles = useMoteurs((s) => s.modeles);
  const moteursStatut = useMoteurs((s) => s.moteursStatut);
  const fetchMoteurs = useMoteurs((s) => s.fetchMoteurs);
  const addToast = useToasts((s) => s.addToast);

  const majVersion = useMaj((s) => s.version);
  const majDerniere = useMaj((s) => s.derniere);
  const majDisponible = useMaj((s) => s.disponible);
  const majVerifieLe = useMaj((s) => s.verifie_le);
  const majMode = useMaj((s) => s.mode);
  const majLoaded = useMaj((s) => s.loaded);
  const majChecking = useMaj((s) => s.checking);
  const majEndpointDisponible = useMaj((s) => s.endpointDisponible);
  const majError = useMaj((s) => s.error);
  const fetchMajEtat = useMaj((s) => s.fetchEtat);
  const verifierMajMaintenant = useMaj((s) => s.verifierMaintenant);
  const changerMajMode = useMaj((s) => s.changerMode);
  const setMajModalOpen = useMaj((s) => s.setModalOpen);

  const [draft, setDraft] = useState<Parametres>(parametres);

  // État section Mémoire
  const [memoires, setMemoires] = useState<Memoire[]>([]);
  const [loadingMemoires, setLoadingMemoires] = useState(false);
  const [newMemType, setNewMemType] = useState<Memoire['type']>('preference');
  const [newMemTexte, setNewMemTexte] = useState('');

  useEffect(() => {
    setDraft(parametres);
  }, [parametres]);

  const loadMemoires = useCallback(async () => {
    setLoadingMemoires(true);
    try {
      const list = await api.get<Memoire[]>('/api/memoires', { silent: true });
      setMemoires(Array.isArray(list) ? list : []);
    } catch {
      setMemoires([]);
    } finally {
      setLoadingMemoires(false);
    }
  }, []);

  useEffect(() => {
    if (activeSection === 'memoire') {
      void loadMemoires();
    } else if (activeSection === 'modeles') {
      void fetchMoteurs();
    } else if (activeSection === 'maj') {
      void fetchMajEtat({ silent: true });
    }
  }, [activeSection, loadMemoires, fetchMoteurs, fetchMajEtat]);

  const savePartial = async (partiel: Partial<Parametres>, successMsg: string) => {
    try {
      await updateParametres(partiel);
      addToast(successMsg, 'succes');
    } catch {
      // Erreur notifiée par toast global
    }
  };

  const handleAddMemoire = async (e: React.FormEvent) => {
    e.preventDefault();
    const clean = newMemTexte.trim();
    if (!clean) return;
    try {
      await api.post('/api/memoires', {
        type: newMemType,
        texte: clean,
        source: 'manuel',
      });
      setNewMemTexte('');
      addToast('Souvenir ajouté.', 'succes');
      await loadMemoires();
    } catch {
      // Toast global API
    }
  };

  const handleDeleteMemoire = async (id: string) => {
    try {
      await api.del(`/api/memoires/${encodeURIComponent(id)}`);
      setMemoires((prev) => prev.filter((m) => m.id !== id));
      addToast('Souvenir supprimé.', 'info');
    } catch {
      // Toast global API
    }
  };

  const tabs = [
    {
      to: '/parametres/personnalisation',
      id: 'personnalisation',
      label: 'Personnalisation',
      icon: Palette,
    },
    {
      to: '/parametres/memoire',
      id: 'memoire',
      label: 'Mémoire',
      icon: Database,
    },
    {
      to: '/parametres/modeles',
      id: 'modeles',
      label: 'Modèles par défaut',
      icon: Cpu,
    },
    {
      to: '/parametres/maj',
      id: 'maj',
      label: 'Mises à jour',
      icon: RefreshCw,
    },
  ];

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-[#f8fafc]">
            Paramètres
          </h1>
          <p className="text-xs text-[#94a3b8]">
            Configuration globale de Jarvis AIOS, de la mémoire persistante et du routeur LLM.
          </p>
        </div>
        <Link
          to="/bienvenue"
          className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/40 bg-[var(--accent)]/10 px-3.5 py-2 text-xs font-medium text-[var(--accent)] transition hover:bg-[var(--accent)]/20"
        >
          <Sparkles className="h-3.5 w-3.5" />
          Assistant de bienvenue
        </Link>
      </div>

      <nav
        aria-label="Sections des paramètres"
        className="flex flex-wrap gap-2 border-b border-white/10 pb-3"
      >
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <NavLink
              key={tab.id}
              to={tab.to}
              className={({ isActive }) =>
                cn(
                  'inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 text-xs font-medium transition',
                  isActive
                    ? 'border-[var(--accent)]/50 bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'border-white/10 bg-[#0b1020] text-[#94a3b8] hover:text-[#e2e8f0]',
                )
              }
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </NavLink>
          );
        })}
      </nav>

      {activeSection === 'personnalisation' && (
        <section className="hud-panel space-y-5 p-5" aria-label="Personnalisation">
          <div>
            <span className="hud-label block">Verbosité des réponses</span>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3">
              {(
                [
                  { id: 'concis', label: 'Concis', desc: 'Aller à l’essentiel' },
                  { id: 'normal', label: 'Normal', desc: 'Équilibre naturel' },
                  { id: 'detaille', label: 'Détaillé', desc: 'Explications complètes' },
                ] as const
              ).map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setDraft({ ...draft, verbosite: opt.id })}
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    draft.verbosite === opt.id
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                      : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                  )}
                >
                  <div className="text-xs font-semibold text-[#f8fafc]">{opt.label}</div>
                  <div className="mt-0.5 text-[11px] text-[#94a3b8]">{opt.desc}</div>
                </button>
              ))}
            </div>
          </div>

          <div>
            <span className="hud-label block">Couleur d&apos;accent HUD</span>
            <div className="mt-2 flex flex-wrap items-center gap-2.5">
              {ACCENT_PRESETS.map((preset) => (
                <button
                  key={preset.hex}
                  type="button"
                  onClick={() => setDraft({ ...draft, accent: preset.hex })}
                  aria-label={`Accent ${preset.label}`}
                  className={cn(
                    'inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition',
                    draft.accent.toLowerCase() === preset.hex.toLowerCase()
                      ? 'border-white bg-white/10 text-[#f8fafc]'
                      : 'border-white/15 bg-[#05070d]/70 text-[#94a3b8]',
                  )}
                >
                  <span
                    className="h-3.5 w-3.5 rounded-full"
                    style={{ backgroundColor: preset.hex }}
                  />
                  {preset.label}
                </button>
              ))}
              <label className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-[#05070d]/70 px-3 py-1 text-xs text-[#94a3b8]">
                <span>Libre</span>
                <input
                  type="color"
                  value={draft.accent}
                  onChange={(e) => setDraft({ ...draft, accent: e.target.value })}
                  aria-label="Sélecteur de couleur d'accent"
                  className="h-5 w-7 cursor-pointer bg-transparent"
                />
                <span className="font-mono text-[#e2e8f0]">{draft.accent}</span>
              </label>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-[#05070d]/60 p-3.5 text-xs">
              <div>
                <div className="font-medium text-[#f8fafc]">Synthèse vocale (TTS)</div>
                <div className="mt-0.5 text-[11px] text-[#94a3b8]">
                  Lit les réponses vocales de Jarvis à voix haute.
                </div>
              </div>
              <input
                type="checkbox"
                checked={draft.voix.active}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    voix: { ...draft.voix, active: e.target.checked },
                  })
                }
                className="accent-[var(--accent)]"
              />
            </label>

            <label className="block rounded-xl border border-white/10 bg-[#05070d]/60 p-3.5 text-xs text-[#94a3b8]">
              Voix Edge-TTS
              <select
                value={draft.voix.nom}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    voix: { ...draft.voix, nom: e.target.value },
                  })
                }
                className="mt-1.5 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
              >
                {VOIX_OPTIONS.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              disabled={saving}
              onClick={() =>
                void savePartial(
                  {
                    verbosite: draft.verbosite,
                    accent: draft.accent,
                    voix: draft.voix,
                  },
                  'Personnalisation enregistrée.',
                )
              }
              className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {saving ? 'Enregistrement…' : 'Enregistrer'}
            </button>
          </div>
        </section>
      )}

      {activeSection === 'memoire' && (
        <section className="hud-panel space-y-4 p-5" aria-label="Mémoire du système">
          <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-[#05070d]/60 p-3.5 text-xs">
            <div>
              <div className="font-medium text-[#f8fafc]">
                Extraction automatique des souvenirs
              </div>
              <div className="mt-0.5 text-[11px] text-[#94a3b8]">
                Extrait périodiquement préférences, corrections et règles depuis vos échanges.
              </div>
            </div>
            <input
              type="checkbox"
              checked={draft.memoire_auto}
              onChange={(e) => {
                const next = e.target.checked;
                setDraft({ ...draft, memoire_auto: next });
                void savePartial(
                  { memoire_auto: next },
                  next ? 'Mémoire automatique activée.' : 'Mémoire automatique désactivée.',
                );
              }}
              className="accent-[var(--accent)]"
            />
          </label>

          <form onSubmit={(e) => void handleAddMemoire(e)} className="flex flex-wrap gap-2">
            <select
              aria-label="Type de souvenir"
              value={newMemType}
              onChange={(e) => setNewMemType(e.target.value as Memoire['type'])}
              className="rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
            >
              {MEMOIRE_TYPES.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={newMemTexte}
              onChange={(e) => setNewMemTexte(e.target.value)}
              placeholder="Ajouter un souvenir ou une règle permanente…"
              aria-label="Texte du souvenir"
              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0]"
            />
            <button
              type="submit"
              className="inline-flex items-center gap-1 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3.5 py-2 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25"
            >
              <Plus className="h-3.5 w-3.5" />
              Ajouter
            </button>
          </form>

          {loadingMemoires ? (
            <div className="h-32 animate-pulse rounded-xl bg-white/5" />
          ) : memoires.length === 0 ? (
            <p className="py-6 text-center text-xs text-[#64748b]">
              Aucun souvenir enregistré dans le second cerveau.
            </p>
          ) : (
            <ul className="space-y-2">
              {memoires.map((mem) => (
                <li
                  key={mem.id}
                  className="flex items-start justify-between gap-3 rounded-xl border border-white/10 bg-[#05070d]/70 p-3 text-xs"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--accent)]">
                        {mem.type}
                      </span>
                      {mem.source && (
                        <span className="font-mono text-[10px] text-[#64748b]">
                          source : {mem.source}
                        </span>
                      )}
                    </div>
                    <p className="leading-relaxed text-[#e2e8f0]">{mem.texte}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleDeleteMemoire(mem.id)}
                    aria-label="Supprimer ce souvenir"
                    className="rounded p-1.5 text-[#94a3b8] hover:bg-white/10 hover:text-[#f87171]"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {activeSection === 'modeles' && (
        <section className="hud-panel space-y-5 p-5" aria-label="Modèles par défaut">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <label className="block text-xs text-[#94a3b8]">
              Moteur par défaut
              <select
                value={draft.moteur}
                onChange={(e) =>
                  setDraft({ ...draft, moteur: e.target.value as MoteurId })
                }
                className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
              >
                {MOTEUR_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-xs text-[#94a3b8]">
              Modèle par défaut
              <select
                value={draft.modele}
                onChange={(e) => setDraft({ ...draft, modele: e.target.value })}
                className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
              >
                {modeles.length > 0 ? (
                  modeles.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.libelle}
                    </option>
                  ))
                ) : (
                  <option value={draft.modele}>{draft.modele}</option>
                )}
              </select>
            </label>

            <label className="block text-xs text-[#94a3b8]">
              Effort par défaut
              <select
                value={draft.effort}
                onChange={(e) =>
                  setDraft({ ...draft, effort: e.target.value as Effort })
                }
                className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-2 text-xs text-[#e2e8f0]"
              >
                {EFFORT_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="flex items-start gap-2.5 rounded-xl border border-[var(--accent)]/30 bg-[var(--accent)]/8 p-3.5 text-xs text-[#cbd5e1]">
            <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]" />
            <p className="leading-relaxed">
              <strong>Ordre de repli automatique :</strong> les requêtes privilégient{' '}
              <span className="font-mono text-[#f8fafc]">Antigravity</span> (Gemini 4
              Hydrogen → Gemini 3.8 Flash → Claude Sonnet → Gemini 4 Argon sous réserve
              de quota ≥ 10 %), puis basculent sur{' '}
              <span className="font-mono text-[#f8fafc]">Claude Code CLI</span> en cas
              d&apos;indisponibilité ou d&apos;échec.
            </p>
          </div>

          <div className="overflow-x-auto rounded-xl border border-white/10">
            <table className="w-full border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-white/10 bg-[#05070d]/80 text-[11px] uppercase tracking-wider text-[#94a3b8]">
                  <th className="px-3 py-2.5">Modèle</th>
                  <th className="px-3 py-2.5">Moteur</th>
                  <th className="px-3 py-2.5">Backend</th>
                  <th className="px-3 py-2.5">Statut</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {modeles.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-6 text-center text-[#64748b]">
                      Aucun modèle chargé depuis `/api/moteurs`.
                    </td>
                  </tr>
                ) : (
                  modeles.map((m) => {
                    const avail = getModelAvailability(m, moteursStatut);
                    return (
                      <tr key={m.id} className="bg-[#0b1020]/50">
                        <td className="px-3 py-2.5 font-medium text-[#f8fafc]">
                          <div>{m.libelle}</div>
                          <div className="font-mono text-[10px] text-[#64748b]">
                            {m.id}
                          </div>
                        </td>
                        <td className="px-3 py-2.5 font-mono text-[#cbd5e1]">
                          {m.moteur_ui}
                        </td>
                        <td className="px-3 py-2.5 font-mono text-[#94a3b8]">
                          {m.backend}
                        </td>
                        <td className="px-3 py-2.5">
                          {avail.disponible ? (
                            <span className="inline-flex items-center gap-1 text-[#34d399]">
                              <CheckCircle2 className="h-3.5 w-3.5" />
                              Disponible
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[#f87171]">
                              <XCircle className="h-3.5 w-3.5" />
                              Indisponible{avail.raison ? ` : ${avail.raison}` : ''}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              disabled={saving}
              onClick={() =>
                void savePartial(
                  {
                    moteur: draft.moteur,
                    modele: draft.modele,
                    effort: draft.effort,
                  },
                  'Modèles par défaut enregistrés.',
                )
              }
              className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {saving ? 'Enregistrement…' : 'Enregistrer'}
            </button>
          </div>
        </section>
      )}

      {activeSection === 'maj' && (
        <section className="hud-panel space-y-5 p-5" aria-label="Mises à jour">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-[#05070d]/60 p-4">
            <div className="space-y-1 text-xs">
              <div className="hud-label">Version installée</div>
              <div className="font-mono text-base font-semibold text-[#f8fafc]">
                {majVersion ? `v${majVersion.replace(/^v/i, '')}` : '—'}
              </div>
              <div className="text-[11px] text-[#94a3b8]">
                Dernière vérification : {formatMajDate(majVerifieLe)}
              </div>
            </div>

            <button
              type="button"
              disabled={majChecking}
              onClick={() => {
                void verifierMajMaintenant().then((res) => {
                  if (!res) return;
                  if (res.disponible && res.derniere?.version) {
                    addToast(
                      `Mise à jour v${res.derniere.version.replace(/^v/i, '')} disponible.`,
                      'info',
                    );
                  } else {
                    addToast('Jarvis est déjà à jour.', 'succes');
                  }
                });
              }}
              className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-4 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25 disabled:opacity-50"
            >
              {majChecking ? (
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              {majChecking ? 'Vérification…' : 'Vérifier maintenant'}
            </button>
          </div>

          {majLoaded && !majEndpointDisponible && (
            <p className="rounded-xl border border-white/10 bg-[#05070d]/60 p-3 text-xs text-[#94a3b8]">
              Le service de mise à jour n&apos;est pas actif sur ce serveur.
            </p>
          )}

          {majError && (
            <p
              role="alert"
              className="rounded-xl border border-[#f87171]/40 bg-[#f87171]/10 p-3 text-xs text-[#fca5a5]"
            >
              {majError}
            </p>
          )}

          {majDisponible && majDerniere && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#34d399]/40 bg-[#34d399]/10 p-4 text-xs">
              <div className="space-y-0.5">
                <div className="font-semibold text-[#34d399]">
                  Mise à jour v{majDerniere.version.replace(/^v/i, '')} disponible
                </div>
                <div className="text-[#cbd5e1]">
                  {majDerniere.legere
                    ? 'Mise à jour légère prête à être installée.'
                    : 'Mise à jour complète prête à être installée.'}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setMajModalOpen(true)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[#34d399]/50 bg-[#34d399]/20 px-3.5 py-2 font-semibold text-[#34d399] transition hover:bg-[#34d399]/30"
              >
                <Download className="h-3.5 w-3.5" />
                Voir et installer
              </button>
            </div>
          )}

          <div>
            <span className="hud-label block">Politique de mise à jour</span>
            <div className="mt-2 grid grid-cols-1 gap-2.5 sm:grid-cols-3">
              {MAJ_MODES.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => {
                    void changerMajMode(opt.id)
                      .then(() => {
                        addToast(`Mode enregistré : ${opt.label}.`, 'succes');
                      })
                      .catch(() => {
                        // Erreur déjà notifiée
                      });
                  }}
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    majMode === opt.id
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                      : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                  )}
                >
                  <div className="text-xs font-semibold text-[#f8fafc]">
                    {opt.label}
                  </div>
                  <div className="mt-0.5 text-[11px] text-[#94a3b8]">
                    {opt.desc}
                  </div>
                </button>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
