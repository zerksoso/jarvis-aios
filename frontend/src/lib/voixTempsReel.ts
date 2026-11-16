import { getToken } from './api';

/**
 * États observables d'une session de conversation vocale quasi temps réel (`/ws/voix`).
 */
export type EtatConversationVocale =
  | 'inactif'
  | 'connexion'
  | 'ecoute'
  | 'parole'
  | 'reflexion'
  | 'reponse'
  | 'erreur';

/**
 * Instantané immuable de l'état de la conversation vocale exposé à l'interface React.
 */
export interface ConversationVocaleSnapshot {
  etat: EtatConversationVocale;
  partiel: string;
  final: string;
  reponse: string;
  erreur: string | null;
  erreurCode: string | null;
}

/**
 * Options d'ouverture d'une session vocale temps réel.
 */
export interface ConversationVocaleOptions {
  agent?: string;
  voix?: string;
}

export type ConversationVocaleListener = (
  snapshot: ConversationVocaleSnapshot,
) => void;

interface PendingAudioMeta {
  seq: number;
  mime: string;
}

const INITIAL_SNAPSHOT: ConversationVocaleSnapshot = {
  etat: 'inactif',
  partiel: '',
  final: '',
  reponse: '',
  erreur: null,
  erreurCode: null,
};

/**
 * Traduit une erreur `getUserMedia` ou Web Audio en message français explicite pour l'utilisateur.
 *
 * @param err - Erreur capturée lors de l'initialisation du microphone ou du graphe audio.
 * @returns Message lisible destiné à l'interface.
 */
function formatAudioInitError(err: unknown): string {
  if (err instanceof DOMException) {
    switch (err.name) {
      case 'NotAllowedError':
      case 'PermissionDeniedError':
        return 'Accès au microphone refusé. Autorisez le micro dans votre navigateur pour utiliser le mode Conversation.';
      case 'NotFoundError':
      case 'DevicesNotFoundError':
        return 'Aucun microphone détecté sur cet appareil.';
      case 'NotReadableError':
      case 'TrackStartError':
        return 'Le microphone est déjà utilisé par une autre application.';
      default:
        return err.message || "Impossible d'accéder au microphone.";
    }
  }
  if (err instanceof Error && err.message.trim()) {
    return err.message;
  }
  return "Impossible d'initialiser la capture audio.";
}

/**
 * Construit l'URL WebSocket `/ws/voix` avec le jeton d'authentification si présent.
 *
 * @returns URL `ws://` ou `wss://` complète prête pour `new WebSocket(url)`.
 */
function buildVoiceWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const token = getToken();
  const params = new URLSearchParams();
  if (token) {
    params.set('token', token);
  }
  const query = params.toString();
  return `${protocol}//${window.location.host}/ws/voix${query ? `?${query}` : ''}`;
}

/**
 * Client autonome de conversation vocale quasi temps réel sur `/ws/voix`.
 *
 * Responsabilités :
 * - Capture du microphone via `getUserMedia` (annulation d'écho, suppression de bruit, AGC)
 *   et `AudioWorklet` (`pcm-worklet.js`) émettant des trames PCM 16 kHz mono int16 LE de 20 ms.
 * - Échange sur `/ws/voix` (config, trames binaires, `interrompre`, `fin`, états et texte live).
 * - Lecture TTS sur un `AudioContext` dédié via `decodeAudioData`, ordonnée strictement
 *   par numéro de séquence `seq` avec enchaînement sans trou et interruption immédiate
 *   sur `stop_audio`, `parole_debut` ou `interrompre()`.
 *
 * Aucune reconnexion automatique n'est effectuée : toute coupure réseau ou refus serveur
 * bascule proprement en état `erreur` avec un message explicite.
 */
export class ConversationVocale {
  private snapshot: ConversationVocaleSnapshot = { ...INITIAL_SNAPSHOT };
  private listeners = new Set<ConversationVocaleListener>();

  private sessionId = 0;
  private closingIntentional = false;
  private serverReady = false;

  private ws: WebSocket | null = null;
  private mediaStream: MediaStream | null = null;
  private captureCtx: AudioContext | null = null;
  private playbackCtx: AudioContext | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private silentGainNode: GainNode | null = null;

  // File de lecture audio ordonnée par `seq`
  private playbackGeneration = 0;
  private pendingAudioMeta: PendingAudioMeta | null = null;
  private decodingSeqs = new Set<number>();
  private readyBuffers = new Map<number, AudioBuffer | null>();
  private nextExpectedSeq = 0;
  private nextPlayTime = 0;
  private activeSources = new Set<AudioBufferSourceNode>();
  private responseStreamEnded = true;

  /**
   * Retourne l'instantané courant de la conversation vocale.
   *
   * @returns État, textes partiel/final/réponse et erreur éventuelle.
   */
  public getSnapshot(): ConversationVocaleSnapshot {
    return this.snapshot;
  }

  /**
   * Abonne un observateur aux changements d'état de la conversation.
   * Appelle immédiatement le callback avec l'instantané courant.
   *
   * Effets de bord : ajoute `listener` dans le registre interne.
   *
   * @param listener - Fonction appelée à chaque changement d'état ou de texte.
   * @returns Fonction de désabonnement.
   */
  public subscribe(listener: ConversationVocaleListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /**
   * Démarre la session de conversation vocale (micro + worklet + contexte de lecture + `/ws/voix`).
   * Si une session est déjà active, elle est d'abord arrêtée proprement.
   *
   * Effets de bord : demande l'accès microphone, crée deux `AudioContext`, ouvre un `WebSocket`.
   *
   * @param options - Agent cible et identifiant de voix TTS optionnels.
   * @returns Promesse résolue une fois l'initialisation locale lancée (ou basculée en `erreur`).
   */
  public async demarrer(options?: ConversationVocaleOptions): Promise<void> {
    if (typeof window === 'undefined') {
      return;
    }

    if (
      this.snapshot.etat !== 'inactif' &&
      this.snapshot.etat !== 'erreur'
    ) {
      this.arreter();
    }

    this.sessionId += 1;
    const currentSession = this.sessionId;
    this.closingIntentional = false;
    this.serverReady = false;
    this.resetAudioQueueState();

    this.setSnapshot({
      etat: 'connexion',
      partiel: '',
      final: '',
      reponse: '',
      erreur: null,
      erreurCode: null,
    });

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      this.failSession(
        currentSession,
        "L'API microphone n'est pas disponible dans ce navigateur.",
      );
      return;
    }

    if (typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') {
      this.failSession(
        currentSession,
        'Votre navigateur ne prend pas en charge Web Audio / AudioWorklet.',
      );
      return;
    }

    // Crée le contexte de lecture dans le geste utilisateur pour débloquer l'autoplay audio.
    let localPlaybackCtx: AudioContext | null = null;
    let localCaptureCtx: AudioContext | null = null;
    let localStream: MediaStream | null = null;

    try {
      localPlaybackCtx = new AudioContext();
      if (localPlaybackCtx.state === 'suspended') {
        void localPlaybackCtx.resume().catch(() => {});
      }

      localStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      if (currentSession !== this.sessionId) {
        this.stopStreamTracks(localStream);
        this.closeAudioContext(localPlaybackCtx);
        return;
      }

      localCaptureCtx = new AudioContext();
      if (!localCaptureCtx.audioWorklet) {
        throw new Error(
          'AudioWorklet est indisponible dans ce contexte (HTTPS ou localhost requis).',
        );
      }

      const workletUrl = `${import.meta.env.BASE_URL}pcm-worklet.js`;
      await localCaptureCtx.audioWorklet.addModule(workletUrl);

      if (currentSession !== this.sessionId) {
        this.stopStreamTracks(localStream);
        this.closeAudioContext(localCaptureCtx);
        this.closeAudioContext(localPlaybackCtx);
        return;
      }

      if (localCaptureCtx.state === 'suspended') {
        await localCaptureCtx.resume();
      }

      if (currentSession !== this.sessionId) {
        this.stopStreamTracks(localStream);
        this.closeAudioContext(localCaptureCtx);
        this.closeAudioContext(localPlaybackCtx);
        return;
      }

      const source = localCaptureCtx.createMediaStreamSource(localStream);
      const worklet = new AudioWorkletNode(localCaptureCtx, 'pcm-16k-processor');
      // Branche sur un GainNode muet vers destination pour garantir l'exécution de process() sous Chromium.
      const silentGain = localCaptureCtx.createGain();
      silentGain.gain.value = 0;

      source.connect(worklet);
      worklet.connect(silentGain);
      silentGain.connect(localCaptureCtx.destination);

      this.playbackCtx = localPlaybackCtx;
      this.captureCtx = localCaptureCtx;
      this.mediaStream = localStream;
      this.sourceNode = source;
      this.workletNode = worklet;
      this.silentGainNode = silentGain;

      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (currentSession !== this.sessionId) return;
        if (!this.serverReady || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
          return;
        }
        if (event.data instanceof ArrayBuffer && event.data.byteLength > 0) {
          this.ws.send(event.data);
        }
      };

      this.openWebSocket(currentSession, options);
    } catch (err) {
      if (currentSession !== this.sessionId) {
        this.stopStreamTracks(localStream);
        this.closeAudioContext(localCaptureCtx);
        this.closeAudioContext(localPlaybackCtx);
        return;
      }
      this.stopStreamTracks(localStream);
      this.closeAudioContext(localCaptureCtx);
      this.closeAudioContext(localPlaybackCtx);
      this.failSession(currentSession, formatAudioInitError(err));
    }
  }

  /**
   * Interrompt immédiatement la réponse vocale en cours (arrêt audio local + vidage
   * de file + signal `{"type":"interrompre"}` au serveur) tout en restant à l'écoute.
   *
   * Effets de bord : stoppe les sources audio actives et envoie une trame WS.
   */
  public interrompre(): void {
    this.stopAudioPlayback();
    this.responseStreamEnded = true;

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify({ type: 'interrompre' }));
      } catch {
        // Ignore si la socket vient de se fermer.
      }
    }

    if (
      this.snapshot.etat === 'reponse' ||
      this.snapshot.etat === 'reflexion'
    ) {
      this.patchSnapshot({ etat: 'ecoute' });
    }
  }

  /**
   * Arrête complètement la conversation vocale et libère toutes les ressources :
   * file audio, WebSocket (`/ws/voix` avec message `fin`), AudioWorklet, pistes micro
   * et les deux `AudioContext`.
   *
   * Effets de bord : ferme toutes les ressources média/réseau et repasse à `inactif`.
   */
  public arreter(): void {
    this.sessionId += 1;
    this.closingIntentional = true;
    this.serverReady = false;
    this.cleanupResources();
    this.setSnapshot({ ...INITIAL_SNAPSHOT });
  }

  private openWebSocket(
    session: number,
    options?: ConversationVocaleOptions,
  ): void {
    const ws = new WebSocket(buildVoiceWsUrl());
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    let socketErrorSeen = false;

    ws.onopen = () => {
      if (session !== this.sessionId) {
        ws.close();
        return;
      }
      const configPayload: Record<string, string> = { type: 'config' };
      if (options?.agent) {
        configPayload.agent = options.agent;
      }
      if (options?.voix) {
        configPayload.voix = options.voix;
      }
      if (configPayload.agent || configPayload.voix) {
        ws.send(JSON.stringify(configPayload));
      }
    };

    ws.onmessage = (event: MessageEvent) => {
      if (session !== this.sessionId) return;

      if (event.data instanceof ArrayBuffer) {
        this.handleBinaryFrame(session, event.data);
        return;
      }

      if (typeof Blob !== 'undefined' && event.data instanceof Blob) {
        // Capture les métadonnées de façon synchrone avant la lecture asynchrone du Blob.
        const meta = this.pendingAudioMeta;
        this.pendingAudioMeta = null;
        const generation = this.playbackGeneration;
        void event.data
          .arrayBuffer()
          .then((buf) => {
            if (session !== this.sessionId || generation !== this.playbackGeneration) {
              return;
            }
            if (meta) {
              this.enqueueAudioDecode(session, generation, meta.seq, buf);
            }
          })
          .catch(() => {});
        return;
      }

      if (typeof event.data === 'string') {
        this.handleJsonMessage(session, event.data);
      }
    };

    ws.onerror = () => {
      socketErrorSeen = true;
    };

    ws.onclose = (event: CloseEvent) => {
      if (session !== this.sessionId || this.closingIntentional) {
        return;
      }
      this.ws = null;
      this.serverReady = false;

      let message = 'Connexion vocale interrompue.';
      if (event.code === 4401) {
        message = "Connexion vocale refusée (jeton d'authentification invalide).";
      } else if (event.code === 4000) {
        message = 'Session vocale fermée car une autre session a été ouverte.';
      } else if (event.reason && event.reason.trim()) {
        message = `Connexion vocale fermée (${event.reason.trim()}).`;
      } else if (socketErrorSeen && !this.serverReady) {
        message = 'Impossible de joindre le serveur vocal (/ws/voix).';
      }

      this.failSession(session, message);
    };
  }

  private handleJsonMessage(session: number, raw: string): void {
    let msg: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return;
      msg = parsed as Record<string, unknown>;
    } catch {
      return;
    }

    if (session !== this.sessionId) return;

    const type = typeof msg.type === 'string' ? msg.type : '';
    switch (type) {
      case 'pret':
        this.serverReady = true;
        this.patchSnapshot({
          etat: 'ecoute',
          erreur: null,
          erreurCode: null,
        });
        break;

      case 'parole_debut':
        this.serverReady = true;
        this.stopAudioPlayback();
        this.responseStreamEnded = true;
        this.patchSnapshot({
          etat: 'parole',
          partiel: '',
          erreur: null,
          erreurCode: null,
        });
        break;

      case 'partiel': {
        const texte = typeof msg.texte === 'string' ? msg.texte : '';
        this.patchSnapshot({
          etat: 'parole',
          partiel: texte,
        });
        break;
      }

      case 'final': {
        const texte = typeof msg.texte === 'string' ? msg.texte : '';
        this.stopAudioPlayback();
        this.responseStreamEnded = false;
        this.patchSnapshot({
          etat: 'reflexion',
          partiel: '',
          final: texte,
          reponse: '',
          erreur: null,
          erreurCode: null,
        });
        break;
      }

      case 'rien':
        this.patchSnapshot({
          etat: 'ecoute',
          partiel: '',
        });
        break;

      case 'reponse_delta': {
        const delta = typeof msg.texte === 'string' ? msg.texte : '';
        if (delta) {
          this.patchSnapshot({
            reponse: `${this.snapshot.reponse}${delta}`,
          });
        }
        break;
      }

      case 'audio_debut': {
        const seq =
          typeof msg.seq === 'number' && Number.isInteger(msg.seq) && msg.seq >= 0
            ? msg.seq
            : 0;
        const mime = typeof msg.mime === 'string' ? msg.mime : 'audio/mpeg';
        this.pendingAudioMeta = { seq, mime };
        this.responseStreamEnded = false;
        if (
          this.snapshot.etat === 'reflexion' ||
          this.snapshot.etat === 'ecoute'
        ) {
          this.patchSnapshot({ etat: 'reponse' });
        }
        break;
      }

      case 'reponse_fin':
        this.responseStreamEnded = true;
        this.checkPlaybackFinished(this.playbackGeneration);
        break;

      case 'stop_audio':
        this.stopAudioPlayback();
        this.responseStreamEnded = true;
        if (
          this.snapshot.etat === 'reponse' ||
          this.snapshot.etat === 'reflexion'
        ) {
          this.patchSnapshot({ etat: 'ecoute' });
        }
        break;

      case 'erreur': {
        const message =
          typeof msg.message === 'string' && msg.message.trim()
            ? msg.message.trim()
            : 'Erreur vocale signalée par le serveur.';
        const code =
          typeof msg.code === 'string' && msg.code.trim()
            ? msg.code.trim()
            : null;
        if (!this.serverReady) {
          this.failSession(session, message);
          break;
        }
        // D'après ARCHITECTURE-VOIX §1, la session reste active à l'écoute après une erreur de tour.
        this.stopAudioPlayback();
        this.responseStreamEnded = true;
        this.patchSnapshot({
          etat: 'ecoute',
          erreur: message,
          erreurCode: code,
        });
        break;
      }

      default:
        break;
    }
  }

  private handleBinaryFrame(session: number, buffer: ArrayBuffer): void {
    const meta = this.pendingAudioMeta;
    this.pendingAudioMeta = null;
    if (!meta) return;

    this.enqueueAudioDecode(session, this.playbackGeneration, meta.seq, buffer);
  }

  private enqueueAudioDecode(
    session: number,
    generation: number,
    seq: number,
    buffer: ArrayBuffer,
  ): void {
    const ctx = this.playbackCtx;
    if (!ctx || session !== this.sessionId || generation !== this.playbackGeneration) {
      return;
    }

    this.decodingSeqs.add(seq);
    // Copie le tampon car decodeAudioData détache l'ArrayBuffer passé en argument.
    const copy = buffer.slice(0);
    ctx
      .decodeAudioData(copy)
      .then((decoded) => {
        if (
          session !== this.sessionId ||
          generation !== this.playbackGeneration
        ) {
          return;
        }
        this.decodingSeqs.delete(seq);
        this.readyBuffers.set(seq, decoded);
        this.scheduleReadyBuffers(session, generation);
      })
      .catch(() => {
        if (
          session !== this.sessionId ||
          generation !== this.playbackGeneration
        ) {
          return;
        }
        // Marque la séquence en échec (null) pour ne pas bloquer les suivantes.
        this.decodingSeqs.delete(seq);
        this.readyBuffers.set(seq, null);
        this.scheduleReadyBuffers(session, generation);
      });
  }

  private scheduleReadyBuffers(session: number, generation: number): void {
    const ctx = this.playbackCtx;
    if (!ctx || session !== this.sessionId || generation !== this.playbackGeneration) {
      return;
    }

    while (this.readyBuffers.has(this.nextExpectedSeq)) {
      const audioBuffer = this.readyBuffers.get(this.nextExpectedSeq) ?? null;
      this.readyBuffers.delete(this.nextExpectedSeq);
      this.nextExpectedSeq += 1;

      if (!audioBuffer) {
        continue;
      }

      if (ctx.state === 'suspended') {
        void ctx.resume().catch(() => {});
      }

      const now = ctx.currentTime;
      const startAt =
        this.nextPlayTime > now ? this.nextPlayTime : now + 0.01;
      this.nextPlayTime = startAt + audioBuffer.duration;

      const source = ctx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(ctx.destination);
      this.activeSources.add(source);

      if (this.snapshot.etat !== 'reponse') {
        this.patchSnapshot({ etat: 'reponse' });
      }

      source.onended = () => {
        this.activeSources.delete(source);
        try {
          source.disconnect();
        } catch {
          // Déjà déconnecté.
        }
        if (
          session === this.sessionId &&
          generation === this.playbackGeneration
        ) {
          this.checkPlaybackFinished(generation);
        }
      };

      source.start(startAt);
    }

    this.checkPlaybackFinished(generation);
  }

  private checkPlaybackFinished(generation: number): void {
    if (generation !== this.playbackGeneration) return;
    if (
      this.responseStreamEnded &&
      this.activeSources.size === 0 &&
      this.decodingSeqs.size === 0 &&
      this.readyBuffers.size === 0
    ) {
      if (
        this.snapshot.etat === 'reponse' ||
        this.snapshot.etat === 'reflexion'
      ) {
        this.patchSnapshot({ etat: 'ecoute' });
      }
    }
  }

  private stopAudioPlayback(): void {
    this.resetAudioQueueState();
    for (const source of this.activeSources) {
      source.onended = null;
      try {
        source.stop();
      } catch {
        // Source déjà terminée ou non démarrée.
      }
      try {
        source.disconnect();
      } catch {
        // Déjà déconnectée.
      }
    }
    this.activeSources.clear();
  }

  private resetAudioQueueState(): void {
    this.playbackGeneration += 1;
    this.pendingAudioMeta = null;
    this.decodingSeqs.clear();
    this.readyBuffers.clear();
    this.nextExpectedSeq = 0;
    this.nextPlayTime = 0;
  }

  private failSession(session: number, message: string): void {
    if (session !== this.sessionId) return;
    this.closingIntentional = true;
    this.serverReady = false;
    this.cleanupResources();
    this.setSnapshot({
      ...this.snapshot,
      etat: 'erreur',
      partiel: '',
      erreur: message,
      erreurCode: null,
    });
  }

  private cleanupResources(): void {
    this.stopAudioPlayback();

    if (this.ws) {
      const sock = this.ws;
      this.ws = null;
      sock.onopen = null;
      sock.onmessage = null;
      sock.onerror = null;
      sock.onclose = null;
      if (sock.readyState === WebSocket.OPEN) {
        try {
          sock.send(JSON.stringify({ type: 'fin' }));
        } catch {
          // Ignore les erreurs d'envoi à la fermeture.
        }
      }
      if (
        sock.readyState === WebSocket.OPEN ||
        sock.readyState === WebSocket.CONNECTING
      ) {
        try {
          sock.close();
        } catch {
          // Ignore.
        }
      }
    }

    if (this.workletNode) {
      try {
        this.workletNode.port.postMessage({ type: 'stop' });
      } catch {
        // Ignore si le port est déjà fermé.
      }
      this.workletNode.port.onmessage = null;
      try {
        this.workletNode.disconnect();
      } catch {
        // Ignore.
      }
      this.workletNode = null;
    }

    if (this.sourceNode) {
      try {
        this.sourceNode.disconnect();
      } catch {
        // Ignore.
      }
      this.sourceNode = null;
    }

    if (this.silentGainNode) {
      try {
        this.silentGainNode.disconnect();
      } catch {
        // Ignore.
      }
      this.silentGainNode = null;
    }

    this.stopStreamTracks(this.mediaStream);
    this.mediaStream = null;

    this.closeAudioContext(this.captureCtx);
    this.captureCtx = null;

    this.closeAudioContext(this.playbackCtx);
    this.playbackCtx = null;
  }

  private stopStreamTracks(stream: MediaStream | null): void {
    if (!stream) return;
    for (const track of stream.getTracks()) {
      try {
        track.stop();
      } catch {
        // Ignore.
      }
    }
  }

  private closeAudioContext(ctx: AudioContext | null): void {
    if (!ctx || ctx.state === 'closed') return;
    void ctx.close().catch(() => {});
  }

  private patchSnapshot(patch: Partial<ConversationVocaleSnapshot>): void {
    this.setSnapshot({ ...this.snapshot, ...patch });
  }

  private setSnapshot(next: ConversationVocaleSnapshot): void {
    this.snapshot = next;
    for (const listener of this.listeners) {
      listener(next);
    }
  }
}
