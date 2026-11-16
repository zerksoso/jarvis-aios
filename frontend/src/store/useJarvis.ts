import { create } from 'zustand';
import type { EtatJarvis } from '@/lib/types';
import type { WSStatus } from '@/lib/ws';

/** Dernière narration émise par le moteur Jarvis. */
export interface NarrationItem {
  texte: string;
  niveau: string;
  ts: string;
}

interface JarvisState {
  etat_jarvis: EtatJarvis;
  narration: NarrationItem | null;
  overlay_connecte: boolean;
  ws_statut: WSStatus;
  setEtatJarvis: (etat: EtatJarvis) => void;
  setNarration: (narration: NarrationItem) => void;
  setOverlayConnecte: (connecte: boolean) => void;
  setWsStatut: (statut: WSStatus) => void;
}

/**
 * Store global de l'état temps réel du cœur Jarvis, de la liaison WebSocket
 * et du statut de l'overlay bureau.
 */
export const useJarvis = create<JarvisState>()((set) => ({
  etat_jarvis: 'repos',
  narration: null,
  overlay_connecte: false,
  ws_statut: 'deconnecte',
  setEtatJarvis: (etat_jarvis) => set({ etat_jarvis }),
  setNarration: (narration) => set({ narration }),
  setOverlayConnecte: (overlay_connecte) => set({ overlay_connecte }),
  setWsStatut: (ws_statut) => set({ ws_statut }),
}));
