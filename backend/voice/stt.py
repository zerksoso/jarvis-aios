"""Reconnaissance vocale locale (faster-whisper) et capture microphone (sounddevice)."""

from __future__ import annotations

import gc
import logging
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

from backend import config

logger = logging.getLogger(__name__)

MODELE_WHISPER = "small"
FREQUENCE_ECHANTILLONNAGE = 16000
CANAUX = 1
TAILLE_BLOC = 1600  # 100 ms à 16 kHz
SEUIL_RMS_PAROLE = 320.0

_modele_lock = threading.Lock()
_modele: Any | None = None
_modele_test: Any | None = None

_ecoute_lock = threading.Lock()
_stop_event: threading.Event | None = None


class MicroIndisponibleError(RuntimeError):
    """Erreur levée lorsque le périphérique microphone est absent ou inaccessible."""


def set_modele_for_tests(modele: Any | None) -> None:
    """Injecte un modèle Whisper simulé pour les tests unitaires, ou restaure le défaut.

    Args:
        modele: Instance exposant ``transcribe(chemin, language=..., vad_filter=...)``,
            ou ``None`` pour revenir au chargement paresseux standard.

    Effets de bord:
        Réinitialise l'instance singleton ``_modele`` sous verrou.
    """
    global _modele, _modele_test
    with _modele_lock:
        _modele_test = modele
        _modele = None


def _charger_modele() -> Any:
    """Charge paresseusement ``WhisperModel('small')`` en CUDA float16 avec repli CPU int8.

    Returns:
        Instance de ``WhisperModel`` (ou du double injecté pour les tests).
    """
    if _modele_test is not None:
        return _modele_test

    from faster_whisper import WhisperModel

    try:
        logger.info("Chargement du modèle faster-whisper '%s' sur CUDA (float16)...", MODELE_WHISPER)
        return WhisperModel(MODELE_WHISPER, device="cuda", compute_type="float16")
    except Exception as exc:
        logger.info("CUDA indisponible pour faster-whisper (%s), repli sur CPU (int8).", exc)
        return WhisperModel(MODELE_WHISPER, device="cpu", compute_type="int8")


def liberer() -> None:
    """Libère le modèle faster-whisper de la mémoire vive/VRAM sous verrou thread.

    Effets de bord:
        Met l'instance globale ``_modele`` à ``None`` et déclenche une collecte GC.
    """
    global _modele
    with _modele_lock:
        _modele = None
    gc.collect()


def arreter_ecoute() -> None:
    """Demande l'arrêt anticipé d'un enregistrement microphone en cours.

    Effets de bord:
        Active l'événement d'arrêt associé à la capture active, s'il existe.
    """
    with _ecoute_lock:
        if _stop_event is not None:
            _stop_event.set()


def transcrire(chemin: str | Path) -> str:
    """Transcrit un fichier audio en français via le singleton faster-whisper.

    Args:
        chemin: Chemin vers le fichier audio (WAV, MP3, WebM, etc.).

    Returns:
        Texte transcrit normalisé (chaîne vide si aucune parole détectée).

    Raises:
        FileNotFoundError: Si le fichier audio source n'existe pas.

    Effets de bord:
        Charge paresseusement le modèle Whisper au premier appel et acquiert ``_modele_lock``.
    """
    global _modele
    fichier = Path(chemin)
    if not fichier.is_file():
        raise FileNotFoundError(f"Fichier audio introuvable : {fichier}")

    with _modele_lock:
        if _modele is None:
            _modele = _charger_modele()
        segments, _info = _modele.transcribe(str(fichier), language="fr", vad_filter=True)
        # Consomme le générateur à l'intérieur du verrou pour garantir la sérialisation CTranslate2.
        morceaux = [
            seg.text.strip()
            for seg in segments
            if isinstance(getattr(seg, "text", None), str) and seg.text.strip()
        ]
    return " ".join(morceaux).strip()


def _sauver_wav(chemin: Path, echantillons: np.ndarray) -> None:
    """Écrit un tableau ``int16`` mono 16 kHz dans un fichier WAV PCM standard."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(echantillons, dtype=np.int16).reshape(-1)
    with wave.open(str(chemin), "wb") as wf:
        wf.setnchannels(CANAUX)
        wf.setsampwidth(2)
        wf.setframerate(FREQUENCE_ECHANTILLONNAGE)
        wf.writeframes(pcm.tobytes())


def enregistrer_micro(duree_max: float = 15.0, silence_s: float = 1.2) -> Path:
    """Enregistre la voix depuis le microphone par défaut jusqu'au silence après parole.

    Args:
        duree_max: Durée maximale d'enregistrement en secondes (défaut 15 s).
        silence_s: Durée de silence après détection de parole déclenchant l'arrêt (défaut 1,2 s).

    Returns:
        Chemin du fichier WAV 16 kHz mono généré sous ``RUNTIME/voice/``.

    Raises:
        MicroIndisponibleError: Si aucun micro n'est disponible ou si le flux échoue.
        ValueError: Si ``duree_max <= 0`` ou ``silence_s < 0``.

    Effets de bord:
        Ouvre le périphérique audio d'entrée via ``sounddevice`` et écrit un fichier WAV local.
    """
    global _stop_event
    if duree_max <= 0:
        raise ValueError("duree_max doit être strictement positif.")
    if silence_s < 0:
        raise ValueError("silence_s doit être positif ou nul.")

    stop_evt = threading.Event()
    with _ecoute_lock:
        _stop_event = stop_evt

    trames: list[np.ndarray] = []
    debut = time.monotonic()
    dernier_son = debut
    parole_detectee = False

    try:
        try:
            with sd.InputStream(
                samplerate=FREQUENCE_ECHANTILLONNAGE,
                channels=CANAUX,
                dtype="int16",
                blocksize=TAILLE_BLOC,
            ) as stream:
                while not stop_evt.is_set():
                    maintenant = time.monotonic()
                    if maintenant - debut >= duree_max:
                        break
                    bloc, _overflowed = stream.read(TAILLE_BLOC)
                    copie = np.asarray(bloc, dtype=np.int16).copy()
                    trames.append(copie)

                    if copie.size > 0:
                        rms = float(np.sqrt(np.mean(copie.astype(np.float32) ** 2)))
                    else:
                        rms = 0.0

                    if rms >= SEUIL_RMS_PAROLE:
                        parole_detectee = True
                        dernier_son = time.monotonic()
                    elif parole_detectee and (time.monotonic() - dernier_son >= silence_s):
                        break
        except MicroIndisponibleError:
            raise
        except Exception as exc:
            raise MicroIndisponibleError(f"Micro indisponible : {exc}") from exc
    finally:
        with _ecoute_lock:
            if _stop_event is stop_evt:
                _stop_event = None

    dossier_voix = config.RUNTIME / "voice"
    chemin_wav = dossier_voix / f"micro-{uuid.uuid4().hex[:10]}.wav"
    if trames:
        audio = np.concatenate(trames, axis=0)
    else:
        audio = np.zeros((0,), dtype=np.int16)
    _sauver_wav(chemin_wav, audio)
    return chemin_wav
