/**
 * Client API typé pour la gestion des mises à jour GitHub (`/api/maj/*`).
 * Réutilise le client HTTP centralisé `api` (jeton `X-Jarvis-Token`).
 */

import { api } from '@/lib/api';

/** Politique de vérification et d'installation des mises à jour. */
export type MajMode = 'demander' | 'auto' | 'jamais';

/** Informations sur la dernière version disponible sur GitHub. */
export interface MajDerniereInfo {
  version: string;
  notes: string;
  legere: boolean;
}

/** État global du système de mise à jour (`GET /api/maj/etat`). */
export interface MajEtat {
  version: string;
  derniere: MajDerniereInfo | null;
  disponible: boolean;
  verifie_le: string | null;
  mode: MajMode;
}

/** Réponse de lancement de l'installation d'une mise à jour (`POST /api/maj/installer`). */
export interface MajInstallerReponse {
  ok?: boolean;
  job_id?: string;
  statut?: string;
}

/** Progression courante du téléchargement et de l'application (`GET /api/maj/progression`). */
export interface MajProgression {
  etape: string;
  pourcent: number;
  erreur: string | null;
}

/** Réponse de modification du mode de mise à jour (`POST /api/maj/mode`). */
export interface MajModeReponse {
  ok?: boolean;
  mode?: MajMode;
}

/**
 * Récupère l'état courant des mises à jour (version installée, mise à jour disponible, mode).
 *
 * @param options - Options d'appel (`silent` masque les notifications globales d'erreur).
 * @returns État typé des mises à jour.
 */
export function getMajEtat(options?: { silent?: boolean }): Promise<MajEtat> {
  return api.get<MajEtat>('/api/maj/etat', options);
}

/**
 * Déclenche immédiatement une vérification de nouvelle version auprès de GitHub.
 *
 * @param options - Options d'appel HTTP.
 * @returns Nouvel état de mise à jour après vérification.
 */
export function verifierMaj(options?: { silent?: boolean }): Promise<MajEtat> {
  return api.post<MajEtat>('/api/maj/verifier', undefined, options);
}

/**
 * Démarre le téléchargement et l'installation de la mise à jour disponible.
 *
 * @param options - Options d'appel HTTP.
 * @returns Confirmation de démarrage du job d'installation.
 */
export function installerMaj(options?: {
  silent?: boolean;
}): Promise<MajInstallerReponse> {
  return api.post<MajInstallerReponse>('/api/maj/installer', undefined, options);
}

/**
 * Lit l'avancement courant de l'installation de la mise à jour.
 *
 * @param options - Options d'appel HTTP.
 * @returns Étape textuelle, pourcentage (0..100) et erreur éventuelle.
 */
export function getMajProgression(options?: {
  silent?: boolean;
}): Promise<MajProgression> {
  return api.get<MajProgression>('/api/maj/progression', options);
}

/**
 * Met à jour le mode automatique des mises à jour (`demander`, `auto`, `jamais`).
 *
 * @param mode - Nouveau mode choisi par l'utilisateur.
 * @param options - Options d'appel HTTP.
 * @returns Confirmation du serveur.
 */
export function setMajMode(
  mode: MajMode,
  options?: { silent?: boolean },
): Promise<MajModeReponse> {
  return api.post<MajModeReponse>('/api/maj/mode', { mode }, options);
}
