/**
 * Store Zustand des mises à jour GitHub (`/api/maj/*`) et de la modale associée.
 * Gère le chargement silencieux (tolérant au 404), la vérification manuelle,
 * le suivi de progression et la réaction à l'événement WebSocket `maj_disponible`.
 */

import { create } from 'zustand';
import {
  getMajEtat,
  getMajProgression,
  installerMaj,
  setMajMode,
  verifierMaj,
  type MajDerniereInfo,
  type MajEtat,
  type MajMode,
  type MajProgression,
} from '@/api/maj';

interface MajState {
  version: string;
  derniere: MajDerniereInfo | null;
  disponible: boolean;
  verifie_le: string | null;
  mode: MajMode;

  loaded: boolean;
  loading: boolean;
  checking: boolean;
  installing: boolean;
  progression: MajProgression | null;
  modalOpen: boolean;
  endpointDisponible: boolean;
  error: string | null;

  fetchEtat: (options?: { silent?: boolean }) => Promise<MajEtat | null>;
  verifierMaintenant: () => Promise<MajEtat | null>;
  changerMode: (mode: MajMode) => Promise<void>;
  lancerInstallation: () => Promise<boolean>;
  rafraichirProgression: () => Promise<MajProgression | null>;
  setModalOpen: (open: boolean) => void;
  handleWsMajDisponible: (payload: {
    version?: string;
    notes?: string;
    legere?: boolean;
  }) => void;
}

/**
 * Store Zustand 5 pour l'état des mises à jour applicatives.
 */
export const useMaj = create<MajState>()((set, get) => ({
  version: '',
  derniere: null,
  disponible: false,
  verifie_le: null,
  mode: 'demander',

  loaded: false,
  loading: false,
  checking: false,
  installing: false,
  progression: null,
  modalOpen: false,
  endpointDisponible: false,
  error: null,

  fetchEtat: async (options) => {
    const silent = options?.silent ?? true;
    if (!get().loaded) {
      set({ loading: true });
    }
    try {
      const data = await getMajEtat({ silent });
      if (!data || typeof data.disponible !== 'boolean') {
        set({ loading: false, loaded: true, endpointDisponible: false });
        return null;
      }
      set({
        version: data.version ?? '',
        derniere: data.derniere ?? null,
        disponible: Boolean(data.disponible),
        verifie_le: data.verifie_le ?? null,
        mode:
          data.mode === 'auto' || data.mode === 'jamais' ? data.mode : 'demander',
        loaded: true,
        loading: false,
        endpointDisponible: true,
        error: null,
      });
      return data;
    } catch (err) {
      set({
        loading: false,
        loaded: true,
        endpointDisponible: false,
        error:
          !silent && err instanceof Error
            ? err.message
            : silent
              ? null
              : 'Impossible de récupérer les informations de mise à jour.',
      });
      return null;
    }
  },

  verifierMaintenant: async () => {
    set({ checking: true, error: null });
    try {
      const res = await verifierMaj({ silent: false });
      if (res && typeof res.disponible === 'boolean') {
        set({
          version: res.version ?? get().version,
          derniere: res.derniere ?? null,
          disponible: Boolean(res.disponible),
          verifie_le: res.verifie_le ?? new Date().toISOString(),
          mode:
            res.mode === 'auto' || res.mode === 'jamais' ? res.mode : get().mode,
          checking: false,
          endpointDisponible: true,
        });
        return res;
      }
      const etat = await get().fetchEtat({ silent: false });
      set({ checking: false });
      return etat;
    } catch (err) {
      set({
        checking: false,
        error:
          err instanceof Error
            ? err.message
            : 'Échec de la vérification des mises à jour.',
      });
      return null;
    }
  },

  changerMode: async (mode) => {
    const prevMode = get().mode;
    set({ mode, error: null });
    try {
      await setMajMode(mode, { silent: false });
    } catch (err) {
      set({
        mode: prevMode,
        error:
          err instanceof Error
            ? err.message
            : 'Impossible de modifier le mode de mise à jour.',
      });
      throw err;
    }
  },

  lancerInstallation: async () => {
    set({
      installing: true,
      error: null,
      progression: {
        etape: 'Démarrage de la mise à jour…',
        pourcent: 0,
        erreur: null,
      },
    });
    try {
      await installerMaj({ silent: true });
      return true;
    } catch (err) {
      const msg =
        err instanceof Error
          ? err.message
          : "Impossible de démarrer l'installation.";
      set({
        installing: false,
        error: msg,
        progression: { etape: 'Erreur', pourcent: 0, erreur: msg },
      });
      return false;
    }
  },

  rafraichirProgression: async () => {
    try {
      const prog = await getMajProgression({ silent: true });
      if (!prog) return null;
      const normalized: MajProgression = {
        etape: typeof prog.etape === 'string' ? prog.etape : 'En cours…',
        pourcent:
          typeof prog.pourcent === 'number' && !Number.isNaN(prog.pourcent)
            ? Math.max(0, Math.min(100, Math.round(prog.pourcent)))
            : 0,
        erreur: prog.erreur ?? null,
      };
      set({
        progression: normalized,
        installing: normalized.erreur ? false : get().installing,
        error: normalized.erreur ?? null,
      });
      return normalized;
    } catch {
      return null;
    }
  },

  setModalOpen: (modalOpen) => set({ modalOpen }),

  handleWsMajDisponible: (payload) => {
    const version = payload.version?.trim() || get().derniere?.version || '';
    const notes = payload.notes ?? get().derniere?.notes ?? '';
    const legere =
      typeof payload.legere === 'boolean'
        ? payload.legere
        : (get().derniere?.legere ?? false);

    if (version) {
      set({
        disponible: true,
        endpointDisponible: true,
        derniere: { version, notes, legere },
      });
    } else {
      set({ disponible: true, endpointDisponible: true });
    }
    void get().fetchEtat({ silent: true });
  },
}));
