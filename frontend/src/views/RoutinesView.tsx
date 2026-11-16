import { useCallback, useEffect, useState } from 'react';
import {
  CalendarClock,
  CheckCircle2,
  Edit3,
  History,
  Loader2,
  Play,
  Plus,
  Save,
  Trash2,
  XCircle,
} from 'lucide-react';
import { MarkdownContent } from '@/components/MarkdownContent';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import { translateCronToFrench } from '@/lib/cron';
import type { Routine, RunRoutine, Skill } from '@/lib/types';
import { wsClient } from '@/lib/ws';
import { useAgents } from '@/store/useAgents';
import { useToasts } from '@/store/useToasts';

type RightMode = 'historique' | 'editeur';

const EMPTY_ROUTINE: Routine = {
  id: '',
  nom: '',
  cron: '0 8 * * *',
  agent: 'jarvis',
  skill: '',
  sortie: '02-Connaissances-Methodes/{date}-routine.md',
  actif: true,
  description: '',
  consigne: '',
};

/**
 * Formate une date ISO complète pour les listes d'exécution.
 */
function formatDateTimeFr(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

/**
 * Calcule la durée d'un run en secondes ou minutes lisibles.
 */
function formatRunDuration(debut: string, fin?: string | null): string {
  if (!fin) return 'en cours';
  const ms = new Date(fin).getTime() - new Date(debut).getTime();
  if (Number.isNaN(ms) || ms < 0) return '—';
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec} s`;
  return `${Math.floor(sec / 60)} min ${sec % 60} s`;
}

/**
 * Vue `#/routines` : liste des routines planifiées, traduction cron en français,
 * déclenchement manuel avec suivi temps réel, historique Markdown et éditeur complet.
 */
export function RoutinesView() {
  const agents = useAgents((s) => s.agents);
  const addToast = useToasts((s) => s.addToast);

  const [routines, setRoutines] = useState<Routine[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rightMode, setRightMode] = useState<RightMode>('historique');

  const [runs, setRuns] = useState<RunRoutine[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [selectedRun, setSelectedRun] = useState<RunRoutine | null>(null);
  const [runningIds, setRunningIds] = useState<Record<string, boolean>>({});

  // Éditeur
  const [editingNew, setEditingNew] = useState(false);
  const [form, setForm] = useState<Routine>(EMPTY_ROUTINE);
  const [saving, setSaving] = useState(false);

  // Préréglages cron interactifs
  const [presetHour, setPresetHour] = useState('08:00');
  const [presetDay, setPresetDay] = useState('1');

  const loadRoutines = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.get<Routine[]>('/api/routines', { silent: true });
      const items = Array.isArray(list) ? list : [];
      setRoutines(items);
      setSelectedId((prev) => prev ?? items[0]?.id ?? null);
    } catch {
      setRoutines([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadHistorique = useCallback(async (routineId: string) => {
    setLoadingRuns(true);
    try {
      const list = await api.get<RunRoutine[]>(
        `/api/routines/${encodeURIComponent(routineId)}/historique?limite=20`,
        { silent: true },
      );
      const items = Array.isArray(list) ? list : [];
      setRuns(items);
      setSelectedRun(items[0] ?? null);
    } catch {
      setRuns([]);
      setSelectedRun(null);
    } finally {
      setLoadingRuns(false);
    }
  }, []);

  useEffect(() => {
    void loadRoutines();
    void api
      .get<Skill[]>('/api/skills', { silent: true })
      .then((list) => {
        if (Array.isArray(list)) setSkills(list);
      })
      .catch(() => {
        // Skills optionnels
      });
  }, [loadRoutines]);

  useEffect(() => {
    if (selectedId && rightMode === 'historique') {
      void loadHistorique(selectedId);
    }
  }, [selectedId, rightMode, loadHistorique]);

  useEffect(() => {
    const unsub = wsClient.subscribe('routine_etat', (data) => {
      const id = typeof data.id === 'string' ? data.id : '';
      const statut = typeof data.statut === 'string' ? data.statut : '';
      if (!id) return;
      setRunningIds((prev) => ({ ...prev, [id]: statut === 'en_cours' }));
      if (statut !== 'en_cours') {
        void loadRoutines();
        if (selectedId === id) {
          void loadHistorique(id);
        }
      }
    });
    return unsub;
  }, [loadRoutines, loadHistorique, selectedId]);

  const handleToggleActif = async (routine: Routine) => {
    try {
      const updated = await api.put<Routine>(
        `/api/routines/${encodeURIComponent(routine.id)}`,
        { ...routine, actif: !routine.actif },
      );
      setRoutines((prev) =>
        prev.map((r) => (r.id === routine.id ? (updated ?? { ...r, actif: !r.actif }) : r)),
      );
      addToast(
        !routine.actif
          ? `Routine « ${routine.nom} » activée.`
          : `Routine « ${routine.nom} » mise en pause.`,
        'info',
      );
    } catch {
      // Toast global API
    }
  };

  const handleLaunchNow = async (routine: Routine) => {
    setRunningIds((prev) => ({ ...prev, [routine.id]: true }));
    try {
      await api.post(`/api/routines/${encodeURIComponent(routine.id)}/lancer`);
      addToast(`Routine « ${routine.nom} » lancée.`, 'succes');
      setSelectedId(routine.id);
      setRightMode('historique');
      void loadHistorique(routine.id);
    } catch {
      setRunningIds((prev) => ({ ...prev, [routine.id]: false }));
    }
  };

  const startNewRoutine = () => {
    setEditingNew(true);
    setForm({
      ...EMPTY_ROUTINE,
      agent: agents[0]?.id ?? 'jarvis',
    });
    setRightMode('editeur');
  };

  const startEditRoutine = async (routine: Routine) => {
    setEditingNew(false);
    setSelectedId(routine.id);
    setForm(routine);
    setRightMode('editeur');
    try {
      const full = await api.get<Routine>(
        `/api/routines/${encodeURIComponent(routine.id)}`,
        { silent: true },
      );
      if (full) setForm(full);
    } catch {
      // Conserve l'objet de liste si GET détail indisponible
    }
  };

  const handleSaveForm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.nom.trim()) return;
    setSaving(true);
    try {
      if (editingNew) {
        const slug = form.nom
          .trim()
          .toLowerCase()
          .normalize('NFD')
          .replace(/[\u0300-\u036f]/g, '')
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/(^-|-$)/g, '');
        const created = await api.post<Routine>('/api/routines', {
          ...form,
          id: form.id.trim() || slug || `routine-${Date.now()}`,
          skill: form.skill || null,
        });
        addToast(`Routine « ${created?.nom ?? form.nom} » créée.`, 'succes');
        setSelectedId(created?.id ?? null);
      } else {
        await api.put<Routine>(
          `/api/routines/${encodeURIComponent(form.id)}`,
          { ...form, skill: form.skill || null },
        );
        addToast(`Routine « ${form.nom} » enregistrée.`, 'succes');
      }
      await loadRoutines();
      setRightMode('historique');
    } catch {
      // Erreur affichée par toast global
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteRoutine = async (routine: Routine) => {
    try {
      await api.del(`/api/routines/${encodeURIComponent(routine.id)}`);
      addToast(`Routine « ${routine.nom} » supprimée.`, 'info');
      if (selectedId === routine.id) {
        setSelectedId(null);
      }
      await loadRoutines();
      setRightMode('historique');
    } catch {
      // Erreur gérée par toast global
    }
  };

  const applyDailyPreset = () => {
    const [hh, mm] = presetHour.split(':');
    setForm((prev) => ({
      ...prev,
      cron: `${Number(mm) || 0} ${Number(hh) || 0} * * *`,
    }));
  };

  const applyWeeklyPreset = () => {
    const [hh, mm] = presetHour.split(':');
    setForm((prev) => ({
      ...prev,
      cron: `${Number(mm) || 0} ${Number(hh) || 0} * * ${presetDay}`,
    }));
  };

  const selectedRoutine = routines.find((r) => r.id === selectedId) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-display text-xl font-semibold text-[#f8fafc]">
            Routines planifiées
          </h1>
          <p className="text-xs text-[#94a3b8]">
            Automatisations périodiques exécutées par vos agents AIOS.
          </p>
        </div>
        <button
          type="button"
          onClick={startNewRoutine}
          className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3.5 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25"
        >
          <Plus className="h-4 w-4" />
          Nouvelle routine
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
        {/* Liste des routines */}
        <section className="space-y-2.5" aria-label="Liste des routines">
          {loading ? (
            <div className="space-y-2.5">
              {[1, 2, 3].map((n) => (
                <div key={n} className="hud-panel h-24 animate-pulse" />
              ))}
            </div>
          ) : routines.length === 0 ? (
            <div className="hud-panel p-8 text-center text-xs text-[#94a3b8]">
              Aucune routine configurée. Créez-en une pour automatiser vos rapports ou briefs.
            </div>
          ) : (
            routines.map((routine) => {
              const isSelected = selectedId === routine.id;
              const isRunning =
                runningIds[routine.id] ||
                routine.dernier_run?.statut === 'en_cours';
              const lastStatus = routine.dernier_run?.statut;

              return (
                <article
                  key={routine.id}
                  className={cn(
                    'hud-panel p-4 transition',
                    isSelected && 'border-[var(--accent)]/50',
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => {
                        setSelectedId(routine.id);
                        setRightMode('historique');
                      }}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <CalendarClock className="h-4 w-4 shrink-0 text-[var(--accent)]" />
                        <h2 className="font-display text-sm font-semibold text-[#f8fafc]">
                          {routine.nom}
                        </h2>
                        <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[10px] text-[#cbd5e1]">
                          {routine.agent}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-[#94a3b8]">
                        {translateCronToFrench(routine.cron)}{' '}
                        <span className="font-mono text-[10px] text-[#64748b]">
                          ({routine.cron})
                        </span>
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-3 font-mono text-[11px] text-[#94a3b8]">
                        <span>
                          Prochaine : {formatDateTimeFr(routine.prochaine_execution)}
                        </span>
                        {lastStatus && (
                          <span
                            className={cn(
                              'inline-flex items-center gap-1',
                              lastStatus === 'ok'
                                ? 'text-[#34d399]'
                                : lastStatus === 'erreur'
                                  ? 'text-[#f87171]'
                                  : 'text-[var(--accent)]',
                            )}
                          >
                            {lastStatus === 'ok' ? (
                              <CheckCircle2 className="h-3 w-3" />
                            ) : lastStatus === 'erreur' ? (
                              <XCircle className="h-3 w-3" />
                            ) : (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            )}
                            Dernier : {lastStatus}
                          </span>
                        )}
                      </div>
                    </button>

                    <div className="flex shrink-0 items-center gap-2">
                      <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-[#94a3b8]">
                        <input
                          type="checkbox"
                          checked={routine.actif}
                          onChange={() => void handleToggleActif(routine)}
                          aria-label={`Activer ou désactiver ${routine.nom}`}
                          className="accent-[var(--accent)]"
                        />
                        <span>{routine.actif ? 'Active' : 'Inactive'}</span>
                      </label>

                      <button
                        type="button"
                        disabled={isRunning}
                        onClick={() => void handleLaunchNow(routine)}
                        className="inline-flex items-center gap-1 rounded-lg border border-[var(--accent)]/45 bg-[var(--accent)]/15 px-2.5 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
                      >
                        {isRunning ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Play className="h-3.5 w-3.5" />
                        )}
                        {isRunning ? 'En cours…' : 'Lancer maintenant'}
                      </button>

                      <button
                        type="button"
                        onClick={() => void startEditRoutine(routine)}
                        aria-label={`Modifier ${routine.nom}`}
                        className="rounded-lg border border-white/10 p-1.5 text-[#94a3b8] hover:border-white/25 hover:text-[#e2e8f0]"
                      >
                        <Edit3 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                </article>
              );
            })
          )}
        </section>

        {/* Panneau droit : Historique ou Éditeur */}
        <aside className="hud-panel flex min-h-[460px] flex-col overflow-hidden">
          <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setRightMode('historique')}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium',
                  rightMode === 'historique'
                    ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'text-[#94a3b8] hover:text-[#e2e8f0]',
                )}
              >
                <History className="h-3.5 w-3.5" />
                Historique
              </button>
              <button
                type="button"
                onClick={() => {
                  if (selectedRoutine && !editingNew) {
                    void startEditRoutine(selectedRoutine);
                  } else {
                    startNewRoutine();
                  }
                }}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium',
                  rightMode === 'editeur'
                    ? 'bg-[var(--accent)]/15 text-[var(--accent)]'
                    : 'text-[#94a3b8] hover:text-[#e2e8f0]',
                )}
              >
                <Edit3 className="h-3.5 w-3.5" />
                Éditeur
              </button>
            </div>
            {selectedRoutine && rightMode === 'historique' && (
              <span className="truncate font-mono text-[11px] text-[#94a3b8]">
                {selectedRoutine.nom}
              </span>
            )}
          </div>

          {rightMode === 'historique' ? (
            <div className="flex-1 space-y-3 overflow-y-auto p-4">
              {!selectedRoutine ? (
                <p className="py-8 text-center text-xs text-[#64748b]">
                  Sélectionnez une routine pour consulter ses exécutions.
                </p>
              ) : loadingRuns ? (
                <div className="h-32 animate-pulse rounded-xl bg-white/5" />
              ) : runs.length === 0 ? (
                <p className="py-8 text-center text-xs text-[#64748b]">
                  Aucune exécution enregistrée pour « {selectedRoutine.nom} ».
                </p>
              ) : (
                <>
                  <ul className="space-y-1.5">
                    {runs.map((run) => (
                      <li key={run.id}>
                        <button
                          type="button"
                          onClick={() => setSelectedRun(run)}
                          className={cn(
                            'flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-xs transition',
                            selectedRun?.id === run.id
                              ? 'border-[var(--accent)]/50 bg-[var(--accent)]/10'
                              : 'border-white/10 bg-[#05070d]/60 hover:bg-white/[0.04]',
                          )}
                        >
                          <span className="font-mono text-[11px] text-[#e2e8f0]">
                            #{run.id} · {formatDateTimeFr(run.debut)}
                          </span>
                          <span className="flex items-center gap-2 font-mono text-[11px]">
                            <span className="text-[#94a3b8]">
                              {formatRunDuration(run.debut, run.fin)}
                            </span>
                            <span
                              className={cn(
                                run.statut === 'ok'
                                  ? 'text-[#34d399]'
                                  : run.statut === 'erreur'
                                    ? 'text-[#f87171]'
                                    : 'text-[var(--accent)]',
                              )}
                            >
                              {run.statut}
                            </span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>

                  {selectedRun && (
                    <div className="rounded-xl border border-white/10 bg-[#05070d]/80 p-3 space-y-2">
                      <div className="flex items-center justify-between font-mono text-[11px] text-[#94a3b8]">
                        <span>Sortie du run #{selectedRun.id}</span>
                        {selectedRun.sortie_path && (
                          <span className="truncate">{selectedRun.sortie_path}</span>
                        )}
                      </div>
                      {selectedRun.erreur ? (
                        <p className="text-xs text-[#f87171]">{selectedRun.erreur}</p>
                      ) : selectedRun.contenu_md || selectedRun.resume ? (
                        <MarkdownContent
                          content={
                            selectedRun.contenu_md || selectedRun.resume || ''
                          }
                          className="text-xs"
                        />
                      ) : (
                        <p className="text-xs text-[#64748b]">
                          Aucun contenu Markdown enregistré.
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          ) : (
            <form
              onSubmit={(e) => void handleSaveForm(e)}
              className="flex-1 space-y-3 overflow-y-auto p-4"
            >
              <label className="block text-xs text-[#94a3b8]">
                Nom
                <input
                  type="text"
                  required
                  value={form.nom}
                  onChange={(e) => setForm({ ...form, nom: e.target.value })}
                  placeholder="Brief du matin"
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <div className="rounded-xl border border-white/10 bg-[#05070d]/60 p-3 space-y-2">
                <label className="block text-xs text-[#94a3b8]">
                  Expression cron (5 champs)
                  <input
                    type="text"
                    required
                    value={form.cron}
                    onChange={(e) => setForm({ ...form, cron: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 font-mono text-xs text-[#e2e8f0]"
                  />
                </label>
                <p className="text-[11px] text-[var(--accent)]">
                  {translateCronToFrench(form.cron)}
                </p>

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <input
                    type="time"
                    value={presetHour}
                    onChange={(e) => setPresetHour(e.target.value)}
                    aria-label="Heure du préréglage cron"
                    className="rounded border border-white/10 bg-[#05070d] px-2 py-1 font-mono text-[11px] text-[#e2e8f0]"
                  />
                  <button
                    type="button"
                    onClick={applyDailyPreset}
                    className="rounded border border-white/15 bg-white/5 px-2 py-1 text-[11px] text-[#e2e8f0] hover:border-[var(--accent)]"
                  >
                    Chaque jour à…
                  </button>
                  <select
                    value={presetDay}
                    onChange={(e) => setPresetDay(e.target.value)}
                    aria-label="Jour du préréglage hebdomadaire"
                    className="rounded border border-white/10 bg-[#05070d] px-2 py-1 text-[11px] text-[#e2e8f0]"
                  >
                    <option value="1">Lundi</option>
                    <option value="2">Mardi</option>
                    <option value="3">Mercredi</option>
                    <option value="4">Jeudi</option>
                    <option value="5">Vendredi</option>
                    <option value="6">Samedi</option>
                    <option value="0">Dimanche</option>
                  </select>
                  <button
                    type="button"
                    onClick={applyWeeklyPreset}
                    className="rounded border border-white/15 bg-white/5 px-2 py-1 text-[11px] text-[#e2e8f0] hover:border-[var(--accent)]"
                  >
                    Chaque semaine…
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="block text-xs text-[#94a3b8]">
                  Agent
                  <select
                    value={form.agent}
                    onChange={(e) => setForm({ ...form, agent: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                  >
                    {agents.length > 0 ? (
                      agents.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.nom}
                        </option>
                      ))
                    ) : (
                      <option value="jarvis">Jarvis</option>
                    )}
                  </select>
                </label>

                <label className="block text-xs text-[#94a3b8]">
                  Skill (optionnel)
                  <select
                    value={form.skill ?? ''}
                    onChange={(e) => setForm({ ...form, skill: e.target.value })}
                    className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                  >
                    <option value="">Aucun</option>
                    {skills.map((s) => (
                      <option key={s.nom} value={s.nom}>
                        {s.nom}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label className="block text-xs text-[#94a3b8]">
                Chemin de sortie (cerveau)
                <input
                  type="text"
                  value={form.sortie}
                  onChange={(e) => setForm({ ...form, sortie: e.target.value })}
                  placeholder="04-Analytics-Resultats/{date}-brief.md"
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 font-mono text-xs text-[#e2e8f0]"
                />
              </label>

              <label className="block text-xs text-[#94a3b8]">
                Description
                <input
                  type="text"
                  value={form.description}
                  onChange={(e) =>
                    setForm({ ...form, description: e.target.value })
                  }
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <label className="block text-xs text-[#94a3b8]">
                Consigne envoyée à l&apos;agent
                <textarea
                  rows={5}
                  value={form.consigne ?? ''}
                  onChange={(e) => setForm({ ...form, consigne: e.target.value })}
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] p-2.5 text-xs text-[#e2e8f0]"
                />
              </label>

              <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
                {!editingNew && form.id ? (
                  <button
                    type="button"
                    onClick={() => void handleDeleteRoutine(form)}
                    className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-[#f87171] hover:border-[#f87171]/50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Supprimer
                  </button>
                ) : (
                  <span />
                )}
                <button
                  type="submit"
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-3.5 py-1.5 text-xs font-semibold text-[var(--accent)] hover:bg-[var(--accent)]/30 disabled:opacity-50"
                >
                  <Save className="h-3.5 w-3.5" />
                  {saving ? 'Enregistrement…' : 'Enregistrer'}
                </button>
              </div>
            </form>
          )}
        </aside>
      </div>
    </div>
  );
}
