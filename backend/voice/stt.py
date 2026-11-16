"""Reconnaissance vocale locale (faster-whisper) et capture microphone (sounddevice)."""

from __future__ import annotations

import gc
import logging
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Literal

import numpy as np
import sounddevice as sd

from backend import config, settings_store

logger = logging.getLogger(__name__)

MODELE_WHISPER = "small"
MODELES_WHISPER_VALIDES: tuple[str, ...] = ("large-v3-turbo", "medium", "small")
FREQUENCE_ECHANTILLONNAGE = 16000
CANAUX = 1
TAILLE_BLOC = 1600  # 100 ms à 16 kHz
SEUIL_RMS_PAROLE = 320.0

_modele_lock = threading.Lock()
_modele: Any | None = None
_modele_nom_charge: str | None = None
_peripherique_actif: Literal["cuda", "cpu"] | None = None
_modele_test: Any | None = None

_ecoute_lock = threading.Lock()
_stop_event: threading.Event | None = None


class MicroIndisponibleError(RuntimeError):
    """Erreur levée lorsque le périphérique microphone est absent ou inaccessible."""


def cuda_disponible() -> bool:
    """Indique si au moins un GPU CUDA utilisable par CTranslate2 est détecté.

    Returns:
        ``True`` si CTranslate2 détecte au moins un périphérique CUDA, sinon ``False``.
    """
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        return False


def modele_defaut() -> str:
    """Retourne le modèle Whisper par défaut selon la disponibilité GPU CUDA.

    Returns:
        ``"large-v3-turbo"`` si CUDA est disponible, sinon ``"small"``.
    """
    return "large-v3-turbo" if cuda_disponible() else "small"


def _resoudre_config_stt() -> tuple[str, int]:
    """Lit ``voix.stt_modele`` et ``voix.stt_beam_final`` depuis les paramètres."""
    modele_repli = modele_defaut()
    try:
        voix_cfg = settings_store.lire().get("voix")
    except Exception:
        voix_cfg = None

    modele = modele_repli
    beam_final_raw: Any = None
    if isinstance(voix_cfg, dict):
        candidat = str(voix_cfg.get("stt_modele") or "").strip()
        if candidat in MODELES_WHISPER_VALIDES:
            modele = candidat
        beam_final_raw = voix_cfg.get("stt_beam_final")

    if isinstance(beam_final_raw, int) and not isinstance(beam_final_raw, bool) and 1 <= beam_final_raw <= 10:
        beam_final = beam_final_raw
    else:
        beam_final = 1 if modele == "large-v3-turbo" else 5

    return modele, beam_final


def set_modele_for_tests(modele: Any | None) -> None:
    """Injecte un modèle Whisper simulé pour les tests unitaires, ou restaure le défaut.

    Args:
        modele: Instance exposant ``transcribe(chemin, language=..., vad_filter=...)``,
            ou ``None`` pour revenir au chargement paresseux standard.

    Effets de bord:
        Réinitialise l'instance singleton ``_modele`` et son nom chargé sous verrou.
    """
    global _modele, _modele_nom_charge, _peripherique_actif, _modele_test
    with _modele_lock:
        _modele_test = modele
        _modele = None
        _modele_nom_charge = None
        _peripherique_actif = None


def _charger_modele(nom_modele: str | None = None) -> Any:
    """Charge ``WhisperModel(nom_modele)`` en CUDA float16 avec repli CPU int8.

    Args:
        nom_modele: Identifiant Whisper à charger (défaut issu des paramètres).

    Returns:
        Instance de ``WhisperModel`` (ou du double injecté pour les tests).
    """
    global _peripherique_actif
    if _modele_test is not None:
        return _modele_test

    cible = nom_modele if nom_modele in MODELES_WHISPER_VALIDES else modele_defaut()
    from faster_whisper import WhisperModel

    try:
        logger.info("Chargement du modèle faster-whisper '%s' sur CUDA (float16)...", cible)
        instance = WhisperModel(cible, device="cuda", compute_type="float16")
        _peripherique_actif = "cuda"
        return instance
    except Exception as exc:
        logger.info("CUDA indisponible pour faster-whisper (%s), repli sur CPU (int8).", exc)
        instance = WhisperModel(cible, device="cpu", compute_type="int8")
        _peripherique_actif = "cpu"
        return instance


def _obtenir_modele_sans_verrou(nom_modele: str) -> Any:
    """Retourne le singleton Whisper en rechargeant à chaud si le modèle configuré a changé."""
    global _modele, _modele_nom_charge, _peripherique_actif
    if _modele_test is not None:
        _modele_nom_charge = nom_modele
        return _modele_test

    if _modele is not None and _modele_nom_charge != nom_modele:
        logger.info(
            "Changement à chaud du modèle Whisper (%s -> %s), rechargement.",
            _modele_nom_charge,
            nom_modele,
        )
        _modele = None
        _modele_nom_charge = None
        _peripherique_actif = None
        gc.collect()

    if _modele is None:
        _modele = _charger_modele(nom_modele)
        _modele_nom_charge = nom_modele
    return _modele


def liberer() -> None:
    """Libère le modèle faster-whisper de la mémoire vive/VRAM sous verrou thread.

    Effets de bord:
        Met l'instance globale ``_modele`` à ``None`` et déclenche une collecte GC.
    """
    global _modele, _modele_nom_charge, _peripherique_actif
    with _modele_lock:
        _modele = None
        _modele_nom_charge = None
        _peripherique_actif = None
    gc.collect()


def arreter_ecoute() -> None:
    """Demande l'arrêt anticipé d'un enregistrement microphone en cours.

    Effets de bord:
        Active l'événement d'arrêt associé à la capture active, s'il existe.
    """
    with _ecoute_lock:
        if _stop_event is not None:
            _stop_event.set()


def precharger() -> None:
    """Précharge le singleton faster-whisper configuré en mémoire sous verrou thread.

    Effets de bord:
        Acquiert ``_modele_lock`` et charge (ou recharge) le modèle configuré.
    """
    nom_modele, _ = _resoudre_config_stt()
    with _modele_lock:
        _obtenir_modele_sans_verrou(nom_modele)


def etat_stt() -> dict[str, Any]:
    """Retourne l'état courant du moteur STT (modèle configuré, périphérique, chargement).

    Returns:
        Dictionnaire ``{"stt_modele", "stt_peripherique", "stt_charge"}``.
    """
    nom_modele, _ = _resoudre_config_stt()
    with _modele_lock:
        charge = bool(_modele is not None or _modele_test is not None)
        periph = _peripherique_actif or ("cuda" if cuda_disponible() else "cpu")
    return {
        "stt_modele": nom_modele,
        "stt_peripherique": periph,
        "stt_charge": charge,
    }


def transcrire_tableau(
    audio: np.ndarray,
    rapide: bool = False,
    beam_size: int | None = None,
) -> str:
    """Transcrit un tampon audio en mémoire (numpy 16 kHz mono) en français.

    Args:
        audio: Tableau 1D (ou mono) d'échantillons audio (``float32`` dans [-1, 1] ou ``int16``).
        rapide: Si ``True`` (partiels temps réel), force ``beam_size=1`` ; sinon utilise
            ``beam_size`` ou ``voix.stt_beam_final`` (défaut 1 en turbo, 5 sinon).
        beam_size: Surcharge explicite de ``beam_size`` pour la transcription finale.

    Returns:
        Texte transcrit normalisé (chaîne vide si le tampon est vide ou sans parole).

    Effets de bord:
        Charge ou recharge à chaud ``_modele`` si besoin et sérialise l'inférence via ``_modele_lock``.
    """
    tableau = np.asarray(audio)
    if tableau.size == 0:
        return ""

    if np.issubdtype(tableau.dtype, np.integer):
        tableau_f32 = tableau.astype(np.float32) / 32768.0
    else:
        tableau_f32 = tableau.astype(np.float32, copy=False)
    tableau_f32 = np.ascontiguousarray(tableau_f32.reshape(-1), dtype=np.float32)
    if tableau_f32.size == 0:
        return ""

    nom_modele, beam_final = _resoudre_config_stt()
    beam_effectif = 1 if rapide else (beam_size if beam_size is not None else beam_final)
    with _modele_lock:
        modele = _obtenir_modele_sans_verrou(nom_modele)
        segments, _info = modele.transcribe(
            tableau_f32,
            language="fr",
            beam_size=beam_effectif,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        # Consomme le générateur à l'intérieur du verrou pour garantir la sérialisation CTranslate2.
        morceaux = [
            seg.text.strip()
            for seg in segments
            if isinstance(getattr(seg, "text", None), str) and seg.text.strip()
        ]
    return " ".join(morceaux).strip()


def transcrire(chemin: str | Path) -> str:
    """Transcrit un fichier audio en français via le singleton faster-whisper.

    Args:
        chemin: Chemin vers le fichier audio (WAV, MP3, WebM, etc.).

    Returns:
        Texte transcrit normalisé (chaîne vide si aucune parole détectée).

    Raises:
        FileNotFoundError: Si le fichier audio source n'existe pas.

    Effets de bord:
        Charge ou recharge à chaud le modèle Whisper et acquiert ``_modele_lock``.
    """
    fichier = Path(chemin)
    if not fichier.is_file():
        raise FileNotFoundError(f"Fichier audio introuvable : {fichier}")

    nom_modele, _ = _resoudre_config_stt()
    with _modele_lock:
        modele = _obtenir_modele_sans_verrou(nom_modele)
        segments, _info = modele.transcribe(str(fichier), language="fr", vad_filter=True)
        # Consomme le générateur à l'intérieur du verrou pour garantir la sérialisation CTranslate2.
        morceaux = [
            seg.text.strip()
            for seg in segments
            if isinstance(getattr(seg, "text", None), str) and seg.text.strip()
        ]
    return " ".join(morceaux).strip()


def lister_micros() -> list[dict[str, Any]]:
    """Liste les périphériques d'entrée audio détectés par ``sounddevice``.

    Returns:
        Liste de dictionnaires ``{"index": int, "nom": str, "defaut": bool}``.
    """
    try:
        devices = sd.query_devices()
    except Exception as exc:
        logger.warning("Impossible de lister les microphones via sounddevice : %s", exc)
        return []

    default_in: int | None = None
    try:
        default_dev = sd.default.device
        if isinstance(default_dev, (list, tuple)) and default_dev:
            candidat = default_dev[0]
            if isinstance(candidat, int) and candidat >= 0:
                default_in = candidat
        elif isinstance(default_dev, int) and default_dev >= 0:
            default_in = default_dev
    except Exception:
        default_in = None

    resultat: list[dict[str, Any]] = []
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            continue
        if int(dev.get("max_input_channels") or 0) <= 0:
            continue
        nom = str(dev.get("name") or f"Micro {idx}").strip()
        resultat.append(
            {
                "index": idx,
                "nom": nom,
                "defaut": idx == default_in,
            }
        )
    return resultat


def _resoudre_index_micro(nom_micro: str | None) -> int | None:
    """Retourne l'index ``sounddevice`` correspondant au nom configuré, ou ``None``."""
    cible = (nom_micro or "").strip()
    if not cible:
        return None
    cible_low = cible.lower()
    micros = lister_micros()
    for item in micros:
        if str(item["nom"]).lower() == cible_low:
            return int(item["index"])
    for item in micros:
        if cible_low in str(item["nom"]).lower():
            return int(item["index"])
    return None


def _sauver_wav(chemin: Path, echantillons: np.ndarray) -> None:
    """Écrit un tableau ``int16`` mono 16 kHz dans un fichier WAV PCM standard."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.asarray(echantillons, dtype=np.int16).reshape(-1)
    with wave.open(str(chemin), "wb") as wf:
        wf.setnchannels(CANAUX)
        wf.setsampwidth(2)
        wf.setframerate(FREQUENCE_ECHANTILLONNAGE)
        wf.writeframes(pcm.tobytes())


def enregistrer_micro(
    duree_max: float = 15.0,
    silence_s: float = 1.2,
    micro: str | None = None,
) -> Path:
    """Enregistre la voix depuis le microphone configuré (ou par défaut) jusqu'au silence.

    Args:
        duree_max: Durée maximale d'enregistrement en secondes (défaut 15 s).
        silence_s: Durée de silence après détection de parole déclenchant l'arrêt (défaut 1,2 s).
        micro: Nom du micro à utiliser ; si ``None``, lit ``voix.micro`` dans les paramètres.

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

    nom_micro = micro
    if nom_micro is None:
        try:
            voix_cfg = settings_store.lire().get("voix")
            if isinstance(voix_cfg, dict) and isinstance(voix_cfg.get("micro"), str):
                nom_micro = voix_cfg.get("micro")
        except Exception:
            nom_micro = None

    device_idx = _resoudre_index_micro(nom_micro) if nom_micro else None

    stop_evt = threading.Event()
    with _ecoute_lock:
        _stop_event = stop_evt

    trames: list[np.ndarray] = []
    debut = time.monotonic()
    dernier_son = debut
    parole_detectee = False

    stream_kwargs: dict[str, Any] = {
        "samplerate": FREQUENCE_ECHANTILLONNAGE,
        "channels": CANAUX,
        "dtype": "int16",
        "blocksize": TAILLE_BLOC,
    }
    if device_idx is not None:
        stream_kwargs["device"] = device_idx

    try:
        try:
            with sd.InputStream(**stream_kwargs) as stream:
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
