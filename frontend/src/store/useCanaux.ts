import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Canal } from '@/lib/types';

interface CanauxState {
  canaux: Canal[];
  loading: boolean;
  error: string | null;
  fetchCanaux: () => Promise<void>;
  upsertCanal: (canal: Canal) => void;
  removeCanal: (id: string) => void;
}

/**
 * Store des canaux multi-agents (`canaux/<id>/canal.json`).
 */
export const useCanaux = create<CanauxState>()((set) => ({
  canaux: [],
  loading: false,
  error: null,
  fetchCanaux: async () => {
    set({ loading: true, error: null });
    try {
      const canaux = await api.get<Canal[]>('/api/canaux', { silent: true });
      set({ canaux: Array.isArray(canaux) ? canaux : [], loading: false });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : 'Erreur de chargement des canaux',
      });
    }
  },
  upsertCanal: (canal) =>
    set((state) => {
      const exists = state.canaux.some((c) => c.id === canal.id);
      return {
        canaux: exists
          ? state.canaux.map((c) => (c.id === canal.id ? canal : c))
          : [...state.canaux, canal],
      };
    }),
  removeCanal: (id) =>
    set((state) => ({
      canaux: state.canaux.filter((c) => c.id !== id),
    })),
}));
