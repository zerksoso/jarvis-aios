import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Quotas } from '@/lib/types';

interface QuotasState {
  quotas: Quotas | null;
  loading: boolean;
  fetchQuotas: () => Promise<void>;
  setQuotas: (quotas: Quotas) => void;
}

/**
 * Store des quotas Antigravity et de l'estimation Claude Code CLI.
 */
export const useQuotas = create<QuotasState>()((set) => ({
  quotas: null,
  loading: false,
  fetchQuotas: async () => {
    set({ loading: true });
    try {
      const quotas = await api.get<Quotas>('/api/quotas', { silent: true });
      set({ quotas: quotas ?? null, loading: false });
    } catch {
      set({ loading: false });
    }
  },
  setQuotas: (quotas) => set({ quotas }),
}));
