/**
 * Client API typé pour l'assistant de premier lancement (`/api/setup/*`).
 * Réutilise le client HTTP centralisé `api` (jeton `X-Jarvis-Token`).
 */

import { api } from '@/lib/api';

/** Identifiants des moteurs IA pris en charge par l'assistant de bienvenue. */
export type SetupMoteurId = 'agy' | 'claude' | 'codex';

/** État d'installation et de connexion d'un moteur IA renvoyé par `/api/setup/etat`. */
export interface SetupMoteurEtat {
  installe: boolean;
  connecte: boolean;
  chemin: string | null;
  version: string | null;
  installation_auto: boolean;
  page_telechargement: string | null;
}

/** État global du premier lancement (`GET /api/setup/etat`). */
export interface SetupEtat {
  termine: boolean;
  version: string;
  moteurs: {
    agy: SetupMoteurEtat;
    claude: SetupMoteurEtat;
    codex: SetupMoteurEtat;
    [key: string]: SetupMoteurEtat;
  };
  node: {
    installe: boolean;
  };
  profil_rempli: boolean;
}

/** Réponse de démarrage d'une installation de moteur (`POST /api/setup/installer/{moteur}`). */
export interface SetupInstallerReponse {
  job_id: string;
}

/** Statut et journal en direct d'un job d'installation (`GET /api/setup/jobs/{job_id}`). */
export interface SetupJobEtat {
  statut: 'en_cours' | 'ok' | 'erreur' | string;
  log: string[];
}

/** Réponse d'ouverture de la console de connexion (`POST /api/setup/connecter/{moteur}`). */
export interface SetupConnecterReponse {
  ok: boolean;
}

/** Données du profil utilisateur transmises lors de l'étape 3 (`POST /api/setup/profil`). */
export interface SetupProfilPayload {
  prenom?: string;
  activite?: string;
  secteur?: string;
  objectifs?: string;
  outils?: string;
  preferences?: string;
}

/** Réponse de sauvegarde du profil utilisateur (`POST /api/setup/profil`). */
export interface SetupProfilReponse {
  ok?: boolean;
  [key: string]: unknown;
}

/** Réponse de finalisation de l'assistant (`POST /api/setup/terminer`). */
export interface SetupTerminerReponse {
  ok?: boolean;
  termine?: boolean;
}

/**
 * Récupère l'état courant du premier lancement et des moteurs IA détectés.
 *
 * @param options - Options d'appel (`silent` masque les notifications globales d'erreur).
 * @returns État typé de l'assistant.
 */
export function getSetupEtat(options?: { silent?: boolean }): Promise<SetupEtat> {
  return api.get<SetupEtat>('/api/setup/etat', options);
}

/**
 * Démarre l'installation automatisée d'un moteur IA (`claude` ou `codex`).
 *
 * @param moteur - Identifiant du moteur à installer.
 * @param options - Options d'appel HTTP.
 * @returns Identifiant du job d'installation à sonder via `getSetupJob`.
 */
export function installerMoteur(
  moteur: SetupMoteurId,
  options?: { silent?: boolean },
): Promise<SetupInstallerReponse> {
  return api.post<SetupInstallerReponse>(
    `/api/setup/installer/${encodeURIComponent(moteur)}`,
    undefined,
    options,
  );
}

/**
 * Consulte l'avancement et les dernières lignes de journal d'un job d'installation.
 *
 * @param jobId - Identifiant du job renvoyé par `installerMoteur`.
 * @param options - Options d'appel HTTP.
 * @returns Statut courant (`en_cours`, `ok`, `erreur`) et lignes de journal.
 */
export function getSetupJob(
  jobId: string,
  options?: { silent?: boolean },
): Promise<SetupJobEtat> {
  return api.get<SetupJobEtat>(
    `/api/setup/jobs/${encodeURIComponent(jobId)}`,
    options,
  );
}

/**
 * Ouvre la fenêtre de connexion interactive du moteur côté système.
 *
 * @param moteur - Identifiant du moteur à connecter.
 * @param options - Options d'appel HTTP.
 * @returns Confirmation `{ ok }`.
 */
export function connecterMoteur(
  moteur: SetupMoteurId,
  options?: { silent?: boolean },
): Promise<SetupConnecterReponse> {
  return api.post<SetupConnecterReponse>(
    `/api/setup/connecter/${encodeURIComponent(moteur)}`,
    undefined,
    options,
  );
}

/**
 * Enregistre les informations de profil dans le second cerveau et les souvenirs initiaux.
 *
 * @param profil - Champs textuels optionnels saisis par l'utilisateur.
 * @param options - Options d'appel HTTP.
 * @returns Réponse de confirmation du backend.
 */
export function sauvegarderProfil(
  profil: SetupProfilPayload,
  options?: { silent?: boolean },
): Promise<SetupProfilReponse> {
  return api.post<SetupProfilReponse>('/api/setup/profil', profil, options);
}

/**
 * Marque l'assistant de premier lancement comme terminé (`premier_lancement.json.termine = true`).
 *
 * @param options - Options d'appel HTTP.
 * @returns Confirmation de clôture.
 */
export function terminerSetup(options?: {
  silent?: boolean;
}): Promise<SetupTerminerReponse> {
  return api.post<SetupTerminerReponse>('/api/setup/terminer', undefined, options);
}
