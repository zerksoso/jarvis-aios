import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Fusionne proprement des classes conditionnelles avec résolution des conflits Tailwind.
 *
 * @param inputs - Liste de classes CSS ou valeurs conditionnelles.
 * @returns Chaîne de classes fusionnée et dédupliquée.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
