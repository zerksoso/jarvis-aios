import { create } from 'zustand';
import { api } from '@/lib/api';
import type {
  ModeleCatalogue,
  MoteurDisponibilite,
  MoteursPayload,
} from '@/lib/types';

interface MoteursState {
  modeles: ModeleCatalogue[];
  moteursStatut: Record<string, MoteurDisponibilite>;
  loading: boolean;
  fetchMoteurs: () => Promise<void>;
}

/**
 * Normalise la réponse de `GET /api/moteurs` (tableau direct ou objet `{modeles, moteurs}`).
 */
function normalizeMoteursPayload(
  payload: ModeleCatalogue[] | MoteursPayload | null | undefined,
): {
  modeles: ModeleCatalogue[];
  moteursStatut: Record<string, MoteurDisponibilite>;
} {
  if (!payload) {
    return { modeles: [], moteursStatut: {} };
  }
  if (Array.isArray(payload)) {
    return { modeles: payload, moteursStatut: {} };
  }
  const list = Array.isArray(payload.modeles)
    ? payload.modeles
    : Array.isArray(payload.catalogue)
      ? payload.catalogue
      : [];
  const statuts =
    payload.moteurs && typeof payload.moteurs === 'object' ? payload.moteurs : {};
  return { modeles: list, moteursStatut: statuts };
}

/**
 * Store du catalogue de modèles LLM et de la disponibilité temps réel des backends.
 */
export const useMoteurs = create<MoteursState>()((set) => ({
  modeles: [],
  moteursStatut: {},
  loading: false,
  fetchMoteurs: async () => {
    set({ loading: true });
    try {
      const data = await api.get<ModeleCatalogue[] | MoteursPayload>(
        '/api/moteurs',
        { silent: true },
      );
      const { modeles, moteursStatut } = normalizeMoteursPayload(data);
      set({ modeles, moteursStatut, loading: false });
    } catch {
      set({ loading: false });
    }
  },
}));
