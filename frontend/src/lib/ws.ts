import { getToken } from './api';
import type { WSEnvelope } from './types';

/** État observable de la liaison WebSocket avec le backend Jarvis. */
export type WSStatus = 'deconnecte' | 'connexion' | 'connecte';

export type WSEventHandler = (data: Record<string, unknown>, envelope: WSEnvelope) => void;
export type WSStatusHandler = (status: WSStatus) => void;

const INITIAL_RETRY_MS = 1000;
const MAX_RETRY_MS = 10000;
const PING_INTERVAL_MS = 25000;

/**
 * Gestionnaire singleton de la connexion WebSocket unique côté interface.
 * Assure la reconnexion exponentielle (1 s → 10 s), le keep-alive `ping` (25 s)
 * et l'abonnement par type d'événement.
 */
class JarvisWebSocket {
  private socket: WebSocket | null = null;
  private status: WSStatus = 'deconnecte';
  private retryDelayMs = INITIAL_RETRY_MS;
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private shouldReconnect = false;
  private refCount = 0;

  private listeners = new Map<string, Set<WSEventHandler>>();
  private statusListeners = new Set<WSStatusHandler>();

  /**
   * Retourne l'état courant de la connexion WebSocket.
   */
  public getStatus(): WSStatus {
    return this.status;
  }

  /**
   * Ouvre la connexion WebSocket si elle n'est pas déjà active.
   * Utilise un compteur de références pour résister au double montage de React StrictMode.
   *
   * Effets de bord : crée un objet `WebSocket`, démarre les timers ping/reconnexion.
   */
  public connect(): void {
    this.refCount += 1;
    this.shouldReconnect = true;
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }
    this.openSocket();
  }

  /**
   * Décrémente le compteur de références et ferme la connexion s'il n'y a plus de consommateur.
   */
  public disconnect(): void {
    this.refCount = Math.max(0, this.refCount - 1);
    if (this.refCount > 0) {
      return;
    }
    this.shouldReconnect = false;
    this.clearTimers();
    if (this.socket) {
      const sock = this.socket;
      this.socket = null;
      sock.onopen = null;
      sock.onmessage = null;
      sock.onerror = null;
      sock.onclose = null;
      sock.close();
    }
    this.updateStatus('deconnecte');
  }

  /**
   * Abonne un callback à un type d'événement serveur précis (ex. `chat_delta`, `quotas`).
   *
   * @param type - Nom exact du type d'événement WebSocket.
   * @param handler - Fonction appelée avec `envelope.data` et l'enveloppe complète.
   * @returns Fonction de désabonnement.
   */
  public subscribe(type: string, handler: WSEventHandler): () => void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set<WSEventHandler>();
      this.listeners.set(type, set);
    }
    set.add(handler);
    return () => {
      const current = this.listeners.get(type);
      if (!current) return;
      current.delete(handler);
      if (current.size === 0) {
        this.listeners.delete(type);
      }
    };
  }

  /**
   * Observe les changements d'état de connexion WebSocket.
   * Déclenche immédiatement le callback avec l'état courant.
   *
   * @param handler - Callback recevant le nouvel état.
   * @returns Fonction de désabonnement.
   */
  public subscribeStatus(handler: WSStatusHandler): () => void {
    this.statusListeners.add(handler);
    handler(this.status);
    return () => {
      this.statusListeners.delete(handler);
    };
  }

  /**
   * Envoie un message JSON au serveur si le socket est ouvert.
   *
   * @param type - Type de message client → serveur (`ping`, `approbation_reponse`, etc.).
   * @param data - Données associées au message.
   * @returns `true` si envoyé, `false` si le socket n'est pas prêt.
   */
  public send(type: string, data: Record<string, unknown> = {}): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      return false;
    }
    this.socket.send(
      JSON.stringify({
        type,
        data,
        ts: new Date().toISOString(),
      }),
    );
    return true;
  }

  private openSocket(): void {
    if (typeof window === 'undefined') return;
    this.clearReconnectTimer();

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const params = new URLSearchParams({ client: 'ui' });
    if (token) {
      params.set('token', token);
    }
    const url = `${protocol}//${window.location.host}/ws?${params.toString()}`;

    this.updateStatus('connexion');
    const sock = new WebSocket(url);
    this.socket = sock;

    sock.onopen = () => {
      if (this.socket !== sock) return;
      this.retryDelayMs = INITIAL_RETRY_MS;
      this.updateStatus('connecte');
      this.startPingLoop();
    };

    sock.onmessage = (event) => {
      if (typeof event.data !== 'string') return;
      try {
        const envelope = JSON.parse(event.data) as WSEnvelope;
        if (!envelope || typeof envelope.type !== 'string') return;
        const handlers = this.listeners.get(envelope.type);
        if (!handlers) return;
        const payload =
          envelope.data && typeof envelope.data === 'object' ? envelope.data : {};
        for (const fn of handlers) {
          fn(payload, envelope);
        }
      } catch {
        // Ignore les trames malformées pour ne pas casser la boucle WS.
      }
    };

    sock.onerror = () => {
      // L'événement onclose gère la reconnexion.
    };

    sock.onclose = () => {
      if (this.socket === sock) {
        this.socket = null;
      }
      this.stopPingLoop();
      this.updateStatus('deconnecte');
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    };
  }

  private startPingLoop(): void {
    this.stopPingLoop();
    this.pingTimer = window.setInterval(() => {
      this.send('ping');
    }, PING_INTERVAL_MS);
  }

  private stopPingLoop(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private clearTimers(): void {
    this.stopPingLoop();
    this.clearReconnectTimer();
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer();
    const delay = this.retryDelayMs;
    this.retryDelayMs = Math.min(MAX_RETRY_MS, this.retryDelayMs * 2);
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      if (this.shouldReconnect) {
        this.openSocket();
      }
    }, delay);
  }

  private updateStatus(next: WSStatus): void {
    if (this.status === next) return;
    this.status = next;
    for (const fn of this.statusListeners) {
      fn(next);
    }
  }
}

export const wsClient = new JarvisWebSocket();
