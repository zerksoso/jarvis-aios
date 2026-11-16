import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Agent, AgentEtat } from '@/lib/types';

interface AgentsState {
  agents: Agent[];
  loading: boolean;
  error: string | null;
  fetchAgents: () => Promise<void>;
  setAgentEtat: (id: string, etat: AgentEtat) => void;
  upsertAgent: (agent: Agent) => void;
  removeAgent: (id: string) => void;
}

/**
 * Store des agents AIOS et de leurs états d'exécution temps réel (`agent_etat`).
 */
export const useAgents = create<AgentsState>()((set) => ({
  agents: [],
  loading: false,
  error: null,
  fetchAgents: async () => {
    set({ loading: true, error: null });
    try {
      const agents = await api.get<Agent[]>('/api/agents', { silent: true });
      set({ agents: Array.isArray(agents) ? agents : [], loading: false });
    } catch (err) {
      set({
        loading: false,
        error: err instanceof Error ? err.message : 'Erreur de chargement des agents',
      });
    }
  },
  setAgentEtat: (id, etat) =>
    set((state) => ({
      agents: state.agents.map((agent) =>
        agent.id === id
          ? { ...agent, avatar: { ...agent.avatar, etat } }
          : agent,
      ),
    })),
  upsertAgent: (agent) =>
    set((state) => {
      const exists = state.agents.some((a) => a.id === agent.id);
      return {
        agents: exists
          ? state.agents.map((a) => (a.id === agent.id ? agent : a))
          : [...state.agents, agent],
      };
    }),
  removeAgent: (id) =>
    set((state) => ({
      agents: state.agents.filter((a) => a.id !== id),
    })),
}));
