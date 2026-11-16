const JOURS_SEMAINE: Record<string, string> = {
  '0': 'dimanche',
  '1': 'lundi',
  '2': 'mardi',
  '3': 'mercredi',
  '4': 'jeudi',
  '5': 'vendredi',
  '6': 'samedi',
  '7': 'dimanche',
  mon: 'lundi',
  tue: 'mardi',
  wed: 'mercredi',
  thu: 'jeudi',
  fri: 'vendredi',
  sat: 'samedi',
  sun: 'dimanche',
};

function pad2(value: string): string {
  return value.padStart(2, '0');
}

/**
 * Traduit une expression cron à 5 champs (`min heure jour mois jour_sem`) en français lisible.
 *
 * @param cron - Expression crontab à 5 champs.
 * @returns Description lisible (ex. `Tous les jours à 06:00`).
 */
export function translateCronToFrench(cron: string): string {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) {
    return cron;
  }

  const [min, hour, dom, month, dow] = parts;

  // Toutes les N minutes
  if (
    min.startsWith('*/') &&
    hour === '*' &&
    dom === '*' &&
    month === '*' &&
    dow === '*'
  ) {
    const step = Number(min.slice(2));
    if (Number.isFinite(step) && step > 0) {
      return step === 1 ? 'Toutes les minutes' : `Toutes les ${step} minutes`;
    }
  }

  // Toutes les heures à la minute M
  if (
    /^\d+$/.test(min) &&
    hour === '*' &&
    dom === '*' &&
    month === '*' &&
    dow === '*'
  ) {
    return `Toutes les heures à :${pad2(min)}`;
  }

  // Toutes les N heures
  if (
    /^\d+$/.test(min) &&
    hour.startsWith('*/') &&
    dom === '*' &&
    month === '*' &&
    dow === '*'
  ) {
    const step = Number(hour.slice(2));
    if (Number.isFinite(step) && step > 0) {
      return `Toutes les ${step} h à :${pad2(min)}`;
    }
  }

  // Heure fixe quotidienne / hebdomadaire / mensuelle
  if (/^\d+$/.test(min) && /^\d+$/.test(hour)) {
    const timeStr = `${pad2(hour)}:${pad2(min)}`;
    if (dom === '*' && month === '*' && dow === '*') {
      return `Tous les jours à ${timeStr}`;
    }
    if (dom === '*' && month === '*' && dow === '1-5') {
      return `Du lundi au vendredi à ${timeStr}`;
    }
    if (dom === '*' && month === '*' && JOURS_SEMAINE[dow.toLowerCase()]) {
      return `Chaque ${JOURS_SEMAINE[dow.toLowerCase()]} à ${timeStr}`;
    }
    if (/^\d+$/.test(dom) && month === '*' && dow === '*') {
      return `Le ${dom} de chaque mois à ${timeStr}`;
    }
  }

  return cron;
}
