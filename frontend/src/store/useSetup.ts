/**
 * Store Zustand de l'assistant de premier lancement (`/api/setup/*`).
 * Gère le chargement silencieux au démarrage, le polling toutes les 3 s pendant
 * l'assistant et la mémorisation par session de la redirection automatique.
 */

import { create } from 'zustand';
import {
  connecterMoteur,
  getSetupEtat,
  getSetupJob,
  installerMoteur,
  sauvegarderProfil,
  terminerSetup,
  type SetupEtat,
  type SetupJobEtat,
  type SetupMoteurId,
  type SetupProfilPayload,
} from '@/api/setup';

const SESSION_REDIRECT_KEY = 'jarvis-setup-redirect-done';
const POLL_INTERVAL_MS = 3000;

/** Tableau vide partagé pour éviter toute allocation dans les sélecteurs Zustand 5. */
export const AUCUN_LOG_SETUP: string[] = [];

let memoryRedirectDone = false;
let pollTimerId: number | null = null;
let pollRefCount = 0;
let pollInFlight = false;

/**
 * Indique si la redirection automatique vers `/bienvenue` a déjà été tentée durant cette session.
 *
 * @returns `true` si la redirection a déjà eu lieu dans l'onglet courant.
 */
export function hasSetupRedirectedThisSession(): boolean {
  if (memoryRedirectDone) return true;
  if (typeof window === 'undefined') return false;
  try {
    return window.sessionStorage.getItem(SESSION_REDIRECT_KEY) === '1';
  } catch {
    return memoryRedirectDone;
  }
}

/**
 * Mémorise que la redirection automatique vers `/bienvenue` a déjà été effectuée pour la session.
 */
export function markSetupRedirectedThisSession(): void {
  memoryRedirectDone = true;
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(SESSION_REDIRECT_KEY, '1');
  } catch {
    // Stockage de session indisponible : le drapeau mémoire suffit.
  }
}

interface SetupState {
  etat: SetupEtat | null;
  loading: boolean;
  loaded: boolean;
  endpointDisponible: boolean;
  error: string | null;
  jobsByMoteur: Partial<Record<SetupMoteurId, string>>;
  jobLogsById: Record<string, SetupJobEtat>;

  fetchEtat: (options?: { silent?: boolean }) => Promise<SetupEtat | null>;
  startPolling: () => () => void;
  stopPolling: () => void;
  lancerInstallation: (moteur: SetupMoteurId) => Promise<string | null>;
  rafraichirJob: (jobId: string) => Promise<SetupJobEtat | null>;
  lancerConnexion: (moteur: SetupMoteurId) => Promise<boolean>;
  enregistrerProfil: (profil: SetupProfilPayload) => Promise<boolean>;
  cloturerSetup: () => Promise<boolean>;
}

/**
 * Store Zustand 5 pilotant l'état et les actions de l'assistant de bienvenue.
 */
export const useSetup = create<SetupState>()((set, get) => ({
  etat: null,
  loading: false,
  loaded: false,
  endpointDisponible: false,
  error: null,
  jobsByMoteur: {},
  jobLogsById: {},

  fetchEtat: async (options) => {
    const silent = options?.silent ?? true;
    if (!get().loaded) {
      set({ loading: true });
    }
    try {
      const data = await getSetupEtat({ silent });
      if (!data || typeof data.termine !== 'boolean') {
        set({ loading: false, loaded: true, endpointDisponible: false });
        return null;
      }
      set({
        etat: data,
        loading: false,
        loaded: true,
        endpointDisponible: true,
        error: null,
      });
      return data;
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Impossible de lire l'état de configuration.";
      set({
        loading: false,
        loaded: true,
        endpointDisponible: false,
        error: silent ? null : message,
      });
      return null;
    }
  },

  startPolling: () => {
    pollRefCount += 1;
    if (typeof window !== 'undefined' && pollTimerId === null) {
      pollTimerId = window.setInterval(() => {
        if (pollInFlight) return;
        pollInFlight = true;
        void get()
          .fetchEtat({ silent: true })
          .finally(() => {
            pollInFlight = false;
          });
      }, POLL_INTERVAL_MS);
    }
    return () => {
      get().stopPolling();
    };
  },

  stopPolling: () => {
    pollRefCount = Math.max(0, pollRefCount - 1);
    if (pollRefCount === 0 && pollTimerId !== null && typeof window !== 'undefined') {
      window.clearInterval(pollTimerId);
      pollTimerId = null;
    }
  },

  lancerInstallation: async (moteur) => {
    set({ error: null });
    try {
      const res = await installerMoteur(moteur, { silent: true });
      const jobId = res?.job_id;
      if (!jobId) {
        set({ error: "Le serveur n'a pas renvoyé d'identifiant d'installation." });
        return null;
      }
      set((state) => ({
        jobsByMoteur: { ...state.jobsByMoteur, [moteur]: jobId },
        jobLogsById: {
          ...state.jobLogsById,
          [jobId]: { statut: 'en_cours', log: [] },
        },
      }));
      return jobId;
    } catch (err) {
      set({
        error:
          err instanceof Error
            ? err.message
            : "Impossible de démarrer l'installation du moteur.",
      });
      return null;
    }
  },

  rafraichirJob: async (jobId) => {
    try {
      const job = await getSetupJob(jobId, { silent: true });
      set((state) => ({
        jobLogsById: {
          ...state.jobLogsById,
          [jobId]: {
            statut: job.statut,
            log: Array.isArray(job.log) ? job.log : AUCUN_LOG_SETUP,
          },
        },
      }));
      if (job.statut !== 'en_cours') {
        void get().fetchEtat({ silent: true });
      }
      return job;
    } catch {
      return null;
    }
  },

  lancerConnexion: async (moteur) => {
    set({ error: null });
    try {
      const res = await connecterMoteur(moteur, { silent: true });
      return Boolean(res?.ok ?? true);
    } catch (err) {
      set({
        error:
          err instanceof Error
            ? err.message
            : "Impossible d'ouvrir la fenêtre de connexion.",
      });
      return false;
    }
  },

  enregistrerProfil: async (profil) => {
    set({ error: null });
    try {
      await sauvegarderProfil(profil, { silent: true });
      await get().fetchEtat({ silent: true });
      return true;
    } catch (err) {
      set({
        error:
          err instanceof Error
            ? err.message
            : "Impossible d'enregistrer ton profil.",
      });
      return false;
    }
  },

  cloturerSetup: async () => {
    set({ error: null });
    try {
      await terminerSetup({ silent: true });
      set((state) => ({
        etat: state.etat ? { ...state.etat, termine: true } : state.etat,
      }));
      return true;
    } catch (err) {
      set({
        error:
          err instanceof Error
            ? err.message
            : "Impossible de finaliser l'assistant.",
      });
      return false;
    }
  },
}));
