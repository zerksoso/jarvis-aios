import { create } from 'zustand';
import { api } from '@/lib/api';
import type { Effort, Message, MessageMeta, MoteurId } from '@/lib/types';
import { useToasts } from '@/store/useToasts';

/** Tableau vide partagé : évite de renvoyer un nouveau tableau à chaque rendu (boucle infinie zustand). */
export const AUCUN_MESSAGE: Message[] = [];

/** État d'un message assistant en cours de diffusion WebSocket. */
export interface StreamingMessage {
  auteur: string;
  contenu: string;
}

export interface SendMessagePayload {
  cible: 'agent' | 'canal';
  id: string;
  message: string;
  moteur?: MoteurId;
  modele?: string;
  effort?: Effort;
  isVoice?: boolean;
}

interface ChatsState {
  messagesByConv: Record<string, Message[]>;
  streamingByConv: Record<string, StreamingMessage | null>;
  typingByConv: Record<string, string | null>;
  loadingByConv: Record<string, boolean>;
  sendingByConv: Record<string, boolean>;
  pendingVoiceConvIds: Record<string, boolean>;

  loadMessages: (type: 'agent' | 'canal', cibleId: string) => Promise<void>;
  sendMessage: (payload: SendMessagePayload) => Promise<string>;
  consumeVoicePending: (conversationId: string) => boolean;

  handleChatDebut: (conversationId: string, auteur: string) => void;
  handleChatDelta: (conversationId: string, auteur: string, delta: string) => void;
  handleChatFin: (payload: {
    conversation_id: string;
    auteur: string;
    message_id?: number | string;
    contenu: string;
    moteur?: string;
    modele?: string;
    bascules?: string[];
    synthese?: boolean;
  }) => void;
  handleChatErreur: (conversationId: string, auteur: string, erreur: string) => void;
}

/**
 * Construit l'identifiant canonique de conversation utilisé par SQLite et le WebSocket.
 */
export function getConversationId(type: 'agent' | 'canal', cibleId: string): string {
  return `${type}:${cibleId}`;
}

/**
 * Normalise le champ `meta` d'un message (chaîne JSON SQLite ou objet déjà parsé).
 */
export function parseMessageMeta(meta: Message['meta']): MessageMeta {
  if (!meta) return {};
  if (typeof meta === 'string') {
    try {
      const parsed = JSON.parse(meta) as MessageMeta;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }
  return meta;
}

/**
 * Store des conversations agents et canaux, incluant le streaming temps réel.
 */
export const useChats = create<ChatsState>()((set, get) => ({
  messagesByConv: {},
  streamingByConv: {},
  typingByConv: {},
  loadingByConv: {},
  sendingByConv: {},
  pendingVoiceConvIds: {},

  loadMessages: async (type, cibleId) => {
    const convId = getConversationId(type, cibleId);
    set((state) => ({
      loadingByConv: { ...state.loadingByConv, [convId]: true },
    }));
    try {
      const rows = await api.get<Message[]>(
        `/api/conversations/${encodeURIComponent(type)}/${encodeURIComponent(cibleId)}/messages?limite=100`,
        { silent: true },
      );
      set((state) => ({
        messagesByConv: {
          ...state.messagesByConv,
          [convId]: Array.isArray(rows) ? rows : [],
        },
        loadingByConv: { ...state.loadingByConv, [convId]: false },
      }));
    } catch {
      set((state) => ({
        loadingByConv: { ...state.loadingByConv, [convId]: false },
      }));
    }
  },

  sendMessage: async (payload) => {
    const convId = getConversationId(payload.cible, payload.id);
    const optimisticMsg: Message = {
      id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      conversation_id: convId,
      role: 'user',
      auteur: 'utilisateur',
      contenu: payload.message,
      cree_le: new Date().toISOString(),
    };

    set((state) => ({
      messagesByConv: {
        ...state.messagesByConv,
        [convId]: [...(state.messagesByConv[convId] ?? []), optimisticMsg],
      },
      sendingByConv: { ...state.sendingByConv, [convId]: true },
      pendingVoiceConvIds: payload.isVoice
        ? { ...state.pendingVoiceConvIds, [convId]: true }
        : state.pendingVoiceConvIds,
    }));

    try {
      const res = await api.post<{
        conversation_id: string;
        code?: string;
        message?: string;
        erreur?: string;
      }>('/api/chat', {
        cible: payload.cible,
        id: payload.id,
        message: payload.message,
        moteur: payload.moteur,
        modele: payload.modele || undefined,
        effort: payload.effort,
      });
      if (res?.code === 'aucun_moteur') {
        useToasts.getState().addToast(
          res.message ||
            res.erreur ||
            "Aucun moteur d'IA n'est encore configuré.",
          'avertissement',
          { label: 'Ouvrir l’assistant', to: '/bienvenue' },
        );
      }
      set((state) => ({
        sendingByConv: { ...state.sendingByConv, [convId]: false },
      }));
      return res?.conversation_id ?? convId;
    } catch (err) {
      set((state) => ({
        sendingByConv: { ...state.sendingByConv, [convId]: false },
        pendingVoiceConvIds: { ...state.pendingVoiceConvIds, [convId]: false },
      }));
      throw err;
    }
  },

  consumeVoicePending: (conversationId) => {
    const isPending = Boolean(get().pendingVoiceConvIds[conversationId]);
    if (isPending) {
      set((state) => ({
        pendingVoiceConvIds: { ...state.pendingVoiceConvIds, [conversationId]: false },
      }));
    }
    return isPending;
  },

  handleChatDebut: (conversationId, auteur) =>
    set((state) => ({
      typingByConv: { ...state.typingByConv, [conversationId]: auteur },
      streamingByConv: {
        ...state.streamingByConv,
        [conversationId]: { auteur, contenu: '' },
      },
    })),

  handleChatDelta: (conversationId, auteur, delta) =>
    set((state) => {
      const current = state.streamingByConv[conversationId];
      const nextContent =
        current && current.auteur === auteur ? current.contenu + delta : delta;
      return {
        typingByConv: { ...state.typingByConv, [conversationId]: auteur },
        streamingByConv: {
          ...state.streamingByConv,
          [conversationId]: { auteur, contenu: nextContent },
        },
      };
    }),

  handleChatFin: (payload) =>
    set((state) => {
      const convId = payload.conversation_id;
      const existing = state.messagesByConv[convId] ?? [];
      const msgId = payload.message_id ?? `ws-${Date.now()}`;
      const alreadyPresent = existing.some((m) => String(m.id) === String(msgId));
      const newMessage: Message = {
        id: msgId,
        conversation_id: convId,
        role: 'assistant',
        auteur: payload.auteur,
        contenu: payload.contenu,
        meta: {
          moteur: payload.moteur,
          modele: payload.modele,
          bascules: payload.bascules,
          synthese: payload.synthese,
        },
        cree_le: new Date().toISOString(),
      };
      return {
        messagesByConv: {
          ...state.messagesByConv,
          [convId]: alreadyPresent ? existing : [...existing, newMessage],
        },
        streamingByConv: { ...state.streamingByConv, [convId]: null },
        typingByConv: { ...state.typingByConv, [convId]: null },
      };
    }),

  handleChatErreur: (conversationId, auteur, erreur) =>
    set((state) => {
      const existing = state.messagesByConv[conversationId] ?? [];
      const errMsg: Message = {
        id: `err-${Date.now()}`,
        conversation_id: conversationId,
        role: 'system',
        auteur: auteur || 'système',
        contenu: `Erreur : ${erreur}`,
        cree_le: new Date().toISOString(),
      };
      return {
        messagesByConv: {
          ...state.messagesByConv,
          [conversationId]: [...existing, errMsg],
        },
        streamingByConv: { ...state.streamingByConv, [conversationId]: null },
        typingByConv: { ...state.typingByConv, [conversationId]: null },
      };
    }),
}));
