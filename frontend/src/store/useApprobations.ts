import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Approbation } from '@/lib/types';

interface ApprobationsState {
  enAttente: Approbation[];
  submittingId: string | null;
  fetchApprobations: () => Promise<void>;
  pushApprobation: (approbation: Approbation) => void;
  resolveApprobation: (id: string, statut?: string) => void;
  decideApprobation: (id: string, decision: 'oui' | 'non') => Promise<void>;
}

/**
 * Store des demandes d'approbation bloquantes (`approbation_demande` / `approbation_resultat`).
 */
export const useApprobations = create<ApprobationsState>()((set) => ({
  enAttente: [],
  submittingId: null,

  fetchApprobations: async () => {
    try {
      const rows = await api.get<Approbation[]>(
        '/api/approbations?statut=en_attente',
        { silent: true },
      );
      set({ enAttente: Array.isArray(rows) ? rows : [] });
    } catch {
      // Silencieux à l'initialisation si aucune approbation n'est disponible.
    }
  },

  pushApprobation: (approbation) =>
    set((state) => {
      if (state.enAttente.some((a) => a.id === approbation.id)) {
        return state;
      }
      return { enAttente: [...state.enAttente, approbation] };
    }),

  resolveApprobation: (id) =>
    set((state) => ({
      enAttente: state.enAttente.filter((a) => a.id !== id),
      submittingId: state.submittingId === id ? null : state.submittingId,
    })),

  decideApprobation: async (id, decision) => {
    set({ submittingId: id });
    try {
      await api.post(`/api/approbations/${encodeURIComponent(id)}`, { decision });
      set((state) => ({
        enAttente: state.enAttente.filter((a) => a.id !== id),
        submittingId: null,
      }));
    } catch (err) {
      set({ submittingId: null });
      throw err;
    }
  },
}));
