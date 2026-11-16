import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Tache } from '@/lib/types';

interface TachesState {
  taches: Tache[];
  loading: boolean;
  fetchTaches: (date?: string) => Promise<void>;
  addTache: (titre: string, date?: string) => Promise<void>;
  toggleTache: (id: number, fait: boolean) => Promise<void>;
  deleteTache: (id: number) => Promise<void>;
}

/**
 * Store des tâches du jour synchronisé avec `GET /api/taches` et l'événement `taches_maj`.
 */
export const useTaches = create<TachesState>()((set, get) => ({
  taches: [],
  loading: false,
  fetchTaches: async (date) => {
    set({ loading: true });
    try {
      const qs = date ? `?date=${encodeURIComponent(date)}` : '';
      const rows = await api.get<Tache[]>(`/api/taches${qs}`, { silent: true });
      set({ taches: Array.isArray(rows) ? rows : [], loading: false });
    } catch {
      set({ loading: false });
    }
  },
  addTache: async (titre, date) => {
    const clean = titre.trim();
    if (!clean) return;
    await api.post('/api/taches', { titre: clean, date });
    await get().fetchTaches(date);
  },
  toggleTache: async (id, fait) => {
    set((state) => ({
      taches: state.taches.map((t) => (t.id === id ? { ...t, fait } : t)),
    }));
    await api.patch(`/api/taches/${id}`, { fait });
  },
  deleteTache: async (id) => {
    set((state) => ({
      taches: state.taches.filter((t) => t.id !== id),
    }));
    await api.del(`/api/taches/${id}`);
  },
}));
