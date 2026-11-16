import { CanvasTexture, LinearFilter, Sprite, SpriteMaterial, SRGBColorSpace } from 'three';
import type { CerveauNoeud } from '@/lib/types';

/** Segment d'extrait de recherche FTS5 après découpage sûr des balises `<mark>`. */
export interface MarkedSegment {
  text: string;
  mark: boolean;
}

/**
 * Calcule un hachage 32 bits non signé déterministe (FNV-1a) pour une chaîne donnée.
 * Utilisé pour positionner de façon stable les nœuds sur l'orbe 3D sans dérive entre rechargements.
 *
 * @param input - Identifiant unique de la note ou graine dérivée.
 * @returns Entier non signé sur 32 bits.
 */
export function hashString(input: string): number {
  let hash = 2166136261;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

/**
 * Normalise un texte en minuscules sans accents ni espaces superflus,
 * à l'identique du backend `backend/brain/parser.py::normaliser_texte`.
 *
 * @param texte - Chaîne brute à normaliser.
 * @returns Chaîne normalisée pour comparaison insensible à la casse et aux diacritiques.
 */
export function normaliserTexte(texte: string): string {
  return texte
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, ' ');
}

/**
 * Résout une cible brute de lien wiki (`[[Cible]]`, `[[Cible|alias]]`, `[[Cible#section]]`)
 * vers l'identifiant canonique de la note dans le graphe, ou `null` si introuvable.
 *
 * @param rawTarget - Contenu brut entre `[[` et `]]`.
 * @param noeuds - Liste complète des nœuds connus du graphe.
 * @returns Identifiant canonique de la note cible ou `null` (lien cassé).
 */
export function resolveWikiTarget(rawTarget: string, noeuds: CerveauNoeud[]): string | null {
  const withoutPipe = rawTarget.split('|')[0] ?? '';
  const withoutHash = (withoutPipe.split('#')[0] ?? '').trim();
  const clean = withoutHash.toLowerCase().endsWith('.md')
    ? withoutHash.slice(0, -3).trim()
    : withoutHash;
  const cle = normaliserTexte(clean.replace(/\\/g, '/'));
  if (!cle) return null;

  for (const n of noeuds) {
    if (normaliserTexte(n.id) === cle) return n.id;
  }
  for (const n of noeuds) {
    const fileName = n.id.split('/').pop() ?? n.id;
    if (normaliserTexte(fileName) === cle) return n.id;
  }
  for (const n of noeuds) {
    if (normaliserTexte(n.titre) === cle) return n.id;
  }
  return null;
}

/**
 * Retire l'en-tête YAML (`--- ... ---`) au début d'une note Markdown pour éviter
 * qu'il ne s'affiche comme du texte brut dans le panneau de lecture.
 *
 * @param markdown - Contenu Markdown complet renvoyé par `/api/cerveau/note`.
 * @returns Corps Markdown sans le bloc frontmatter initial.
 */
export function stripFrontmatter(markdown: string): string {
  return markdown.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '');
}

/**
 * Transforme les liens wiki `[[Cible]]` / `[[Cible|Alias]]` / `[[Cible#Ancre]]`
 * hors blocs de code en liens Markdown internes `[label](jarvis-note://cible)`
 * afin de les rendre cliquables via `react-markdown`.
 *
 * @param markdown - Corps Markdown (frontmatter retiré).
 * @returns Markdown prêt pour `ReactMarkdown`.
 */
export function preprocessWikiLinks(markdown: string): string {
  // Protège les blocs ```...``` et le code inline `...` pour ne pas altérer d'éventuels exemples.
  const segments = markdown.split(/(```[\s\S]*?```|`[^`\n]+`)/g);
  return segments
    .map((segment, idx) => {
      if (idx % 2 === 1) return segment;
      return segment.replace(/\[\[([^\]\n]+)\]\]/g, (_full, inner: string) => {
        const trimmed = inner.trim();
        if (!trimmed) return _full;
        const pipeIdx = trimmed.indexOf('|');
        const targetPart = (pipeIdx >= 0 ? trimmed.slice(0, pipeIdx) : trimmed).trim();
        const aliasPart = (pipeIdx >= 0 ? trimmed.slice(pipeIdx + 1) : targetPart).trim();
        const safeLabel = aliasPart.replace(/[\[\]]/g, '\\$&');
        return `[${safeLabel}](jarvis-note://${encodeURIComponent(targetPart)})`;
      });
    })
    .join('');
}

/**
 * Découpe de façon sûre un extrait FTS5 contenant uniquement des balises `<mark>…</mark>`.
 * Aucun HTML arbitraire n'est interprété : tout texte hors/dans `<mark>` reste du texte React échappé.
 *
 * @param extrait - Chaîne retournée par `snippet(notes_fts, -1, '<mark>', '</mark>', ...)`.
 * @returns Liste ordonnée de segments `{ text, mark }`.
 */
export function parseSearchSnippet(extrait: string): MarkedSegment[] {
  if (!extrait) return [];
  const parts = extrait.split(/(<\/?mark>)/gi);
  const segments: MarkedSegment[] = [];
  let inMark = false;

  for (const part of parts) {
    if (!part) continue;
    const lower = part.toLowerCase();
    if (lower === '<mark>') {
      inMark = true;
      continue;
    }
    if (lower === '</mark>') {
      inMark = false;
      continue;
    }
    segments.push({ text: part, mark: inMark });
  }
  return segments;
}

/**
 * Interpole linéairement deux couleurs hexadécimales `#rrggbb`.
 * Utilisé pour atténuer les nœuds/liens hors recherche vers la couleur de fond `#05070d`
 * sans produire de chaînes `rgba(...)` incompatibles avec certains parseurs Three.js.
 *
 * @param hexA - Couleur source au format `#rrggbb`.
 * @param hexB - Couleur cible au format `#rrggbb`.
 * @param t - Facteur d'interpolation dans `[0, 1]` (`0` → `hexA`, `1` → `hexB`).
 * @returns Couleur hexadécimale résultante `#rrggbb`.
 */
export function mixHex(hexA: string, hexB: string, t: number): string {
  const parse = (hex: string): [number, number, number] => {
    const clean = hex.replace('#', '');
    if (clean.length !== 6) return [148, 163, 184];
    const r = Number.parseInt(clean.slice(0, 2), 16);
    const g = Number.parseInt(clean.slice(2, 4), 16);
    const b = Number.parseInt(clean.slice(4, 6), 16);
    if (Number.isNaN(r) || Number.isNaN(g) || Number.isNaN(b)) return [148, 163, 184];
    return [r, g, b];
  };
  const clamped = Math.max(0, Math.min(1, t));
  const [r1, g1, b1] = parse(hexA);
  const [r2, g2, b2] = parse(hexB);
  const r = Math.round(r1 + (r2 - r1) * clamped);
  const g = Math.round(g1 + (g2 - g1) * clamped);
  const b = Math.round(b1 + (b2 - b1) * clamped);
  return `#${r.toString(16).padStart(2, '0')}${g.toString(16).padStart(2, '0')}${b.toString(16).padStart(2, '0')}`;
}

/**
 * Extrait l'identifiant d'une extrémité de lien, qu'elle soit encore une chaîne
 * ou déjà hydratée en objet nœud par `d3-force`.
 *
 * @param endpoint - Chaîne d'id ou objet contenant `id`.
 * @returns Identifiant sous forme de chaîne.
 */
export function getEndpointId(endpoint: unknown): string {
  if (typeof endpoint === 'string') return endpoint;
  if (typeof endpoint === 'number') return String(endpoint);
  if (endpoint && typeof endpoint === 'object' && 'id' in endpoint) {
    return String((endpoint as { id?: unknown }).id ?? '');
  }
  return '';
}

/**
 * Crée un sprite Three.js d'étiquette textuelle via un `<canvas>` 2D (sans dépendance `three-spritetext`).
 * Penser à libérer `sprite.material.map?.dispose()` et `sprite.material.dispose()` lors du retrait.
 *
 * @param text - Titre à afficher dans la pastille flottante.
 * @param borderColor - Couleur hexadécimale de liseré (branche du nœud).
 * @param worldHeight - Hauteur souhaitée du sprite dans l'espace 3D.
 * @returns Instance `THREE.Sprite` prête à être ajoutée à la scène.
 */
export function createLabelSprite(
  text: string,
  borderColor = '#22d3ee',
  worldHeight = 10,
): Sprite {
  const truncated = text.length > 38 ? `${text.slice(0, 35)}…` : text;
  const dpr = 2;
  const fontSize = 24;
  const padX = 20;
  const padY = 12;

  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    const emptyTex = new CanvasTexture(canvas);
    return new Sprite(new SpriteMaterial({ map: emptyTex, transparent: true }));
  }

  ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
  const metrics = ctx.measureText(truncated);
  const width = Math.ceil(metrics.width + padX * 2);
  const height = Math.ceil(fontSize + padY * 2);

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  ctx.scale(dpr, dpr);

  // Fond HUD translucide + liseré coloré de branche
  const radius = 10;
  ctx.beginPath();
  ctx.roundRect(1, 1, width - 2, height - 2, radius);
  ctx.fillStyle = 'rgba(5, 7, 13, 0.9)';
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = borderColor;
  ctx.stroke();

  ctx.font = `600 ${fontSize}px Inter, system-ui, sans-serif`;
  ctx.fillStyle = '#f8fafc';
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';
  ctx.fillText(truncated, width / 2, height / 2 + 1);

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.minFilter = LinearFilter;
  texture.magFilter = LinearFilter;
  texture.needsUpdate = true;

  const material = new SpriteMaterial({
    map: texture,
    transparent: true,
    depthWrite: false,
    depthTest: false,
  });

  const sprite = new Sprite(material);
  sprite.renderOrder = 999;
  const aspect = width / height;
  sprite.scale.set(worldHeight * aspect, worldHeight, 1);
  return sprite;
}

/**
 * Libère proprement les ressources GPU (`CanvasTexture` + `SpriteMaterial`) d'un sprite texte.
 *
 * @param sprite - Sprite précédemment créé avec `createLabelSprite`.
 */
export function disposeLabelSprite(sprite: Sprite | null | undefined): void {
  if (!sprite) return;
  const mat = sprite.material;
  if (mat instanceof SpriteMaterial) {
    mat.map?.dispose();
    mat.dispose();
  }
}
