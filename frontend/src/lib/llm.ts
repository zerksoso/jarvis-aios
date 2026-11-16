import type {
  Effort,
  ModeleCatalogue,
  MoteurDisponibilite,
  MoteurId,
} from './types';

/** Options de moteur exposées dans l'interface. */
export const MOTEUR_OPTIONS: Array<{ id: MoteurId; label: string }> = [
  { id: 'auto', label: 'Auto' },
  { id: 'gemini', label: 'Gemini · Antigravity' },
  { id: 'claude', label: 'Claude' },
  { id: 'chatgpt', label: 'ChatGPT · GPT-OSS' },
];

/** Options d'effort de raisonnement exposées en français. */
export const EFFORT_OPTIONS: Array<{ id: Effort; label: string }> = [
  { id: 'low', label: 'Rapide' },
  { id: 'medium', label: 'Équilibré' },
  { id: 'high', label: 'Approfondi' },
  { id: 'xhigh', label: 'Maximum' },
];

/**
 * Filtre les modèles du catalogue en fonction du moteur UI sélectionné.
 *
 * @param modeles - Catalogue complet renvoyé par `/api/moteurs`.
 * @param moteur - Moteur sélectionné (`auto` retourne tout le catalogue).
 * @returns Modèles compatibles.
 */
export function filterModelsByMoteur(
  modeles: ModeleCatalogue[],
  moteur: MoteurId,
): ModeleCatalogue[] {
  if (moteur === 'auto') return modeles;
  return modeles.filter((m) => m.moteur_ui === moteur);
}

/**
 * Détermine si un modèle est indisponible et retourne sa raison éventuelle.
 *
 * @param model - Entrée du catalogue.
 * @param moteursStatut - Statuts de disponibilité des backends.
 */
export function getModelAvailability(
  model: ModeleCatalogue,
  moteursStatut: Record<string, MoteurDisponibilite>,
): { disponible: boolean; raison: string | null } {
  if (model.disponible === false) {
    return {
      disponible: false,
      raison: model.raison || 'Indisponible',
    };
  }
  const backendStatus = moteursStatut[model.backend];
  if (backendStatus && backendStatus.ok === false) {
    return {
      disponible: false,
      raison: backendStatus.raison || 'Moteur indisponible',
    };
  }
  return { disponible: true, raison: null };
}

/**
 * Formate un horodatage ISO en heure locale courte `HH:MM`.
 */
export function formatHeureFr(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return new Intl.DateTimeFormat('fr-FR', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}
