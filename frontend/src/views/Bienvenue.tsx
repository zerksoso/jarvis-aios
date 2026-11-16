/**
 * Assistant de premier lancement (`/#/bienvenue`), affiché hors coque HUD.
 * Guide l'utilisateur en 4 étapes : Moteurs IA → Connexion → Profil → Terminé.
 * Sonde `/api/setup/etat` toutes les 3 s et nettoie tous les timers au démontage.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Cpu,
  Download,
  ExternalLink,
  Keyboard,
  LoaderCircle,
  RefreshCw,
  Rocket,
  Terminal,
  User,
} from 'lucide-react';
import type {
  SetupMoteurEtat,
  SetupMoteurId,
  SetupProfilPayload,
} from '@/api/setup';
import { cn } from '@/lib/cn';
import { AUCUN_LOG_SETUP, useSetup } from '@/store/useSetup';

type WizardStep = 1 | 2 | 3 | 4;

interface MoteurDescriptor {
  id: SetupMoteurId;
  nom: string;
  fournisseur: string;
  description: string;
}

const MOTEURS_CATALOGUE: MoteurDescriptor[] = [
  {
    id: 'agy',
    nom: 'Antigravity',
    fournisseur: 'Google Gemini',
    description:
      'Utilise ton compte Google pour accéder aux modèles Gemini et à la recherche rapide.',
  },
  {
    id: 'claude',
    nom: 'Claude Code',
    fournisseur: 'Anthropic Claude',
    description:
      'Utilise ton compte Claude pour les analyses approfondies, la rédaction et le code.',
  },
  {
    id: 'codex',
    nom: 'Codex',
    fournisseur: 'OpenAI ChatGPT',
    description:
      'Utilise ton compte ChatGPT pour les modèles OpenAI en complément des autres moteurs.',
  },
];

const ETAPES: Array<{ num: WizardStep; titre: string }> = [
  { num: 1, titre: 'Moteurs IA' },
  { num: 2, titre: 'Connexion' },
  { num: 3, titre: 'Profil' },
  { num: 4, titre: 'Terminé' },
];

const JOB_POLL_INTERVAL_MS = 1000;

/**
 * Vue autonome de l'assistant de bienvenue (`/#/bienvenue`).
 *
 * Effets de bord :
 * - Déclenche le polling 3 s sur `/api/setup/etat` à l'ouverture et l'arrête au démontage.
 * - Sonde les jobs d'installation actifs (`/api/setup/jobs/{id}`) toutes les secondes jusqu'à complétion.
 */
export function Bienvenue() {
  const navigate = useNavigate();

  const etat = useSetup((s) => s.etat);
  const loading = useSetup((s) => s.loading);
  const loaded = useSetup((s) => s.loaded);
  const endpointDisponible = useSetup((s) => s.endpointDisponible);
  const storeError = useSetup((s) => s.error);
  const jobsByMoteur = useSetup((s) => s.jobsByMoteur);
  const jobLogsById = useSetup((s) => s.jobLogsById);

  const fetchEtat = useSetup((s) => s.fetchEtat);
  const startPolling = useSetup((s) => s.startPolling);
  const lancerInstallation = useSetup((s) => s.lancerInstallation);
  const rafraichirJob = useSetup((s) => s.rafraichirJob);
  const lancerConnexion = useSetup((s) => s.lancerConnexion);
  const enregistrerProfil = useSetup((s) => s.enregistrerProfil);
  const cloturerSetup = useSetup((s) => s.cloturerSetup);

  const [step, setStep] = useState<WizardStep>(1);
  const [installingMoteur, setInstallingMoteur] = useState<SetupMoteurId | null>(
    null,
  );
  const [connectingMoteur, setConnectingMoteur] = useState<SetupMoteurId | null>(
    null,
  );
  const [savingProfil, setSavingProfil] = useState(false);
  const [finishing, setFinishing] = useState(false);

  const [profil, setProfil] = useState<SetupProfilPayload>({
    prenom: '',
    activite: '',
    secteur: '',
    objectifs: '',
    outils: '',
    preferences: '',
  });

  const jobTimersRef = useRef<Record<string, number>>({});

  // Chargement initial et polling toutes les 3 s sur /api/setup/etat
  useEffect(() => {
    void fetchEtat({ silent: false });
    const stopPolling = startPolling();
    const timersMap = jobTimersRef.current;

    return () => {
      stopPolling();
      for (const id of Object.keys(timersMap)) {
        window.clearInterval(timersMap[id]);
        delete timersMap[id];
      }
    };
  }, [fetchEtat, startPolling]);

  // Efface automatiquement l'indicateur d'attente dès que le moteur passe à connecté
  useEffect(() => {
    if (!connectingMoteur || !etat?.moteurs) return;
    if (etat.moteurs[connectingMoteur]?.connecte) {
      setConnectingMoteur(null);
    }
  }, [connectingMoteur, etat]);

  const moteursList = useMemo(() => {
    return MOTEURS_CATALOGUE.map((desc) => {
      const m: SetupMoteurEtat | undefined = etat?.moteurs?.[desc.id];
      return { desc, etat: m };
    });
  }, [etat]);

  const nbInstalles = useMemo(
    () => moteursList.filter((item) => Boolean(item.etat?.installe)).length,
    [moteursList],
  );

  const nbConnectes = useMemo(
    () =>
      moteursList.filter(
        (item) => Boolean(item.etat?.installe && item.etat?.connecte),
      ).length,
    [moteursList],
  );

  const startJobPolling = useCallback(
    (jobId: string) => {
      if (jobTimersRef.current[jobId] !== undefined) return;
      void rafraichirJob(jobId);
      const timerId = window.setInterval(() => {
        void rafraichirJob(jobId).then((job) => {
          if (job && job.statut !== 'en_cours') {
            window.clearInterval(timerId);
            delete jobTimersRef.current[jobId];
          }
        });
      }, JOB_POLL_INTERVAL_MS);
      jobTimersRef.current[jobId] = timerId;
    },
    [rafraichirJob],
  );

  const handleInstall = async (moteur: SetupMoteurId) => {
    setInstallingMoteur(moteur);
    try {
      const jobId = await lancerInstallation(moteur);
      if (jobId) {
        startJobPolling(jobId);
      }
    } finally {
      setInstallingMoteur(null);
    }
  };

  const handleConnect = async (moteur: SetupMoteurId) => {
    setConnectingMoteur(moteur);
    const ok = await lancerConnexion(moteur);
    if (!ok) {
      setConnectingMoteur(null);
    }
  };

  const handleSaveProfil = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingProfil(true);
    try {
      const ok = await enregistrerProfil(profil);
      if (ok) {
        setStep(4);
      }
    } finally {
      setSavingProfil(false);
    }
  };

  const handleFinish = async () => {
    setFinishing(true);
    try {
      const ok = await cloturerSetup();
      if (ok) {
        navigate('/accueil', { replace: true });
      }
    } finally {
      setFinishing(false);
    }
  };

  const activeError = storeError;
  const progressPct = Math.round((step / ETAPES.length) * 100);

  return (
    <div className="min-h-screen w-full overflow-x-hidden bg-[#05070d] px-3 py-6 text-[#e2e8f0] sm:px-6 sm:py-10">
      <main className="mx-auto w-full max-w-4xl space-y-6">
        {/* En-tête de marque */}
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
              <Cpu className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="font-display text-lg font-bold tracking-wide text-[#f8fafc] sm:text-xl">
                Bienvenue dans Jarvis AIOS
              </h1>
              <p className="text-xs text-[#94a3b8]">
                Configuration guidée en 4 étapes{etat?.version ? ` · v${etat.version.replace(/^v/i, '')}` : ''}
              </p>
            </div>
          </div>

          <Link
            to="/accueil"
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-[#0b1020] px-3 py-1.5 text-xs text-[#94a3b8] transition hover:border-[var(--accent)]/50 hover:text-[#e2e8f0]"
          >
            Aller à l&apos;accueil
          </Link>
        </header>

        {/* Barre de progression des 4 étapes */}
        <section
          aria-label="Progression de l'assistant"
          className="hud-panel space-y-3 p-4"
        >
          <div className="flex items-center justify-between text-xs">
            <span className="font-medium text-[#f8fafc]">
              Étape {step} sur {ETAPES.length} : {ETAPES[step - 1].titre}
            </span>
            <span className="font-mono text-[var(--accent)]">{progressPct}%</span>
          </div>
          <div
            className="h-1.5 w-full overflow-hidden rounded-full bg-white/10"
            role="progressbar"
            aria-valuenow={progressPct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-[var(--accent)] transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <nav
            aria-label="Étapes de configuration"
            className="grid grid-cols-2 gap-2 sm:grid-cols-4"
          >
            {ETAPES.map((item) => {
              const isActive = item.num === step;
              const isPast = item.num < step;
              return (
                <button
                  key={item.num}
                  type="button"
                  onClick={() => setStep(item.num)}
                  aria-current={isActive ? 'step' : undefined}
                  className={cn(
                    'flex items-center gap-2 rounded-lg border px-2.5 py-2 text-left text-xs transition',
                    isActive
                      ? 'border-[var(--accent)]/60 bg-[var(--accent)]/15 text-[#f8fafc]'
                      : isPast
                        ? 'border-[#34d399]/35 bg-[#34d399]/10 text-[#34d399]'
                        : 'border-white/10 bg-[#05070d]/60 text-[#94a3b8] hover:border-white/20 hover:text-[#e2e8f0]',
                  )}
                >
                  <span
                    className={cn(
                      'flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-semibold',
                      isActive
                        ? 'bg-[var(--accent)] text-[#05070d]'
                        : isPast
                          ? 'bg-[#34d399]/25 text-[#34d399]'
                          : 'bg-white/10 text-[#94a3b8]',
                    )}
                  >
                    {isPast ? <Check className="h-3 w-3" /> : item.num}
                  </span>
                  <span className="truncate font-medium">{item.titre}</span>
                </button>
              );
            })}
          </nav>
        </section>

        {/* État de chargement initial */}
        {!loaded && loading && (
          <div className="hud-panel flex items-center gap-3 p-6 text-sm text-[#cbd5e1]">
            <LoaderCircle className="h-5 w-5 animate-spin text-[var(--accent)]" />
            <span>Analyse des moteurs d&apos;IA disponibles sur ton ordinateur…</span>
          </div>
        )}

        {/* État d'erreur explicite */}
        {loaded && (!endpointDisponible || activeError) && (
          <div
            role="alert"
            className="hud-panel flex flex-wrap items-start justify-between gap-3 border-[#f59e0b]/40 p-4"
          >
            <div className="flex min-w-0 flex-1 items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-[#f59e0b]" />
              <div className="min-w-0 space-y-1 text-xs">
                <p className="font-semibold text-[#f8fafc]">
                  {activeError ||
                    "L'assistant de configuration serveur n'a pas encore répondu."}
                </p>
                <p className="text-[#94a3b8]">
                  Vérifie que Jarvis est bien démarré en arrière-plan ou réessaie dans quelques secondes.
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void fetchEtat({ silent: false })}
              className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 bg-[#05070d] px-3 py-1.5 text-xs font-medium text-[#e2e8f0] transition hover:border-[var(--accent)]/50"
            >
              <RefreshCw className="h-3.5 w-3.5" />
              Réessayer
            </button>
          </div>
        )}

        {/* ÉTAPE 1 : MOTEURS IA */}
        {step === 1 && (
          <section className="space-y-4" aria-label="Choix et installation des moteurs IA">
            <div className="hud-glass flex items-start gap-3 p-4 text-xs leading-relaxed text-[#cbd5e1]">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--accent)]" />
              <p>
                <strong className="text-[#f8fafc]">
                  Au moins un moteur est nécessaire.
                </strong>{' '}
                Aucune clé API payante n&apos;est utilisée : la connexion se fait
                avec tes propres comptes.
              </p>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {moteursList.map(({ desc, etat: mEtat }) => {
                const installe = Boolean(mEtat?.installe);
                const connecte = Boolean(mEtat?.connecte);
                const autoInstall = Boolean(mEtat?.installation_auto);
                const pageDl = mEtat?.page_telechargement;
                const jobId = jobsByMoteur[desc.id];
                const job = jobId ? jobLogsById[jobId] : undefined;
                const jobLogs = job?.log ?? AUCUN_LOG_SETUP;
                const isJobRunning =
                  installingMoteur === desc.id || job?.statut === 'en_cours';
                const isWaitingConn = connectingMoteur === desc.id && !connecte;

                return (
                  <article
                    key={desc.id}
                    className="hud-panel flex min-w-0 flex-col justify-between gap-4 p-4"
                  >
                    <div className="space-y-3">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <h2 className="font-display text-base font-semibold text-[#f8fafc]">
                            {desc.nom}
                          </h2>
                          <p className="text-[11px] text-[var(--accent)]">
                            {desc.fournisseur}
                          </p>
                        </div>
                        {mEtat?.version && (
                          <span className="rounded border border-white/10 bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-[#94a3b8]">
                            {mEtat.version}
                          </span>
                        )}
                      </div>

                      <div className="flex flex-wrap gap-1.5">
                        <span
                          className={cn(
                            'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
                            installe
                              ? 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]'
                              : 'border-white/15 bg-white/5 text-[#94a3b8]',
                          )}
                        >
                          {installe ? 'Installé' : 'Non installé'}
                        </span>
                        <span
                          className={cn(
                            'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium',
                            connecte
                              ? 'border-[#34d399]/40 bg-[#34d399]/10 text-[#34d399]'
                              : 'border-[#f59e0b]/35 bg-[#f59e0b]/10 text-[#f59e0b]',
                          )}
                        >
                          {connecte ? 'Connecté' : 'Non connecté'}
                        </span>
                      </div>

                      <p className="text-xs leading-relaxed text-[#94a3b8]">
                        {desc.description}
                      </p>
                    </div>

                    <div className="space-y-2.5 pt-2 border-t border-white/10">
                      {!installe && autoInstall && (
                        <button
                          type="button"
                          disabled={isJobRunning}
                          onClick={() => void handleInstall(desc.id)}
                          className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25 disabled:opacity-50"
                        >
                          {isJobRunning ? (
                            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Download className="h-3.5 w-3.5" />
                          )}
                          {isJobRunning ? 'Installation en cours…' : 'Installer'}
                        </button>
                      )}

                      {!installe && !autoInstall && pageDl && (
                        <a
                          href={pageDl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--accent)]/50 bg-[var(--accent)]/15 px-3 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/25"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                          Page de téléchargement
                        </a>
                      )}

                      {!installe && !autoInstall && !pageDl && (
                        <p className="rounded-lg border border-white/10 bg-[#05070d]/70 p-2.5 text-[11px] text-[#94a3b8]">
                          {desc.id === 'codex' && etat && !etat.node?.installe
                            ? 'Node.js est requis pour installer Codex automatiquement.'
                            : 'Installation manuelle requise puis clique sur « Actualiser ».'}
                        </p>
                      )}

                      {installe && !connecte && (
                        <button
                          type="button"
                          disabled={isWaitingConn}
                          onClick={() => void handleConnect(desc.id)}
                          className="inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[#f59e0b]/50 bg-[#f59e0b]/15 px-3 py-2 text-xs font-semibold text-[#fbbf24] transition hover:bg-[#f59e0b]/25 disabled:opacity-60"
                        >
                          {isWaitingConn ? (
                            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Terminal className="h-3.5 w-3.5" />
                          )}
                          {isWaitingConn ? 'En attente de connexion…' : 'Se connecter'}
                        </button>
                      )}

                      {installe && connecte && (
                        <div className="flex items-center justify-center gap-1.5 rounded-xl border border-[#34d399]/35 bg-[#34d399]/10 px-3 py-2 text-xs font-medium text-[#34d399]">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Prêt à l&apos;emploi
                        </div>
                      )}

                      {job && (
                        <div className="space-y-1.5 rounded-lg border border-white/10 bg-[#05070d] p-2.5">
                          <div className="flex items-center justify-between text-[11px]">
                            <span className="font-mono text-[#94a3b8]">
                              Journal d&apos;installation
                            </span>
                            <span
                              className={cn(
                                'font-mono font-semibold',
                                job.statut === 'ok'
                                  ? 'text-[#34d399]'
                                  : job.statut === 'erreur'
                                    ? 'text-[#f87171]'
                                    : 'text-[var(--accent)]',
                              )}
                            >
                              {job.statut === 'ok'
                                ? 'Terminé'
                                : job.statut === 'erreur'
                                  ? 'Erreur'
                                  : 'En cours…'}
                            </span>
                          </div>
                          <pre className="max-h-32 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-[#cbd5e1]">
                            {jobLogs.length > 0
                              ? jobLogs.join('\n')
                              : 'Préparation de l’installation…'}
                          </pre>
                        </div>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <button
                type="button"
                onClick={() => void fetchEtat({ silent: false })}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-[#0b1020] px-3.5 py-2 text-xs font-medium text-[#cbd5e1] transition hover:border-white/30"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Actualiser la détection
              </button>

              <button
                type="button"
                onClick={() => setStep(2)}
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30"
              >
                Étape suivante : Connexion
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </section>
        )}

        {/* ÉTAPE 2 : CONNEXION AUX COMPTES */}
        {step === 2 && (
          <section className="hud-panel space-y-5 p-5" aria-label="Connexion à tes comptes">
            <div className="space-y-1">
              <h2 className="font-display text-base font-semibold text-[#f8fafc]">
                Connecte-toi à au moins un moteur
              </h2>
              <p className="text-xs leading-relaxed text-[#94a3b8]">
                Au moins un moteur est nécessaire. Aucune clé API payante n&apos;est
                utilisée : la connexion se fait avec tes propres comptes.
              </p>
            </div>

            {nbInstalles === 0 ? (
              <div className="rounded-xl border border-[#f59e0b]/40 bg-[#f59e0b]/10 p-4 text-xs text-[#fbbf24]">
                Aucun moteur n&apos;est encore détecté sur cet ordinateur. Reviens à
                l&apos;étape 1 pour en installer ou en télécharger au moins un.
              </div>
            ) : (
              <div className="space-y-3">
                {moteursList.map(({ desc, etat: mEtat }) => {
                  const installe = Boolean(mEtat?.installe);
                  const connecte = Boolean(mEtat?.connecte);
                  const isWaiting = connectingMoteur === desc.id && !connecte;

                  return (
                    <div
                      key={desc.id}
                      className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-[#05070d]/70 p-3.5"
                    >
                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-display text-sm font-semibold text-[#f8fafc]">
                            {desc.nom}
                          </span>
                          <span className="text-xs text-[#94a3b8]">
                            ({desc.fournisseur})
                          </span>
                        </div>
                        <p className="text-xs text-[#94a3b8]">
                          {!installe
                            ? 'Non installé à l’étape précédente.'
                            : connecte
                              ? 'Compte connecté et vérifié.'
                              : isWaiting
                                ? 'Console ouverte : termine la connexion dans la fenêtre qui vient de s’afficher…'
                                : 'Clique sur « Se connecter » pour ouvrir la fenêtre de connexion.'}
                        </p>
                      </div>

                      <div className="shrink-0">
                        {!installe ? (
                          <span className="inline-flex rounded-full border border-white/10 px-2.5 py-1 text-[11px] text-[#64748b]">
                            Non installé
                          </span>
                        ) : connecte ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#34d399]/40 bg-[#34d399]/10 px-3 py-1 text-xs font-medium text-[#34d399]">
                            <CheckCircle2 className="h-3.5 w-3.5" />
                            Connecté
                          </span>
                        ) : (
                          <button
                            type="button"
                            disabled={isWaiting}
                            onClick={() => void handleConnect(desc.id)}
                            className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-3.5 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-60"
                          >
                            {isWaiting ? (
                              <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Terminal className="h-3.5 w-3.5" />
                            )}
                            {isWaiting ? 'En attente de connexion…' : 'Se connecter'}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {connectingMoteur && (
              <div className="flex items-center gap-2.5 rounded-xl border border-[var(--accent)]/35 bg-[var(--accent)]/10 p-3 text-xs text-[#cbd5e1]">
                <LoaderCircle className="h-4 w-4 shrink-0 animate-spin text-[var(--accent)]" />
                <span>
                  Vérification automatique toutes les 3 secondes. Dès que ta connexion
                  est terminée, le statut passera au vert ici.
                </span>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <button
                type="button"
                onClick={() => setStep(1)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-[#05070d] px-3.5 py-2 text-xs font-medium text-[#cbd5e1] transition hover:border-white/30"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Précédent
              </button>

              <button
                type="button"
                onClick={() => setStep(3)}
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30"
              >
                {nbConnectes > 0 ? 'Étape suivante : Profil' : 'Continuer quand même'}
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </section>
        )}

        {/* ÉTAPE 3 : PROFIL UTILISATEUR (FACULTATIF) */}
        {step === 3 && (
          <section className="hud-panel space-y-5 p-5" aria-label="Ton profil">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[var(--accent)]/40 bg-[var(--accent)]/15 text-[var(--accent)]">
                <User className="h-4 w-4" />
              </div>
              <div className="space-y-1">
                <h2 className="font-display text-base font-semibold text-[#f8fafc]">
                  Parle un peu de toi à Jarvis (facultatif)
                </h2>
                <p className="text-xs text-[#94a3b8]">
                  Ces informations restent uniquement sur ton ordinateur pour adapter
                  les réponses de tes assistants à ton quotidien.
                </p>
              </div>
            </div>

            {etat?.profil_rempli && (
              <div className="rounded-xl border border-[#34d399]/35 bg-[#34d399]/10 p-3 text-xs text-[#34d399]">
                Un profil est déjà enregistré : tu peux le compléter ci-dessous ou passer cette étape.
              </div>
            )}

            <form onSubmit={(e) => void handleSaveProfil(e)} className="space-y-4">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="setup-prenom"
                    className="block text-xs font-medium text-[#cbd5e1]"
                  >
                    Prénom
                  </label>
                  <input
                    id="setup-prenom"
                    type="text"
                    value={profil.prenom ?? ''}
                    onChange={(e) =>
                      setProfil((p) => ({ ...p, prenom: e.target.value }))
                    }
                    placeholder="Ex. : Camille"
                    className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                  />
                </div>

                <div>
                  <label
                    htmlFor="setup-activite"
                    className="block text-xs font-medium text-[#cbd5e1]"
                  >
                    Activité
                  </label>
                  <input
                    id="setup-activite"
                    type="text"
                    value={profil.activite ?? ''}
                    onChange={(e) =>
                      setProfil((p) => ({ ...p, activite: e.target.value }))
                    }
                    placeholder="Ex. : Gérant, consultant, étudiant…"
                    className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="setup-secteur"
                    className="block text-xs font-medium text-[#cbd5e1]"
                  >
                    Secteur
                  </label>
                  <input
                    id="setup-secteur"
                    type="text"
                    value={profil.secteur ?? ''}
                    onChange={(e) =>
                      setProfil((p) => ({ ...p, secteur: e.target.value }))
                    }
                    placeholder="Ex. : Immobilier, commerce, conseil…"
                    className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                  />
                </div>

                <div>
                  <label
                    htmlFor="setup-outils"
                    className="block text-xs font-medium text-[#cbd5e1]"
                  >
                    Outils du quotidien
                  </label>
                  <input
                    id="setup-outils"
                    type="text"
                    value={profil.outils ?? ''}
                    onChange={(e) =>
                      setProfil((p) => ({ ...p, outils: e.target.value }))
                    }
                    placeholder="Ex. : Gmail, Excel, Notion, WhatsApp…"
                    className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                  />
                </div>
              </div>

              <div>
                <label
                  htmlFor="setup-objectifs"
                  className="block text-xs font-medium text-[#cbd5e1]"
                >
                  Tes objectifs avec Jarvis
                </label>
                <textarea
                  id="setup-objectifs"
                  rows={2}
                  value={profil.objectifs ?? ''}
                  onChange={(e) =>
                    setProfil((p) => ({ ...p, objectifs: e.target.value }))
                  }
                  placeholder="Ex. : Rédiger mes messages plus vite, organiser mes projets, faire ma veille…"
                  className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                />
              </div>

              <div>
                <label
                  htmlFor="setup-preferences"
                  className="block text-xs font-medium text-[#cbd5e1]"
                >
                  Préférences (ton, format des réponses…)
                </label>
                <textarea
                  id="setup-preferences"
                  rows={2}
                  value={profil.preferences ?? ''}
                  onChange={(e) =>
                    setProfil((p) => ({ ...p, preferences: e.target.value }))
                  }
                  placeholder="Ex. : Réponses courtes et concrètes, tutoiement…"
                  className="mt-1 w-full rounded-xl border border-white/10 bg-[#05070d] px-3 py-2 text-xs text-[#e2e8f0] placeholder:text-[#64748b]"
                />
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setStep(2)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-[#05070d] px-3.5 py-2 text-xs font-medium text-[#cbd5e1] transition hover:border-white/30"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  Précédent
                </button>

                <div className="flex flex-wrap items-center gap-2.5">
                  <button
                    type="button"
                    disabled={savingProfil}
                    onClick={() => setStep(4)}
                    className="rounded-xl border border-white/15 bg-[#05070d] px-3.5 py-2 text-xs font-medium text-[#94a3b8] transition hover:text-[#e2e8f0] disabled:opacity-50"
                  >
                    Passer
                  </button>
                  <button
                    type="submit"
                    disabled={savingProfil}
                    className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-4 py-2 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-50"
                  >
                    {savingProfil ? 'Enregistrement…' : 'Enregistrer et continuer'}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </form>
          </section>
        )}

        {/* ÉTAPE 4 : TERMINÉ */}
        {step === 4 && (
          <section className="hud-panel space-y-5 p-5" aria-label="Configuration terminée">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-[#34d399]/40 bg-[#34d399]/15 text-[#34d399]">
                <Rocket className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <h2 className="font-display text-base font-semibold text-[#f8fafc]">
                  Tout est prêt pour démarrer
                </h2>
                <p className="text-xs text-[#94a3b8]">
                  Voici le récapitulatif de tes moteurs IA et les raccourcis pour
                  piloter Jarvis au quotidien.
                </p>
              </div>
            </div>

            {nbConnectes === 0 && (
              <div className="rounded-xl border border-[#f59e0b]/40 bg-[#f59e0b]/10 p-3.5 text-xs text-[#fbbf24]">
                Attention : aucun moteur n&apos;est encore connecté. Tu pourras rouvrir
                cet assistant à tout moment depuis les Paramètres.
              </div>
            )}

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              {moteursList.map(({ desc, etat: mEtat }) => {
                const installe = Boolean(mEtat?.installe);
                const connecte = Boolean(mEtat?.connecte);
                return (
                  <div
                    key={desc.id}
                    className="rounded-xl border border-white/10 bg-[#05070d]/70 p-3 text-xs"
                  >
                    <div className="font-display font-semibold text-[#f8fafc]">
                      {desc.nom}
                    </div>
                    <div className="mt-1 text-[11px] text-[#94a3b8]">
                      {connecte
                        ? 'Installé et connecté'
                        : installe
                          ? 'Installé (non connecté)'
                          : 'Non installé'}
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="rounded-xl border border-white/10 bg-[#05070d]/70 p-4 space-y-2.5">
              <div className="flex items-center gap-2 text-xs font-semibold text-[#f8fafc]">
                <Keyboard className="h-4 w-4 text-[var(--accent)]" />
                <span>Raccourcis utiles</span>
              </div>
              <ul className="space-y-2 text-xs text-[#cbd5e1]">
                <li className="flex flex-wrap items-center justify-between gap-2">
                  <span>Parler à Jarvis à la voix</span>
                  <kbd className="rounded border border-white/15 bg-white/5 px-2 py-0.5 font-mono text-[11px] text-[var(--accent)]">
                    Ctrl+Espace
                  </kbd>
                </li>
                <li className="flex flex-wrap items-center justify-between gap-2">
                  <span>Ouvrir la palette de commandes et recherches rapides</span>
                  <kbd className="rounded border border-white/15 bg-white/5 px-2 py-0.5 font-mono text-[11px] text-[var(--accent)]">
                    Ctrl+K
                  </kbd>
                </li>
              </ul>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <button
                type="button"
                onClick={() => setStep(3)}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/15 bg-[#05070d] px-3.5 py-2 text-xs font-medium text-[#cbd5e1] transition hover:border-white/30"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Précédent
              </button>

              <button
                type="button"
                disabled={finishing}
                onClick={() => void handleFinish()}
                className="inline-flex items-center gap-2 rounded-xl border border-[var(--accent)]/60 bg-[var(--accent)]/20 px-5 py-2.5 text-xs font-semibold text-[var(--accent)] transition hover:bg-[var(--accent)]/30 disabled:opacity-50"
              >
                {finishing ? (
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                ) : (
                  <Rocket className="h-4 w-4" />
                )}
                {finishing ? 'Ouverture…' : 'Ouvrir Jarvis'}
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
