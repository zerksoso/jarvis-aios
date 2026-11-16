/**
 * AudioWorkletProcessor de capture vocale temps réel pour `/ws/voix`.
 *
 * Convertit le flux d'entrée du microphone (mono ou multi-canal, à la fréquence
 * du contexte audio, généralement 48 kHz ou 44,1 kHz) en trames PCM
 * 16 kHz mono int16 little-endian de 320 échantillons (20 ms = 640 octets).
 *
 * Maintient un état continu entre les blocs (`prevSample`, `phase`) afin
 * d'éviter tout clic ou discontinuité lors du rééchantillonnage linéaire.
 */

const TARGET_SAMPLE_RATE = 16000;
const FRAME_SAMPLES = 320;
const FRAME_BYTES = FRAME_SAMPLES * 2;

/**
 * Processeur AudioWorklet effectuant le mixage mono, le rééchantillonnage
 * linéaire vers 16 kHz et le découpage en paquets de 20 ms.
 */
class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    /** Dernier échantillon mono du bloc précédent pour l'interpolation à la frontière. */
    this.prevSample = 0;
    /** Position fractionnaire courante dans le bloc courant (peut débuter dans [-1, 0)). */
    this.phase = 0;
    /** Tampon réutilisé pour le mixage multi-canal vers mono sans allocation par quantum. */
    this.monoScratch = new Float32Array(128);
    /** Trame PCM sortante en cours de remplissage (320 échantillons int16 LE). */
    this.frameBuffer = new ArrayBuffer(FRAME_BYTES);
    this.frameView = new DataView(this.frameBuffer);
    this.frameOffset = 0;
    this.stopped = false;

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'stop') {
        this.stopped = true;
      }
    };
  }

  /**
   * Traite un quantum audio entrant, rééchantillonne vers 16 kHz et émet
   * chaque trame complète de 320 échantillons via `port.postMessage`.
   *
   * @param {Float32Array[][]} inputs - Entrées audio fournies par le graphe Web Audio.
   * @returns {boolean} `true` pour garder le nœud actif, `false` après arrêt.
   */
  process(inputs) {
    if (this.stopped) {
      return false;
    }

    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    const channelCount = input.length;
    const firstChannel = input[0];
    if (!firstChannel || firstChannel.length === 0) {
      return true;
    }

    const sampleCount = firstChannel.length;
    let mono;
    if (channelCount === 1) {
      mono = firstChannel;
    } else {
      if (this.monoScratch.length < sampleCount) {
        this.monoScratch = new Float32Array(sampleCount);
      }
      mono = this.monoScratch;
      for (let i = 0; i < sampleCount; i += 1) {
        let sum = 0;
        for (let ch = 0; ch < channelCount; ch += 1) {
          sum += input[ch][i];
        }
        mono[i] = sum / channelCount;
      }
    }

    const sourceRate =
      typeof sampleRate === 'number' && sampleRate > 0 ? sampleRate : 48000;
    const step = sourceRate / TARGET_SAMPLE_RATE;

    // On s'arrête avant le dernier échantillon du bloc afin de toujours disposer
    // de (i0, i0 + 1) ; le dernier échantillon devient prevSample pour le bloc suivant.
    while (this.phase < sampleCount - 1) {
      const i0 = Math.floor(this.phase);
      const frac = this.phase - i0;
      const s0 = i0 < 0 ? this.prevSample : mono[i0];
      const s1 = mono[i0 + 1];
      const interpolated = s0 + (s1 - s0) * frac;

      const clamped =
        interpolated < -1 ? -1 : interpolated > 1 ? 1 : interpolated;
      const int16 =
        clamped < 0
          ? Math.round(clamped * 32768)
          : Math.round(clamped * 32767);

      this.frameView.setInt16(this.frameOffset * 2, int16, true);
      this.frameOffset += 1;

      if (this.frameOffset === FRAME_SAMPLES) {
        const readyBuffer = this.frameBuffer;
        this.port.postMessage(readyBuffer, [readyBuffer]);
        this.frameBuffer = new ArrayBuffer(FRAME_BYTES);
        this.frameView = new DataView(this.frameBuffer);
        this.frameOffset = 0;
      }

      this.phase += step;
    }

    this.prevSample = mono[sampleCount - 1];
    this.phase -= sampleCount;

    return true;
  }
}

registerProcessor('pcm-16k-processor', Pcm16kProcessor);
