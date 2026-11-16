import { create } from 'zustand';
import type { ToastAction, ToastItem } from '@/lib/types';

interface ToastsState {
  toasts: ToastItem[];
  addToast: (
    texte: string,
    niveau?: ToastItem['niveau'],
    action?: ToastAction,
  ) => void;
  removeToast: (id: string) => void;
}

const MAX_TOASTS = 5;
const AUTO_DISMISS_MS = 4500;
const ACTION_DISMISS_MS = 9000;

/**
 * Store des notifications toast affichées en surimpression de la coque.
 * Limite la pile à 5 éléments et programme leur fermeture automatique.
 */
export const useToasts = create<ToastsState>()((set) => ({
  toasts: [],
  addToast: (texte, niveau = 'info', action) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    set((state) => ({
      toasts: [
        ...state.toasts.slice(-(MAX_TOASTS - 1)),
        { id, texte, niveau, action },
      ],
    }));
    if (typeof window !== 'undefined') {
      window.setTimeout(
        () => {
          set((state) => ({
            toasts: state.toasts.filter((t) => t.id !== id),
          }));
        },
        action ? ACTION_DISMISS_MS : AUTO_DISMISS_MS,
      );
    }
  },
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
}));
