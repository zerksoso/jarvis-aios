/**
 * Client HTTP centralisé vers l'API REST `/api/*` de Jarvis AIOS.
 * Gère le jeton d'authentification `X-Jarvis-Token` et la normalisation des erreurs.
 */

type ErrorHandler = (message: string, code?: string) => void;

let errorHandler: ErrorHandler | null = null;

/**
 * Erreur HTTP normalisée renvoyée par le client REST Jarvis.
 * Conserve le code d'erreur applicatif éventuel (ex. `aucun_moteur`) et le statut HTTP.
 */
export class ApiError extends Error {
  public readonly status?: number;
  public readonly code?: string;

  constructor(message: string, status?: number, code?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

/**
 * Enregistre un gestionnaire global appelé lors des erreurs API (ex. toast d'erreur).
 *
 * @param handler - Callback recevant le message d'erreur lisible, ou `null` pour désactiver.
 * @returns Rien.
 */
export function setApiErrorHandler(handler: ErrorHandler | null): void {
  errorHandler = handler;
}

/**
 * Récupère le jeton de session Jarvis depuis la balise meta injectée par le serveur,
 * avec repli sur `localStorage["jarvis-token"]` en mode développement Vite.
 *
 * @returns Le jeton d'authentification (chaîne vide si absent).
 */
export function getToken(): string {
  if (typeof document !== 'undefined') {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="jarvis-token"]');
    const content = meta?.getAttribute('content')?.trim() ?? '';
    if (content && content !== '__JARVIS_TOKEN__') {
      return content;
    }
  }
  if (typeof window !== 'undefined') {
    try {
      return window.localStorage.getItem('jarvis-token')?.trim() ?? '';
    } catch {
      return '';
    }
  }
  return '';
}

/**
 * Normalise un chemin API afin qu'il commence toujours par `/api`.
 *
 * @param chemin - Chemin relatif ou déjà préfixé (`/taches`, `/api/taches`).
 * @returns Chemin complet prêt pour `fetch`.
 */
function normalizeApiPath(chemin: string): string {
  if (chemin.startsWith('http://') || chemin.startsWith('https://')) {
    return chemin;
  }
  const withSlash = chemin.startsWith('/') ? chemin : `/${chemin}`;
  return withSlash.startsWith('/api') ? withSlash : `/api${withSlash}`;
}

/**
 * Exécute une requête HTTP vers l'API Jarvis avec en-tête `X-Jarvis-Token`.
 *
 * Effets de bord :
 * - Lit le jeton via `getToken()`.
 * - Appelle `errorHandler` si la requête échoue et que `notifyError` n'est pas désactivé.
 *
 * @param method - Méthode HTTP (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).
 * @param chemin - Chemin de la route API.
 * @param corps - Charge utile JSON ou `FormData` optionnelle.
 * @param options - Options d'appel (`silent` évite le toast automatique).
 * @returns Réponse JSON typée `T`.
 * @throws Error avec le champ `detail` du serveur ou un message HTTP lisible.
 */
async function request<T>(
  method: string,
  chemin: string,
  corps?: unknown,
  options?: { silent?: boolean },
): Promise<T> {
  const url = normalizeApiPath(chemin);
  const headers = new Headers();
  const token = getToken();
  if (token) {
    headers.set('X-Jarvis-Token', token);
  }

  let body: BodyInit | undefined;
  if (corps !== undefined) {
    if (corps instanceof FormData) {
      body = corps;
    } else {
      headers.set('Content-Type', 'application/json');
      body = JSON.stringify(corps);
    }
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
    });
  } catch (err) {
    const message =
      err instanceof Error ? err.message : 'Impossible de joindre le serveur Jarvis';
    if (!options?.silent && errorHandler) {
      errorHandler(message);
    }
    throw new Error(message);
  }

  if (!response.ok) {
    let detail = `Erreur HTTP ${response.status}`;
    let code: string | undefined;
    try {
      const data = (await response.json()) as {
        detail?: unknown;
        message?: unknown;
        code?: unknown;
      };
      if (typeof data?.detail === 'string' && data.detail.trim()) {
        detail = data.detail;
      } else if (typeof data?.message === 'string' && data.message.trim()) {
        detail = data.message;
      }
      if (typeof data?.code === 'string' && data.code.trim()) {
        code = data.code.trim();
      }
    } catch {
      // Réponse non JSON : on conserve le statut HTTP.
    }
    if (!options?.silent && errorHandler) {
      errorHandler(detail, code);
    }
    throw new ApiError(detail, response.status, code);
  }

  // Certaines routes DELETE/POST peuvent renvoyer 204 No Content
  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

/**
 * Façades typées pour appeler l'API REST Jarvis.
 */
export const api = {
  get: <T>(chemin: string, options?: { silent?: boolean }): Promise<T> =>
    request<T>('GET', chemin, undefined, options),
  post: <T>(chemin: string, corps?: unknown, options?: { silent?: boolean }): Promise<T> =>
    request<T>('POST', chemin, corps, options),
  put: <T>(chemin: string, corps?: unknown, options?: { silent?: boolean }): Promise<T> =>
    request<T>('PUT', chemin, corps, options),
  patch: <T>(chemin: string, corps?: unknown, options?: { silent?: boolean }): Promise<T> =>
    request<T>('PATCH', chemin, corps, options),
  del: <T>(chemin: string, corps?: unknown, options?: { silent?: boolean }): Promise<T> =>
    request<T>('DELETE', chemin, corps, options),
  upload: <T>(chemin: string, formData: FormData, options?: { silent?: boolean }): Promise<T> =>
    request<T>('POST', chemin, formData, options),
};
