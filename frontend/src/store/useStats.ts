import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Stats } from '@/lib/types';

interface StatsState {
  stats: Stats | null;
  loading: boolean;
  lastRoutineEvent: { id: string; statut: string; run_id?: number } | null;
  fetchStats: () => Promise<void>;
  setLastRoutineEvent: (evt: { id: string; statut: string; run_id?: number }) => void;
}

/**
 * Store des statistiques globales (`GET /api/stats`) et du dernier événement `routine_etat`.
 */
export const useStats = create<StatsState>()((set) => ({
  stats: null,
  loading: false,
  lastRoutineEvent: null,
  fetchStats: async () => {
    set({ loading: true });
    try {
      const stats = await api.get<Stats>('/api/stats', { silent: true });
      set({ stats: stats ?? null, loading: false });
    } catch {
      set({ loading: false });
    }
  },
  setLastRoutineEvent: (lastRoutineEvent) => set({ lastRoutineEvent }),
}));
