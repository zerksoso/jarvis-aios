import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Parametres, VoixParametres } from '@/lib/types';

export type ParametresPatch = Omit<Partial<Parametres>, 'voix'> & {
  voix?: Partial<VoixParametres>;
};

const DEFAULT_PARAMETRES: Parametres = {
  moteur: 'auto',
  modele: 'hydrogen',
  effort: 'medium',
  verbosite: 'normal',
  accent: '#22d3ee',
  voix: {
    active: true,
    nom: 'fr-FR-HenriNeural',
    moteur_tts: 'edge',
    tts_url: null,
    stt_modele: 'small',
    stt_beam_final: 5,
    declenchement: 'push',
    micro: null,
    rapide: true,
    modele_rapide: 'gemini-4-hydrogen-low',
    llm_rapide: 'agy',
    llm_local_url: 'http://127.0.0.1:1234/v1',
    llm_local_modele: '',
    mode: 'local',
    web_service: 'chatgpt',
    web_navigateur: 'defaut',
  },
  raccourci: 'ctrl+space',
  memoire_auto: true,
};

/**
 * Applique la couleur d'accent sur la variable CSS racine `--accent`.
 *
 * @param accent - Code hexadécimal valide (ex. `#22d3ee`).
 */
export function applyAccentTheme(accent: string): void {
  if (typeof document === 'undefined') return;
  if (/^#[0-9a-fA-F]{6}$/.test(accent)) {
    document.documentElement.style.setProperty('--accent', accent);
  }
}

interface ParametresState {
  parametres: Parametres;
  loaded: boolean;
  saving: boolean;
  fetchParametres: () => Promise<void>;
  updateParametres: (partiel: ParametresPatch) => Promise<Parametres>;
}

/**
 * Store des paramètres globaux (`RUNTIME/parametres.json`), avec synchronisation CSS `--accent`.
 */
export const useParametres = create<ParametresState>()((set, get) => ({
  parametres: DEFAULT_PARAMETRES,
  loaded: false,
  saving: false,

  fetchParametres: async () => {
    try {
      const data = await api.get<Partial<Parametres>>('/api/parametres', { silent: true });
      const merged: Parametres = {
        ...DEFAULT_PARAMETRES,
        ...data,
        voix: {
          ...DEFAULT_PARAMETRES.voix,
          ...(data?.voix ?? {}),
        },
      };
      applyAccentTheme(merged.accent);
      set({ parametres: merged, loaded: true });
    } catch {
      applyAccentTheme(DEFAULT_PARAMETRES.accent);
      set({ loaded: true });
    }
  },

  updateParametres: async (partiel) => {
    const current = get().parametres;
    const optimistic: Parametres = {
      ...current,
      ...partiel,
      voix: {
        ...current.voix,
        ...(partiel.voix ?? {}),
      },
    };
    applyAccentTheme(optimistic.accent);
    set({ parametres: optimistic, saving: true });
    try {
      const saved = await api.put<Parametres>('/api/parametres', partiel);
      const merged: Parametres = {
        ...optimistic,
        ...(saved ?? {}),
        voix: {
          ...optimistic.voix,
          ...(saved?.voix ?? {}),
        },
      };
      applyAccentTheme(merged.accent);
      set({ parametres: merged, saving: false });
      return merged;
    } catch (err) {
      applyAccentTheme(current.accent);
      set({ parametres: current, saving: false });
      throw err;
    }
  },
}));
