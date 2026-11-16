import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  ArrowDown,
  ArrowUp,
  CheckSquare,
  ExternalLink,
  Mic,
  MicOff,
  PinOff,
  Plus,
  Send,
  SlidersHorizontal,
  Square,
  Trash2,
  X,
} from 'lucide-react';
import { AgentAvatar } from '@/components/AgentAvatar';
import { HoloSphere } from '@/components/HoloSphere';
import { MarkdownContent } from '@/components/MarkdownContent';
import { api } from '@/lib/api';
import { cn } from '@/lib/cn';
import {
  EFFORT_OPTIONS,
  filterModelsByMoteur,
  formatHeureFr,
  getModelAvailability,
  MOTEUR_OPTIONS,
} from '@/lib/llm';
import type { Effort, EtatJarvis, MoteurId } from '@/lib/types';
import { useAgents } from '@/store/useAgents';
import { useCartes } from '@/store/useCartes';
import { getConversationId, useChats } from '@/store/useChats';
import { useJarvis } from '@/store/useJarvis';
import { useMoteurs } from '@/store/useMoteurs';
import { useParametres } from '@/store/useParametres';
import { useStats } from '@/store/useStats';
import { useTaches } from '@/store/useTaches';
import { useToasts } from '@/store/useToasts';
import { AUCUN_MESSAGE } from '@/store/useChats';

type WidgetId = 'notes' | 'taches' | 'routines' | 'usage' | 'projets' | 'souvenirs';

interface WidgetPref {
  id: WidgetId;
  label: string;
  enabled: boolean;
}

const WIDGETS_STORAGE_KEY = 'jarvis-accueil-widgets';

const DEFAULT_WIDGETS: WidgetPref[] = [
  { id: 'notes', label: 'Notes & liens', enabled: true },
  { id: 'taches', label: 'Tâches du jour', enabled: true },
  { id: 'routines', label: 'Routines 7 j', enabled: true },
  { id: 'usage', label: 'Usage IA 24 h', enabled: true },
  { id: 'projets', label: 'Projets actifs', enabled: true },
  { id: 'souvenirs', label: 'Souvenirs', enabled: true },
];

const ETAT_LABELS: Record<EtatJarvis, string> = {
  repos: 'En veille',
  ecoute: "Je t'écoute…",
  reflexion: 'Je réfléchis…',
  parole: 'Je parle',
  action: "J'agis",
};

const RADAR_BRANCHES: Array<{ key: string; label: string }> = [
  { key: '00-Profil', label: 'Profil' },
  { key: '01-Business-Projets', label: 'Projets' },
  { key: '02-Connaissances-Methodes', label: 'Savoir' },
  { key: '03-Outils-Workflows', label: 'Outils' },
  { key: '04-Analytics-Resultats', label: 'Analytics' },
  { key: '05-Erreurs-Lecons', label: 'Leçons' },
];

function loadWidgetPrefs(): WidgetPref[] {
  if (typeof window === 'undefined') return DEFAULT_WIDGETS;
  try {
    const raw = window.localStorage.getItem(WIDGETS_STORAGE_KEY);
    if (!raw) return DEFAULT_WIDGETS;
    const parsed = JSON.parse(raw) as WidgetPref[];
    if (!Array.isArray(parsed)) return DEFAULT_WIDGETS;
    const validIds = new Set(DEFAULT_WIDGETS.map((w) => w.id));
    const filtered = parsed.filter(
      (p) => p && validIds.has(p.id) && typeof p.enabled === 'boolean',
    );
    for (const def of DEFAULT_WIDGETS) {
      if (!filtered.some((f) => f.id === def.id)) {
        filtered.push(def);
      }
    }
    return filtered.map((f) => ({
      ...f,
      label: DEFAULT_WIDGETS.find((d) => d.id === f.id)?.label ?? f.label,
    }));
  } catch {
    return DEFAULT_WIDGETS;
  }
}

/**
 * Radar SVG hexagonal simple reflétant le nombre de notes par branche du second cerveau.
 */
function BrainRadar({ branches }: { branches: Record<string, number> }) {
  const cx = 100;
  const cy = 92;
  const maxR = 56;
  const values = RADAR_BRANCHES.map((b) => branches[b.key] ?? 0);
  const peak = Math.max(1, ...values);
  const total = RADAR_BRANCHES.length;

  const pointAt = (index: number, radius: number) => {
    const angle = -Math.PI / 2 + (index * Math.PI * 2) / total;
    return {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    };
  };

  const ringPoints = (ratio: number) =>
    RADAR_BRANCHES.map((_, idx) => {
      const p = pointAt(idx, maxR * ratio);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    }).join(' ');

  const dataPoints = RADAR_BRANCHES.map((_, idx) => {
    const ratio = Math.max(0.1, values[idx] / peak);
    const p = pointAt(idx, maxR * ratio);
    return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
  }).join(' ');

  return (
    <svg
      viewBox="0 0 200 184"
      className="mx-auto h-44 w-full max-w-[220px]"
      role="img"
      aria-label="Radar d'activité du second cerveau par branche"
    >
      {[0.33, 0.66, 1].map((r) => (
        <polygon
          key={r}
          points={ringPoints(r)}
          fill="none"
          stroke="rgba(148,163,184,0.16)"
          strokeWidth="1"
        />
      ))}
      {RADAR_BRANCHES.map((b, idx) => {
        const edge = pointAt(idx, maxR);
        const labelPos = pointAt(idx, maxR + 16);
        return (
          <g key={b.key}>
            <line
              x1={cx}
              y1={cy}
              x2={edge.x}
              y2={edge.y}
              stroke="rgba(148,163,184,0.14)"
              strokeWidth="1"
            />
            <text
              x={labelPos.x}
              y={labelPos.y}
              textAnchor="middle"
              dominantBaseline="middle"
              fill="#94a3b8"
              fontSize="8.5"
            >
              {b.label} ({values[idx]})
            </text>
          </g>
        );
      })}
      <polygon
        points={dataPoints}
        fill="var(--accent)"
        fillOpacity="0.22"
        stroke="var(--accent)"
        strokeWidth="1.6"
      />
    </svg>
  );
}

/**
 * Vue principale `#/accueil` en grille 3 colonnes :
 * - Gauche : widgets métriques personnalisables (`/api/stats`)
 * - Centre : sphère holographique, dictée micro (`Ctrl+Espace`), prompt Jarvis, 4 derniers échanges
 * - Droite : tâches du jour, équipe d'agents, cartes épinglées et radar du second cerveau.
 */
export function Accueil() {
  const navigate = useNavigate();
  const etatJarvis = useJarvis((s) => s.etat_jarvis);
  const narration = useJarvis((s) => s.narration);
  const parametres = useParametres((s) => s.parametres);
  const agents = useAgents((s) => s.agents);
  const modeles = useMoteurs((s) => s.modeles);
  const moteursStatut = useMoteurs((s) => s.moteursStatut);
  const stats = useStats((s) => s.stats);
  const fetchStats = useStats((s) => s.fetchStats);
  const taches = useTaches((s) => s.taches);
  const addTache = useTaches((s) => s.addTache);
  const toggleTache = useTaches((s) => s.toggleTache);
  const deleteTache = useTaches((s) => s.deleteTache);
  const cartes = useCartes((s) => s.cartes);
  const pinCarte = useCartes((s) => s.pinCarte);
  const addToast = useToasts((s) => s.addToast);

  const jarvisConvId = getConversationId('agent', 'jarvis');
  const messages = useChats((s) => s.messagesByConv[jarvisConvId] ?? AUCUN_MESSAGE);
  const streaming = useChats((s) => s.streamingByConv[jarvisConvId] ?? null);
  const loadMessages = useChats((s) => s.loadMessages);
  const sendMessage = useChats((s) => s.sendMessage);

  const [widgets, setWidgets] = useState<WidgetPref[]>(loadWidgetPrefs);
  const [customizingWidgets, setCustomizingWidgets] = useState(false);

  const [moteur, setMoteur] = useState<MoteurId>(parametres.moteur);
  const [modele, setModele] = useState<string>(parametres.modele);
  const [effort, setEffort] = useState<Effort>(parametres.effort);
  const [promptText, setPromptText] = useState('');
  const [sending, setSending] = useState(false);

  const [newTaskTitle, setNewTaskTitle] = useState('');

  // État enregistrement micro navigateur
  const [isRecording, setIsRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  useEffect(() => {
    setMoteur(parametres.moteur);
    setModele(parametres.modele);
    setEffort(parametres.effort);
  }, [parametres.moteur, parametres.modele, parametres.effort]);

  useEffect(() => {
    void loadMessages('agent', 'jarvis');
    void fetchStats();
    const timer = window.setInterval(() => {
      void fetchStats();
    }, 60000);
    return () => window.clearInterval(timer);
  }, [loadMessages, fetchStats]);

  useEffect(() => {
    try {
      window.localStorage.setItem(WIDGETS_STORAGE_KEY, JSON.stringify(widgets));
    } catch {
      // Stockage local indisponible : ignoré.
    }
  }, [widgets]);

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

  const handleSendText = useCallback(
    async (text: string, isVoice = false) => {
      const clean = text.trim();
      if (!clean || sending) return;
      setSending(true);
      try {
        await sendMessage({
          cible: 'agent',
          id: 'jarvis',
          message: clean,
          moteur,
          modele,
          effort,
          isVoice,
        });
        if (!isVoice) setPromptText('');
      } catch {
        // Erreur notifiée par toast global
      } finally {
        setSending(false);
      }
    },
    [sending, sendMessage, moteur, modele, effort],
  );

  const stopRecording = useCallback(() => {
    const rec = mediaRecorderRef.current;
    if (rec && rec.state !== 'inactive') {
      rec.stop();
    }
    setIsRecording(false);
  }, []);

  const startRecording = useCallback(async () => {
    setMicError(null);
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const msg = "L'API microphone n'est pas disponible dans ce navigateur.";
      setMicError(msg);
      addToast(msg, 'erreur');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (evt) => {
        if (evt.data.size > 0) {
          chunksRef.current.push(evt.data);
        }
      };

      recorder.onstop = () => {
        for (const track of stream.getTracks()) {
          track.stop();
        }
        streamRef.current = null;
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        });
        chunksRef.current = [];
        if (blob.size === 0) return;

        setTranscribing(true);
        const formData = new FormData();
        formData.append('audio', blob, 'dictation.webm');
        void api
          .upload<{ texte: string }>('/api/voix/transcrire', formData)
          .then((res) => {
            const texte = res?.texte?.trim() ?? '';
            if (texte) {
              return handleSendText(texte, true);
            }
            addToast('Aucune parole détectée.', 'avertissement');
            return undefined;
          })
          .catch(() => {
            // Erreur gérée par le toast API global
          })
          .finally(() => {
            setTranscribing(false);
          });
      };

      recorder.start();
      setIsRecording(true);
    } catch {
      const msg =
        "Accès au microphone refusé. Autorisez le micro dans le navigateur ou utilisez la saisie texte.";
      setMicError(msg);
      addToast(msg, 'avertissement');
    }
  }, [addToast, handleSendText]);

  const toggleRecording = useCallback(() => {
    if (isRecording) {
      stopRecording();
    } else {
      void startRecording();
    }
  }, [isRecording, startRecording, stopRecording]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.code === 'Space') {
        e.preventDefault();
        toggleRecording();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stop();
      }
      if (streamRef.current) {
        for (const track of streamRef.current.getTracks()) {
          track.stop();
        }
      }
    };
  }, [toggleRecording]);

  const toggleWidget = (id: WidgetId) => {
    setWidgets((prev) =>
      prev.map((w) => (w.id === id ? { ...w, enabled: !w.enabled } : w)),
    );
  };

  const moveWidget = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= widgets.length) return;
    setWidgets((prev) => {
      const next = [...prev];
      const [item] = next.splice(index, 1);
      next.splice(target, 0, item);
      return next;
    });
  };

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskTitle.trim()) return;
    try {
      await addTache(newTaskTitle);
      setNewTaskTitle('');
    } catch {
      // Erreur notifiée par toast global
    }
  };

  const effectiveSphereState: EtatJarvis = isRecording ? 'ecoute' : etatJarvis;
  const recentMessages = messages.slice(-4);
  const pinnedCartes = cartes.filter((c) => Boolean(c.epinglee));

  const renderWidget = (id: WidgetId) => {
    if (!stats) {
      return (
        <div key={id} className="hud-panel p-3.5">
          <div className="h-4 w-28 animate-pulse rounded bg-white/10" />
          <div className="mt-3 h-8 w-20 animate-pulse rounded bg-white/5" />
        </div>
      );
    }

    switch (id) {
      case 'notes':
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="hud-label">Notes &amp; liens</div>
            <div className="mt-2 flex items-baseline justify-between">
              <span className="font-mono text-2xl font-semibold text-[#f8fafc]">
                {stats.notes}
              </span>
              <span className="font-mono text-xs text-[var(--accent)]">
                {stats.liens} liens
              </span>
            </div>
            <svg viewBox="0 0 120 14" className="mt-2.5 h-3.5 w-full" aria-hidden="true">
              <rect x="0" y="5" width="120" height="4" rx="2" fill="rgba(148,163,184,0.15)" />
              <rect
                x="0"
                y="5"
                width={Math.min(120, Math.max(12, stats.notes * 2))}
                height="4"
                rx="2"
                fill="var(--accent)"
              />
            </svg>
          </div>
        );

      case 'taches': {
        const done = stats.taches_jour?.faites ?? 0;
        const total = stats.taches_jour?.total ?? 0;
        const pct = total > 0 ? Math.round((done / total) * 100) : 0;
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="hud-label">Tâches du jour</div>
            <div className="mt-2 flex items-baseline justify-between">
              <span className="font-mono text-2xl font-semibold text-[#f8fafc]">
                {done}/{total}
              </span>
              <span className="font-mono text-xs text-[#34d399]">{pct}%</span>
            </div>
            <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-white/10">
              <div
                className="h-full rounded-full bg-[#34d399] transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        );
      }

      case 'routines': {
        const ok = stats.runs_7j?.ok ?? 0;
        const err = stats.runs_7j?.erreur ?? 0;
        const sum = Math.max(1, ok + err);
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="flex items-center justify-between">
              <span className="hud-label">Routines 7 j</span>
              <span className="font-mono text-[11px] text-[#94a3b8]">
                {stats.routines_actives} actives
              </span>
            </div>
            <div className="mt-2 flex items-baseline gap-3 font-mono">
              <span className="text-xl font-semibold text-[#34d399]">{ok} ok</span>
              <span className="text-sm text-[#f87171]">{err} err</span>
            </div>
            <svg viewBox="0 0 120 12" className="mt-2.5 h-3 w-full" aria-hidden="true">
              <rect
                x="0"
                y="4"
                width={(ok / sum) * 120}
                height="4"
                rx="2"
                fill="#34d399"
              />
              <rect
                x={(ok / sum) * 120}
                y="4"
                width={(err / sum) * 120}
                height="4"
                rx="2"
                fill="#f87171"
              />
            </svg>
          </div>
        );
      }

      case 'usage': {
        const entries = Object.entries(stats.usage_llm_24h?.par_modele ?? {});
        const maxCalls = Math.max(1, ...entries.map(([, c]) => c));
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="flex items-center justify-between">
              <span className="hud-label">Usage IA 24 h</span>
              <span className="font-mono text-[11px] text-[#94a3b8]">
                {(stats.usage_llm_24h?.tokens ?? 0).toLocaleString('fr-FR')} tok
              </span>
            </div>
            <div className="mt-1.5 font-mono text-lg font-semibold text-[#f8fafc]">
              {stats.usage_llm_24h?.appels ?? 0} appels
            </div>
            {entries.length > 0 ? (
              <div className="mt-2 space-y-1.5">
                {entries.slice(0, 4).map(([mod, count]) => (
                  <div key={mod} className="space-y-0.5">
                    <div className="flex justify-between font-mono text-[10px] text-[#94a3b8]">
                      <span className="truncate">{mod}</span>
                      <span>{count}</span>
                    </div>
                    <div className="h-1 w-full rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-[var(--accent)]"
                        style={{ width: `${Math.round((count / maxCalls) * 100)}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-[11px] text-[#64748b]">Aucun appel sur 24 h</p>
            )}
          </div>
        );
      }

      case 'projets':
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="hud-label">Projets actifs</div>
            <div className="mt-2 flex items-baseline justify-between">
              <span className="font-mono text-2xl font-semibold text-[#f8fafc]">
                {stats.projets_actifs}
              </span>
              <Link
                to="/projets"
                className="text-xs text-[var(--accent)] hover:underline"
              >
                Voir
              </Link>
            </div>
          </div>
        );

      case 'souvenirs':
        return (
          <div key={id} className="hud-panel p-3.5">
            <div className="hud-label">Souvenirs</div>
            <div className="mt-2 flex items-baseline justify-between">
              <span className="font-mono text-2xl font-semibold text-[#f8fafc]">
                {stats.souvenirs}
              </span>
              <Link
                to="/parametres/memoire"
                className="text-xs text-[var(--accent)] hover:underline"
              >
                Gérer
              </Link>
            </div>
          </div>
        );
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)_360px]">
      {/* Colonne gauche : Widgets métriques personnalisables */}
      <section className="min-w-0 space-y-3" aria-label="Métriques du système">
        <div className="flex items-center justify-between px-1">
          <h2 className="hud-label font-semibold">Télémétrie</h2>
          <button
            type="button"
            onClick={() => setCustomizingWidgets((prev) => !prev)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-[#0b1020] px-2.5 py-1 text-[11px] text-[#94a3b8] transition hover:border-[var(--accent)]/50 hover:text-[#e2e8f0]"
          >
            <SlidersHorizontal className="h-3 w-3" />
            Personnaliser
          </button>
        </div>

        {customizingWidgets && (
          <div className="hud-glass p-3 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-[#f8fafc]">
                Widgets affichés
              </span>
              <button
                type="button"
                onClick={() => setCustomizingWidgets(false)}
                aria-label="Fermer la personnalisation"
                className="rounded p-0.5 text-[#94a3b8] hover:text-[#e2e8f0]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <ul className="space-y-1.5">
              {widgets.map((w, idx) => (
                <li
                  key={w.id}
                  className="flex items-center justify-between gap-2 rounded-lg bg-[#05070d]/60 px-2.5 py-1.5 text-xs"
                >
                  <label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="checkbox"
                      checked={w.enabled}
                      onChange={() => toggleWidget(w.id)}
                      className="accent-[var(--accent)]"
                    />
                    <span>{w.label}</span>
                  </label>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => moveWidget(idx, -1)}
                      disabled={idx === 0}
                      aria-label={`Monter ${w.label}`}
                      className="rounded p-1 text-[#94a3b8] hover:bg-white/10 disabled:opacity-30"
                    >
                      <ArrowUp className="h-3 w-3" />
                    </button>
                    <button
                      type="button"
                      onClick={() => moveWidget(idx, 1)}
                      disabled={idx === widgets.length - 1}
                      aria-label={`Descendre ${w.label}`}
                      className="rounded p-1 text-[#94a3b8] hover:bg-white/10 disabled:opacity-30"
                    >
                      <ArrowDown className="h-3 w-3" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">
          {widgets.filter((w) => w.enabled).map((w) => renderWidget(w.id))}
        </div>
      </section>

      {/* Colonne centre : Sphère holographique + micro + commande rapide + 4 derniers échanges */}
      <section className="min-w-0 space-y-4" aria-label="Interface principale Jarvis">
        <div className="hud-panel flex flex-col items-center px-4 py-5">
          <div className="h-60 w-full max-w-[340px] sm:h-72">
            <HoloSphere
              etat={effectiveSphereState}
              accentColor={parametres.accent}
            />
          </div>

          <div className="mt-1 text-center">
            <p className="font-display text-base font-semibold tracking-wide text-[#f8fafc]">
              {ETAT_LABELS[effectiveSphereState]}
            </p>
            <p className="mt-1 min-h-[1.25rem] max-w-md text-xs text-[#94a3b8]">
              {narration?.texte ?? 'Prêt à recevoir vos instructions.'}
            </p>
          </div>

          {/* Bouton micro + raccourci Ctrl+Espace */}
          <div className="mt-4 flex flex-col items-center gap-2">
            <button
              type="button"
              onClick={toggleRecording}
              disabled={transcribing}
              aria-label={
                isRecording
                  ? "Arrêter l'écoute vocale"
                  : 'Parler à Jarvis (Ctrl+Espace)'
              }
              className={cn(
                'flex h-13 w-13 items-center justify-center rounded-full border transition',
                isRecording
                  ? 'border-[#f87171] bg-[#f87171]/20 text-[#f87171] shadow-lg shadow-[#f87171]/25'
                  : 'border-[var(--accent)]/60 bg-[var(--accent)]/15 text-[var(--accent)] hover:bg-[var(--accent)]/25',
                transcribing && 'opacity-60',
              )}
            >
              {isRecording ? (
                <Square className="h-5 w-5" />
              ) : micError ? (
                <MicOff className="h-5 w-5" />
              ) : (
                <Mic className="h-5 w-5" />
              )}
            </button>
            <span className="font-mono text-[10px] text-[#64748b]">
              {transcribing
                ? 'Transcription en cours…'
                : isRecording
                  ? 'Enregistrement… cliquez pour envoyer'
                  : 'Ctrl+Espace pour parler'}
            </span>
            {micError && (
              <p className="max-w-sm text-center text-xs text-[#f59e0b]">
                {micError}
              </p>
            )}
          </div>

          {/* Saisie et sélecteurs moteur / modèle / effort */}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void handleSendText(promptText);
            }}
            className="mt-4 w-full space-y-2.5"
          >
            <div className="flex gap-2">
              <input
                type="text"
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                placeholder="Demander quelque chose à Jarvis…"
                aria-label="Message pour Jarvis"
                className="min-w-0 flex-1 rounded-xl border border-white/10 bg-[#05070d]/90 px-3.5 py-2.5 text-sm text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[var(--accent)]/60 focus:outline-none"
              />
              <button
                type="submit"
                disabled={sending || !promptText.trim()}
                aria-label="Envoyer à Jarvis"
                className="inline-flex shrink-0 items-center justify-center rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/20 px-4 text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              <label className="block text-[11px] text-[#94a3b8]">
                Moteur
                <select
                  value={moteur}
                  onChange={(e) => setMoteur(e.target.value as MoteurId)}
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2 py-1.5 text-xs text-[#e2e8f0]"
                >
                  {MOTEUR_OPTIONS.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-[11px] text-[#94a3b8]">
                Modèle
                <select
                  value={modele}
                  onChange={(e) => setModele(e.target.value)}
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2 py-1.5 text-xs text-[#e2e8f0]"
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
              </label>

              <label className="block text-[11px] text-[#94a3b8]">
                Effort
                <select
                  value={effort}
                  onChange={(e) => setEffort(e.target.value as Effort)}
                  className="mt-1 w-full rounded-lg border border-white/10 bg-[#05070d] px-2 py-1.5 text-xs text-[#e2e8f0]"
                >
                  {EFFORT_OPTIONS.map((opt) => (
                    <option key={opt.id} value={opt.id}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </form>
        </div>

        {/* Aperçu des 4 derniers échanges */}
        <div className="hud-panel p-4">
          <div className="flex items-center justify-between gap-2">
            <h2 className="hud-label font-semibold">Échanges récents</h2>
            <Link
              to="/agent/jarvis"
              className="inline-flex items-center gap-1 text-xs text-[var(--accent)] hover:underline"
            >
              Conversation complète
              <ExternalLink className="h-3 w-3" />
            </Link>
          </div>

          <div className="mt-3 space-y-2.5">
            {recentMessages.length === 0 && !streaming ? (
              <p className="py-4 text-center text-xs text-[#64748b]">
                Aucun échange récent avec Jarvis.
              </p>
            ) : (
              recentMessages.map((msg) => {
                const isUser = msg.role === 'user' || msg.auteur === 'utilisateur';
                return (
                  <div
                    key={String(msg.id)}
                    className={cn(
                      'rounded-xl border px-3 py-2 text-xs',
                      isUser
                        ? 'border-[var(--accent)]/25 bg-[var(--accent)]/8'
                        : 'border-white/10 bg-[#05070d]/70',
                    )}
                  >
                    <div className="mb-1 flex items-center justify-between font-mono text-[10px] text-[#94a3b8]">
                      <span>{isUser ? 'Vous' : msg.auteur}</span>
                      <span>{formatHeureFr(msg.cree_le)}</span>
                    </div>
                    <MarkdownContent content={msg.contenu} className="text-xs" />
                  </div>
                );
              })
            )}

            {streaming && (
              <div className="rounded-xl border border-[var(--accent)]/35 bg-[#05070d]/85 px-3 py-2 text-xs">
                <div className="mb-1 font-mono text-[10px] text-[var(--accent)]">
                  {streaming.auteur || 'jarvis'} · en direct
                </div>
                <MarkdownContent
                  content={`${streaming.contenu}▍`}
                  className="text-xs"
                />
              </div>
            )}
          </div>
        </div>
      </section>

      {/* Colonne droite : Tâches du jour, Ton équipe, Cartes & radar */}
      <section className="min-w-0 space-y-4" aria-label="Organisation quotidienne">
        {/* Tâches du jour */}
        <div className="hud-panel p-4">
          <div className="flex items-center justify-between">
            <h2 className="hud-label font-semibold">Tâches du jour</h2>
            <CheckSquare className="h-4 w-4 text-[var(--accent)]" />
          </div>

          <form onSubmit={(e) => void handleAddTask(e)} className="mt-3 flex gap-2">
            <input
              type="text"
              value={newTaskTitle}
              onChange={(e) => setNewTaskTitle(e.target.value)}
              placeholder="Ajouter une tâche…"
              aria-label="Nouvelle tâche du jour"
              className="min-w-0 flex-1 rounded-lg border border-white/10 bg-[#05070d] px-2.5 py-1.5 text-xs text-[#e2e8f0] placeholder:text-[#64748b] focus:border-[var(--accent)]/60 focus:outline-none"
            />
            <button
              type="submit"
              aria-label="Ajouter la tâche"
              className="inline-flex shrink-0 items-center justify-center rounded-lg border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-2.5 text-[var(--accent)] hover:bg-[var(--accent)]/25"
            >
              <Plus className="h-4 w-4" />
            </button>
          </form>

          <div className="mt-3 space-y-1.5">
            {taches.length === 0 ? (
              <p className="py-3 text-center text-xs text-[#64748b]">
                Aucune tâche pour aujourd&apos;hui
              </p>
            ) : (
              taches.map((t) => {
                const checked = Boolean(t.fait);
                return (
                  <div
                    key={t.id}
                    className="flex items-center justify-between gap-2 rounded-lg border border-white/[0.06] bg-[#05070d]/60 px-2.5 py-1.5"
                  >
                    <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => void toggleTache(t.id, !checked)}
                        className="accent-[var(--accent)]"
                      />
                      <span
                        className={cn(
                          'truncate',
                          checked ? 'text-[#64748b] line-through' : 'text-[#e2e8f0]',
                        )}
                      >
                        {t.titre}
                      </span>
                    </label>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {t.source === 'brief' && (
                        <span className="rounded bg-[var(--accent)]/15 px-1.5 py-0.5 font-mono text-[10px] text-[var(--accent)]">
                          brief
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={() => void deleteTache(t.id)}
                        aria-label={`Supprimer la tâche ${t.titre}`}
                        className="rounded p-1 text-[#64748b] hover:bg-white/10 hover:text-[#f87171]"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Ton équipe */}
        <div className="hud-panel p-4">
          <h2 className="hud-label font-semibold">Ton équipe</h2>
          {agents.length === 0 ? (
            <p className="mt-3 text-center text-xs text-[#64748b]">
              Aucun agent configuré
            </p>
          ) : (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-2">
              {agents.map((agent) => (
                <button
                  key={agent.id}
                  type="button"
                  onClick={() => navigate(`/agent/${encodeURIComponent(agent.id)}`)}
                  className="flex items-center gap-2.5 rounded-xl border border-white/[0.07] bg-[#05070d]/60 p-2 text-left transition hover:border-[var(--accent)]/40 hover:bg-white/[0.04]"
                >
                  <AgentAvatar avatar={agent.avatar} size="sm" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-xs font-medium text-[#f8fafc]">
                      {agent.nom}
                    </div>
                    <div className="truncate font-mono text-[10px] text-[#94a3b8]">
                      {agent.avatar.etat}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Cartes & radar */}
        <div className="hud-panel p-4 space-y-3">
          <h2 className="hud-label font-semibold">Cartes &amp; radar</h2>

          {pinnedCartes.length === 0 ? (
            <p className="text-center text-xs text-[#64748b]">
              Aucune carte épinglée
            </p>
          ) : (
            <div className="space-y-2">
              {pinnedCartes.map((carte) => (
                <div
                  key={carte.id}
                  className="rounded-xl border border-white/10 bg-[#05070d]/70 p-3"
                >
                  <div className="mb-1.5 flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-[#f8fafc]">
                      {carte.titre}
                    </span>
                    <button
                      type="button"
                      onClick={() => void pinCarte(carte.id, false)}
                      aria-label={`Retirer la carte ${carte.titre}`}
                      title="Retirer des épingles"
                      className="rounded p-1 text-[#94a3b8] hover:bg-white/10 hover:text-[#e2e8f0]"
                    >
                      <PinOff className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <MarkdownContent content={carte.contenu_md} className="text-xs" />
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-white/10 pt-2">
            <BrainRadar branches={stats?.branches ?? {}} />
          </div>
        </div>
      </section>
    </div>
  );
}
