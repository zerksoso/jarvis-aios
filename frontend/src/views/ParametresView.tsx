import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, NavLink, useParams } from 'react-router-dom';
import {
  CheckCircle2,
  Cpu,
  Database,
  Download,
  ExternalLink,
  Info,
  LoaderCircle,
  Mic,
  Palette,
  Play,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  XCircle,
} from 'lucide-react';
import type { MajMode } from '@/api/maj';
import { api, getToken } from '@/lib/api';
import { cn } from '@/lib/cn';
import {
  EFFORT_OPTIONS,
  getModelAvailability,
  MOTEUR_OPTIONS,
} from '@/lib/llm';
import type {
  DeclenchementVoix,
  Effort,
  Memoire,
  MoteurId,
  Parametres,
  PontWebOuvrirReponse,
  SttModele,
  VoixEtat,
  VoixMicro,
  WebNavigateurVoix,
  WebServiceVoix,
} from '@/lib/types';
import { useMaj } from '@/store/useMaj';
import { useMoteurs } from '@/store/useMoteurs';
import { type ParametresPatch, useParametres } from '@/store/useParametres';
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
  { id: 'fr-FR-HenriNeural', label: 'Henri (fr-FR-HenriNeural)' },
  { id: 'fr-FR-DeniseNeural', label: 'Denise (fr-FR-DeniseNeural)' },
];

type VoixPresetId = 'edge-henri' | 'edge-denise' | 'windows' | 'http' | 'kyutai';

const VOIX_PRESETS: Array<{
  id: VoixPresetId;
  label: string;
  desc: string;
}> = [
  {
    id: 'edge-henri',
    label: 'Henri (fr-FR-HenriNeural)',
    desc: 'Voix Edge-TTS naturelle masculine (par défaut).',
  },
  {
    id: 'edge-denise',
    label: 'Denise (fr-FR-DeniseNeural)',
    desc: 'Voix Edge-TTS naturelle féminine.',
  },
  {
    id: 'kyutai',
    label: 'Kyutai (locale, très naturelle)',
    desc: 'Voix neuronale française sur ton GPU, hors ligne. Se lance toute seule ; Edge sert de secours le temps du chargement.',
  },
  {
    id: 'windows',
    label: 'Windows locale (SAPI5)',
    desc: 'Synthèse locale 100 % hors ligne via pyttsx3.',
  },
  {
    id: 'http',
    label: 'Serveur local HTTP',
    desc: 'Serveur compatible OpenAI (/v1/audio/speech, ex. KugelAudio) avec repli.',
  },
];

const STT_MODELES: Array<{
  id: SttModele;
  label: string;
  taille: string;
  note: string;
}> = [
  {
    id: 'large-v3-turbo',
    label: 'Large v3 Turbo',
    taille: '~1,6 Go',
    note: 'Précision maximale et rapide avec GPU CUDA (recommandé NVIDIA).',
  },
  {
    id: 'medium',
    label: 'Medium',
    taille: '~1,5 Go',
    note: 'Très bonne précision, plus exigeant sur CPU seul.',
  },
  {
    id: 'small',
    label: 'Small',
    taille: '~500 Mo',
    note: 'Léger et fluide sur processeur (CPU) sans carte graphique dédiée.',
  },
];

const DECLENCHEMENT_OPTIONS: Array<{
  id: DeclenchementVoix;
  label: string;
  desc: string;
}> = [
  {
    id: 'push',
    label: 'Push-to-talk',
    desc: 'Parler via le raccourci (Ctrl+Espace) ou le bouton micro.',
  },
  {
    id: 'continu',
    label: 'Conversation continue',
    desc: 'Écoute mains libres avec détection de parole et coupure instantanée.',
  },
];

const WEB_SERVICES_OPTIONS: Array<{
  id: WebServiceVoix;
  label: string;
  url: string;
  desc: string;
}> = [
  {
    id: 'chatgpt',
    label: 'ChatGPT',
    url: 'https://chatgpt.com/',
    desc: 'Ouvre ChatGPT dans son interface dédiée avec ta session habituelle.',
  },
  {
    id: 'gemini',
    label: 'Gemini',
    url: 'https://gemini.google.com/app',
    desc: 'Ouvre Google Gemini dans son interface dédiée avec ton compte Google.',
  },
];

const WEB_NAVIGATEUR_OPTIONS: Array<{
  id: WebNavigateurVoix;
  label: string;
  desc: string;
}> = [
  {
    id: 'defaut',
    label: 'Navigateur par défaut',
    desc: 'Ouvre le site avec ton navigateur Windows par défaut.',
  },
  {
    id: 'chrome',
    label: 'Google Chrome (--app)',
    desc: 'Fenêtre épurée dédiée avec ton profil Chrome déjà connecté.',
  },
  {
    id: 'edge',
    label: 'Microsoft Edge (--app)',
    desc: 'Fenêtre épurée dédiée avec ton profil Edge déjà connecté.',
  },
];

/**
 * Déduit l'identifiant de préréglage TTS affiché dans la section « Voix & Audio ».
 */
function getVoixPresetId(voix: Parametres['voix']): VoixPresetId {
  if (voix.moteur_tts === 'kyutai') return 'kyutai';
  if (voix.moteur_tts === 'windows') return 'windows';
  if (voix.moteur_tts === 'http') return 'http';
  return voix.nom === 'fr-FR-DeniseNeural' ? 'edge-denise' : 'edge-henri';
}

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
 * Vue `#/parametres/:section` : Personnalisation, Voix & Audio, Mémoire, Modèles par défaut et Mises à jour.
 */
export function ParametresView() {
  const { section = 'personnalisation' } = useParams<{ section?: string }>();
  const activeSection =
    section === 'voix' ||
    section === 'memoire' ||
    section === 'modeles' ||
    section === 'maj'
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

  // État section Voix & Audio
  const [micros, setMicros] = useState<VoixMicro[]>([]);
  const [loadingMicros, setLoadingMicros] = useState(false);
  const [voixEtat, setVoixEtat] = useState<VoixEtat | null>(null);
  const [loadingVoixEtat, setLoadingVoixEtat] = useState(false);
  const [testingVoice, setTestingVoice] = useState(false);
  const [openingWeb, setOpeningWeb] = useState(false);
  const testAudioRef = useRef<HTMLAudioElement | null>(null);
  const testAudioUrlRef = useRef<string | null>(null);

  // État section Mémoire
  const [memoires, setMemoires] = useState<Memoire[]>([]);
  const [loadingMemoires, setLoadingMemoires] = useState(false);
  const [newMemType, setNewMemType] = useState<Memoire['type']>('preference');
  const [newMemTexte, setNewMemTexte] = useState('');

  useEffect(() => {
    setDraft(parametres);
  }, [parametres]);

  const stopTestAudio = useCallback(() => {
    if (testAudioRef.current) {
      testAudioRef.current.pause();
      testAudioRef.current.onended = null;
      testAudioRef.current.onerror = null;
      testAudioRef.current = null;
    }
    if (testAudioUrlRef.current) {
      URL.revokeObjectURL(testAudioUrlRef.current);
      testAudioUrlRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopTestAudio();
    };
  }, [stopTestAudio]);

  const loadVoixInfos = useCallback(async () => {
    setLoadingMicros(true);
    setLoadingVoixEtat(true);
    const [microsRes, etatRes] = await Promise.allSettled([
      api.get<VoixMicro[]>('/api/voix/micros', { silent: true }),
      api.get<VoixEtat>('/api/voix/etat', { silent: true }),
    ]);
    if (microsRes.status === 'fulfilled' && Array.isArray(microsRes.value)) {
      setMicros(microsRes.value);
    } else {
      setMicros([]);
    }
    if (etatRes.status === 'fulfilled' && etatRes.value) {
      setVoixEtat(etatRes.value);
    }
    setLoadingMicros(false);
    setLoadingVoixEtat(false);
  }, []);

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
    if (activeSection === 'voix') {
      void loadVoixInfos();
    } else if (activeSection === 'memoire') {
      void loadMemoires();
    } else if (activeSection === 'modeles') {
      void fetchMoteurs();
    } else if (activeSection === 'maj') {
      void fetchMajEtat({ silent: true });
    }
  }, [activeSection, loadVoixInfos, loadMemoires, fetchMoteurs, fetchMajEtat]);

  const savePartial = async (partiel: ParametresPatch, successMsg: string) => {
    try {
      await updateParametres(partiel);
      addToast(successMsg, 'succes');
      return true;
    } catch {
      // Erreur notifiée par toast global
      return false;
    }
  };

  const handleSelectVoixPreset = (presetId: VoixPresetId) => {
    setDraft((prev) => {
      const nextVoix = { ...prev.voix };
      if (presetId === 'edge-henri') {
        nextVoix.moteur_tts = 'edge';
        nextVoix.nom = 'fr-FR-HenriNeural';
      } else if (presetId === 'edge-denise') {
        nextVoix.moteur_tts = 'edge';
        nextVoix.nom = 'fr-FR-DeniseNeural';
      } else if (presetId === 'kyutai') {
        nextVoix.moteur_tts = 'kyutai';
        nextVoix.nom = 'defaut';
      } else if (presetId === 'windows') {
        nextVoix.moteur_tts = 'windows';
      } else {
        nextVoix.moteur_tts = 'http';
      }
      return { ...prev, voix: nextVoix };
    });
  };

  const handleSaveVoix = async () => {
    const cleanUrl = (draft.voix.tts_url ?? '').trim();
    if (
      draft.voix.moteur_tts === 'http' &&
      (!cleanUrl || (!cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')))
    ) {
      addToast(
        'Indique une URL http:// ou https:// valide pour le serveur TTS local.',
        'erreur',
      );
      return;
    }
    const ok = await savePartial(
      {
        voix: {
          ...draft.voix,
          tts_url: cleanUrl || null,
          micro: draft.voix.micro?.trim() || null,
        },
      },
      'Paramètres voix & audio enregistrés.',
    );
    if (ok) {
      void loadVoixInfos();
    }
  };

  const handleTestVoice = async () => {
    const cleanUrl = (draft.voix.tts_url ?? '').trim();
    if (
      draft.voix.moteur_tts === 'http' &&
      (!cleanUrl || (!cleanUrl.startsWith('http://') && !cleanUrl.startsWith('https://')))
    ) {
      addToast(
        'Indique une URL http:// ou https:// valide avant de tester le serveur TTS.',
        'avertissement',
      );
      return;
    }

    stopTestAudio();
    setTestingVoice(true);
    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      const token = getToken();
      if (token) {
        headers['X-Jarvis-Token'] = token;
      }
      const response = await fetch('/api/voix/tester', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          voix: draft.voix.nom,
          moteur_tts: draft.voix.moteur_tts,
          tts_url: cleanUrl || null,
        }),
      });
      if (!response.ok) {
        let detail = `Échec du test vocal (HTTP ${response.status})`;
        try {
          const errData = (await response.json()) as { detail?: string };
          if (typeof errData?.detail === 'string' && errData.detail.trim()) {
            detail = errData.detail;
          }
        } catch {
          // Garde le message HTTP par défaut.
        }
        addToast(detail, 'erreur');
        return;
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const audio = new Audio(objectUrl);
      testAudioUrlRef.current = objectUrl;
      testAudioRef.current = audio;
      audio.onended = () => {
        if (testAudioUrlRef.current === objectUrl) {
          stopTestAudio();
        }
      };
      audio.onerror = () => {
        if (testAudioUrlRef.current === objectUrl) {
          stopTestAudio();
        }
        addToast("Impossible de lire l'audio renvoyé par le serveur.", 'erreur');
      };
      await audio.play();
    } catch (err) {
      const msg =
        err instanceof Error && err.message.trim()
          ? err.message
          : 'Impossible de tester la synthèse vocale.';
      addToast(msg, 'erreur');
    } finally {
      setTestingVoice(false);
    }
  };

  const handleOpenPontWeb = async () => {
    setOpeningWeb(true);
    try {
      const res = await api.post<PontWebOuvrirReponse>('/api/voix/web/ouvrir', {
        service: draft.voix.web_service,
        navigateur: draft.voix.web_navigateur,
      });
      const nomService = res?.service === 'gemini' ? 'Gemini' : 'ChatGPT';
      addToast(
        res?.action === 'premier_plan'
          ? `${nomService} ramené au premier plan.`
          : `${nomService} ouvert — lance le mode vocal dans la fenêtre.`,
        'succes',
      );
    } catch {
      // Erreur notifiée par toast global API
    } finally {
      setOpeningWeb(false);
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
      to: '/parametres/voix',
      id: 'voix',
      label: 'Voix & Audio',
      icon: Mic,
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

  const selectedVoixPreset = getVoixPresetId(draft.voix);
  const selectedMicroMissing =
    Boolean(draft.voix.micro) &&
    !micros.some((m) => m.nom === draft.voix.micro);

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

      {activeSection === 'voix' && (
        <section className="hud-panel space-y-6 p-5" aria-label="Voix et Audio">
          {/* État temps réel du pipeline vocal */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-[#05070d]/60 p-4">
            <div className="min-w-0 space-y-1 text-xs">
              <div className="hud-label">État du pipeline vocal</div>
              {loadingVoixEtat && !voixEtat ? (
                <div className="text-[#94a3b8]">Inspection du matériel vocal…</div>
              ) : voixEtat ? (
                <div className="flex flex-wrap items-center gap-2 text-[#e2e8f0]">
                  <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[11px]">
                    STT : {voixEtat.stt_modele}
                  </span>
                  <span
                    className={cn(
                      'rounded border px-2 py-0.5 font-mono text-[11px]',
                      voixEtat.stt_peripherique === 'cuda'
                        ? 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]'
                        : 'border-white/15 bg-white/5 text-[#cbd5e1]',
                    )}
                  >
                    {voixEtat.stt_peripherique.toUpperCase()}
                  </span>
                  <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[11px]">
                    {voixEtat.stt_charge ? 'Whisper chargé' : 'Whisper en veille'}
                  </span>
                  <span className="rounded border border-white/10 bg-white/5 px-2 py-0.5 font-mono text-[11px]">
                    TTS : {voixEtat.tts_moteur}
                  </span>
                  {voixEtat.tts_moteur === 'http' && (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 rounded border px-2 py-0.5 font-mono text-[11px]',
                        voixEtat.tts_http_joignable
                          ? 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]'
                          : 'border-[#f87171]/40 bg-[#f87171]/10 text-[#f87171]',
                      )}
                    >
                      {voixEtat.tts_http_joignable ? 'Serveur HTTP joignable' : 'Serveur HTTP injoignable'}
                    </span>
                  )}
                </div>
              ) : (
                <div className="text-[#94a3b8]">État vocal indisponible.</div>
              )}
            </div>

            <button
              type="button"
              onClick={() => void loadVoixInfos()}
              disabled={loadingVoixEtat || loadingMicros}
              className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-[#0b1020] px-3 py-1.5 text-xs text-[#cbd5e1] transition hover:border-[var(--accent)]/50 disabled:opacity-50"
            >
              <RefreshCw className={cn('h-3.5 w-3.5', loadingVoixEtat && 'animate-spin')} />
              Actualiser
            </button>
          </div>

          {/* Mode vocal */}
          <div className="space-y-4">
            <span className="hud-label block">Mode vocal</span>
            <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
              <button
                type="button"
                onClick={() =>
                  setDraft({
                    ...draft,
                    voix: { ...draft.voix, mode: 'local' },
                  })
                }
                className={cn(
                  'rounded-xl border p-3.5 text-left transition',
                  draft.voix.mode === 'local'
                    ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                    : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-[#f8fafc]">Pipeline local</span>
                  {draft.voix.mode === 'local' && (
                    <span className="rounded-full border border-[var(--accent)]/40 bg-[var(--accent)]/15 px-2 py-0.5 font-mono text-[10px] text-[var(--accent)]">
                      Actif
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-[#94a3b8]">
                  Transcription Whisper locale + synthèse vocale sans clé API payante.
                </p>
              </button>

              <button
                type="button"
                onClick={() =>
                  setDraft({
                    ...draft,
                    voix: { ...draft.voix, mode: 'web' },
                  })
                }
                className={cn(
                  'rounded-xl border p-3.5 text-left transition',
                  draft.voix.mode === 'web'
                    ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                    : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold text-[#f8fafc]">
                    Pont web (ChatGPT / Gemini)
                  </span>
                  {draft.voix.mode === 'web' && (
                    <span className="rounded-full border border-[var(--accent)]/40 bg-[var(--accent)]/15 px-2 py-0.5 font-mono text-[10px] text-[var(--accent)]">
                      Actif
                    </span>
                  )}
                </div>
                <p className="mt-1 text-[11px] text-[#94a3b8]">
                  Ouvre la fenêtre de ton service vocal web sur Ctrl+Espace ou clic sur l&apos;orbe.
                </p>
              </button>
            </div>

            {draft.voix.mode === 'web' && (
              <div className="space-y-4 rounded-xl border border-[var(--accent)]/30 bg-[#05070d]/70 p-4">
                <div className="space-y-2">
                  <span className="hud-label block">Service web</span>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {WEB_SERVICES_OPTIONS.map((svc) => (
                      <button
                        key={svc.id}
                        type="button"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            voix: { ...draft.voix, web_service: svc.id },
                          })
                        }
                        className={cn(
                          'rounded-xl border p-3 text-left transition',
                          draft.voix.web_service === svc.id
                            ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                            : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold text-[#f8fafc]">{svc.label}</span>
                          <span className="truncate font-mono text-[10px] text-[#64748b]">
                            {svc.url}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] text-[#94a3b8]">{svc.desc}</p>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-2">
                  <span className="hud-label block">Navigateur</span>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                    {WEB_NAVIGATEUR_OPTIONS.map((nav) => (
                      <button
                        key={nav.id}
                        type="button"
                        onClick={() =>
                          setDraft({
                            ...draft,
                            voix: { ...draft.voix, web_navigateur: nav.id },
                          })
                        }
                        className={cn(
                          'rounded-xl border p-3 text-left transition',
                          draft.voix.web_navigateur === nav.id
                            ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                            : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                        )}
                      >
                        <div className="text-xs font-semibold text-[#f8fafc]">{nav.label}</div>
                        <p className="mt-0.5 text-[11px] text-[#94a3b8]">{nav.desc}</p>
                      </button>
                    ))}
                  </div>
                </div>

                <div className="flex items-start gap-2.5 rounded-xl border border-white/10 bg-[#0b1020]/70 p-3.5 text-[11px] leading-relaxed text-[#cbd5e1]">
                  <Info className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]" />
                  <div className="space-y-1">
                    <p>
                      <strong>Fonctionnement simple :</strong> Jarvis ouvre la fenêtre du service (ou la ramène au premier plan), puis tu lances le mode vocal du site toi-même.
                    </p>
                    <p className="text-[#94a3b8]">
                      Aucune commande n&apos;est transmise à Jarvis dans ce mode (aucun pilotage ni lecture de la page). Ton compte personnel et ses conditions d&apos;utilisation s&apos;appliquent.
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-[11px] text-[#94a3b8]">
                    Pense à cliquer sur <strong>Enregistrer</strong> pour activer ce mode sur l&apos;orbe et <code className="font-mono">Ctrl+Espace</code>.
                  </span>
                  <button
                    type="button"
                    disabled={openingWeb}
                    onClick={() => void handleOpenPontWeb()}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3.5 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25 disabled:opacity-50"
                  >
                    {openingWeb ? (
                      <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <ExternalLink className="h-3.5 w-3.5" />
                    )}
                    {openingWeb ? 'Ouverture…' : 'Ouvrir maintenant'}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Microphone */}
          <div className="space-y-2">
            <label
              htmlFor="voix-micro-select"
              className="block text-xs font-medium text-[#cbd5e1]"
            >
              Microphone d&apos;entrée
            </label>
            <select
              id="voix-micro-select"
              value={draft.voix.micro ?? ''}
              onChange={(e) =>
                setDraft({
                  ...draft,
                  voix: {
                    ...draft.voix,
                    micro: e.target.value ? e.target.value : null,
                  },
                })
              }
              className="w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0]"
            >
              <option value="">Microphone par défaut du système</option>
              {selectedMicroMissing && draft.voix.micro && (
                <option value={draft.voix.micro}>
                  {draft.voix.micro} (non détecté actuellement)
                </option>
              )}
              {micros.map((m) => (
                <option key={`${m.index}-${m.nom}`} value={m.nom}>
                  {m.nom}
                  {m.defaut ? ' (par défaut)' : ''}
                </option>
              ))}
            </select>
            {loadingMicros && (
              <p className="text-[11px] text-[#64748b]">Détection des microphones…</p>
            )}
          </div>

          {/* Voix & Moteur TTS + Bouton Tester */}
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="hud-label block">Voix de réponse (TTS)</span>
              <button
                type="button"
                disabled={testingVoice}
                onClick={() => void handleTestVoice()}
                className="inline-flex items-center gap-1.5 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-1.5 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25 disabled:opacity-50"
              >
                {testingVoice ? (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                {testingVoice ? 'Synthèse…' : 'Tester la voix'}
              </button>
            </div>

            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {VOIX_PRESETS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => handleSelectVoixPreset(opt.id)}
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    selectedVoixPreset === opt.id
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                      : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                  )}
                >
                  <div className="text-xs font-semibold text-[#f8fafc]">{opt.label}</div>
                  <div className="mt-0.5 text-[11px] text-[#94a3b8]">{opt.desc}</div>
                </button>
              ))}
            </div>

            {draft.voix.moteur_tts === 'http' && (
              <div>
                <label
                  htmlFor="voix-tts-url"
                  className="block text-xs font-medium text-[#cbd5e1]"
                >
                  URL du serveur TTS local (compatible OpenAI)
                </label>
                <input
                  id="voix-tts-url"
                  type="url"
                  value={draft.voix.tts_url ?? ''}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      voix: { ...draft.voix, tts_url: e.target.value },
                    })
                  }
                  placeholder="http://127.0.0.1:8880"
                  className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 font-mono text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                />
                <p className="mt-1 text-[11px] text-[#94a3b8]">
                  Appelle <code className="font-mono">/v1/audio/speech</code> ; bascule automatiquement sur Edge-TTS si injoignable.
                </p>
              </div>
            )}

            <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-[#05070d]/60 p-3.5 text-xs">
              <div>
                <div className="font-medium text-[#f8fafc]">Lecture vocale activée</div>
                <div className="mt-0.5 text-[11px] text-[#94a3b8]">
                  Prononce automatiquement les réponses vocales de Jarvis.
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
          </div>

          {/* Modèle de transcription Whisper */}
          <div className="space-y-2.5">
            <span className="hud-label block">Modèle de transcription (Whisper)</span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {STT_MODELES.map((mod) => (
                <button
                  key={mod.id}
                  type="button"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      voix: {
                        ...draft.voix,
                        stt_modele: mod.id,
                        stt_beam_final: mod.id === 'large-v3-turbo' ? 1 : 5,
                      },
                    })
                  }
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    draft.voix.stt_modele === mod.id
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                      : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-[#f8fafc]">{mod.label}</span>
                    <span className="font-mono text-[10px] text-[var(--accent)]">{mod.taille}</span>
                  </div>
                  <p className="mt-1 text-[11px] leading-relaxed text-[#94a3b8]">{mod.note}</p>
                </button>
              ))}
            </div>
            <div className="flex items-start gap-2 rounded-xl border border-white/10 bg-[#05070d]/50 p-3 text-[11px] text-[#94a3b8]">
              <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--accent)]" />
              <span>
                {voixEtat?.stt_peripherique === 'cuda'
                  ? 'GPU CUDA détecté : Large v3 Turbo offre la meilleure vitesse et précision.'
                  : 'Aucun GPU CUDA actif détecté : le modèle Small (int8 CPU) est recommandé pour rester fluide.'}
              </span>
            </div>
          </div>

          {/* Déclenchement & Voie rapide */}
          <div className="space-y-3">
            <span className="hud-label block">Déclenchement &amp; Réactivité</span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {DECLENCHEMENT_OPTIONS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      voix: { ...draft.voix, declenchement: opt.id },
                    })
                  }
                  className={cn(
                    'rounded-xl border p-3 text-left transition',
                    draft.voix.declenchement === opt.id
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                      : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                  )}
                >
                  <div className="text-xs font-semibold text-[#f8fafc]">{opt.label}</div>
                  <div className="mt-0.5 text-[11px] text-[#94a3b8]">{opt.desc}</div>
                </button>
              ))}
            </div>

            <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-white/10 bg-[#05070d]/60 p-3.5 text-xs">
              <div>
                <div className="font-medium text-[#f8fafc]">
                  Réponses rapides (voie rapide)
                </div>
                <div className="mt-0.5 text-[11px] text-[#94a3b8]">
                  Garde Antigravity chaud (2 à 4 s) pour les questions directes ; bascule vers le pipeline complet dès qu&apos;une action est demandée.
                </div>
              </div>
              <input
                type="checkbox"
                checked={draft.voix.rapide}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    voix: { ...draft.voix, rapide: e.target.checked },
                  })
                }
                className="accent-[var(--accent)]"
              />
            </label>

            {draft.voix.rapide && (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {([
                  { id: 'agy', label: 'Antigravity (cloud)', desc: 'Plus intelligent, 2 à 4 s avant la première phrase.' },
                  { id: 'local', label: 'Modèle local (LM Studio)', desc: 'Réponse en moins d’une seconde, hors ligne, sur ton GPU. Les actions passent toujours par l’agent complet.' },
                ] as const).map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() =>
                      setDraft({ ...draft, voix: { ...draft.voix, llm_rapide: opt.id } })
                    }
                    className={cn(
                      'rounded-xl border p-3 text-left transition',
                      draft.voix.llm_rapide === opt.id
                        ? 'border-[var(--accent)]/60 bg-[var(--accent)]/12'
                        : 'border-white/10 bg-[#05070d]/70 hover:border-white/25',
                    )}
                  >
                    <div className="text-xs font-semibold text-[#f8fafc]">{opt.label}</div>
                    <div className="mt-0.5 text-[11px] text-[#94a3b8]">{opt.desc}</div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              disabled={saving}
              onClick={() => void handleSaveVoix()}
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
