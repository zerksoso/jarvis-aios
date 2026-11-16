import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Carte } from '@/lib/types';

interface CartesState {
  cartes: Carte[];
  loading: boolean;
  fetchCartes: (epinglees?: boolean) => Promise<void>;
  addCarte: (carte: Carte) => void;
  pinCarte: (id: string, epinglee: boolean) => Promise<void>;
  deleteCarte: (id: string) => Promise<void>;
}

/**
 * Store des cartes d'information épinglées dans le tableau de bord.
 */
export const useCartes = create<CartesState>()((set) => ({
  cartes: [],
  loading: false,
  fetchCartes: async (epinglees = true) => {
    set({ loading: true });
    try {
      const qs = epinglees ? '?epinglees=1' : '';
      const rows = await api.get<Carte[]>(`/api/cartes${qs}`, { silent: true });
      set({ cartes: Array.isArray(rows) ? rows : [], loading: false });
    } catch {
      set({ loading: false });
    }
  },
  addCarte: (carte) =>
    set((state) => {
      const exists = state.cartes.some((c) => c.id === carte.id);
      return {
        cartes: exists
          ? state.cartes.map((c) => (c.id === carte.id ? carte : c))
          : [carte, ...state.cartes],
      };
    }),
  pinCarte: async (id, epinglee) => {
    await api.post(`/api/cartes/${encodeURIComponent(id)}/epingler`, { epinglee });
    set((state) => ({
      cartes: epinglee
        ? state.cartes.map((c) => (c.id === id ? { ...c, epinglee } : c))
        : state.cartes.filter((c) => c.id !== id),
    }));
  },
  deleteCarte: async (id) => {
    await api.del(`/api/cartes/${encodeURIComponent(id)}`);
    set((state) => ({
      cartes: state.cartes.filter((c) => c.id !== id),
    }));
  },
}));
