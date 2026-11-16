"""Synthèse vocale TTS avec edge-tts, nettoyage Markdown, cache par hash et repli pyttsx3."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import threading
import uuid
from pathlib import Path

import edge_tts
import pyttsx3

from backend import config

logger = logging.getLogger(__name__)

VOIX_DEFAUT = "fr-FR-DeniseNeural"
MAX_CARACTERES_TTS = 1200

_pyttsx3_lock = threading.Lock()

_RE_BLOC_CODE_BACKTICK = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_RE_BLOC_CODE_TILDE = re.compile(r"~~~[\s\S]*?~~~", re.MULTILINE)
_RE_IMAGE_MD = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RE_LIEN_MD = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_RE_CODE_INLINE = re.compile(r"`([^`]*)`")
_RE_TITRE_MD = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_RE_CITATION_MD = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)
_RE_REGLE_HORIZONTALE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", re.MULTILINE)
_RE_PUCE_LISTE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+", re.MULTILINE)
_RE_GRAS_ETOILES = re.compile(r"\*\*([^*]+)\*\*")
_RE_GRAS_SOULIGNE = re.compile(r"__([^_]+)__")
_RE_BARRE = re.compile(r"~~([^~]+)~~")
_RE_ITALIQUE_ETOILE = re.compile(r"\*([^*\n]+)\*")
_RE_ITALIQUE_SOULIGNE = re.compile(r"(?<!\w)_([^_\n]+)_(?!\w)")


def nettoyer_markdown_pour_tts(texte: str, limite: int = MAX_CARACTERES_TTS) -> str:
    """Nettoie un texte Markdown pour le rendre naturel à l'oral et le borne à ``limite``.

    Transforme les blocs de code en ``(bloc de code)``, retire les marqueurs ``**``,
    ``#``, liens et syntaxes Markdown résiduelles, puis tronque à ``limite`` caractères.

    Args:
        texte: Contenu brut (potentiellement en Markdown).
        limite: Longueur maximale en caractères (1 200 par défaut).

    Returns:
        Texte propre prêt pour la synthèse vocale.
    """
    if not texte:
        return ""

    propre = _RE_BLOC_CODE_BACKTICK.sub(" (bloc de code) ", texte)
    propre = _RE_BLOC_CODE_TILDE.sub(" (bloc de code) ", propre)
    propre = _RE_IMAGE_MD.sub(lambda m: m.group(1), propre)
    propre = _RE_LIEN_MD.sub(lambda m: m.group(1), propre)
    propre = _RE_CODE_INLINE.sub(lambda m: m.group(1), propre)
    propre = _RE_REGLE_HORIZONTALE.sub(" ", propre)
    propre = _RE_TITRE_MD.sub("", propre)
    propre = _RE_CITATION_MD.sub("", propre)
    propre = _RE_PUCE_LISTE.sub("", propre)

    for _ in range(2):
        propre = _RE_GRAS_ETOILES.sub(lambda m: m.group(1), propre)
        propre = _RE_GRAS_SOULIGNE.sub(lambda m: m.group(1), propre)
        propre = _RE_BARRE.sub(lambda m: m.group(1), propre)
        propre = _RE_ITALIQUE_ETOILE.sub(lambda m: m.group(1), propre)
        propre = _RE_ITALIQUE_SOULIGNE.sub(lambda m: m.group(1), propre)

    propre = propre.replace("**", "").replace("#", "")
    propre = re.sub(r"\s+", " ", propre).strip()
    if limite > 0 and len(propre) > limite:
        propre = propre[:limite].rstrip()
    return propre


def _cle_cache(texte_nettoye: str, voix: str) -> str:
    """Calcule une clé SHA-256 tronquée déterministe à partir de la voix et du texte nettoyé."""
    empreinte = hashlib.sha256(f"{voix}\n{texte_nettoye}".encode()).hexdigest()
    return empreinte[:24]


def _synthetiser_pyttsx3(texte: str, destination: Path) -> Path:
    """Génère un fichier WAV hors ligne via ``pyttsx3`` (exécuté dans un thread dédié).

    Args:
        texte: Texte déjà nettoyé.
        destination: Chemin cible du fichier ``.wav`` final.

    Returns:
        Le chemin ``destination`` écrit.

    Effets de bord:
        Initialise COM sous Windows sur le thread courant et écrit sur disque sous verrou.
    """
    with _pyttsx3_lock:
        pythoncom_mod = None
        if sys.platform == "win32":
            try:
                import pythoncom

                pythoncom.CoInitialize()
                pythoncom_mod = pythoncom
            except Exception:
                pythoncom_mod = None

        tmp_wav = destination.with_name(f"{destination.stem}.{uuid.uuid4().hex[:8]}.tmp.wav")
        try:
            moteur = pyttsx3.init()
            try:
                voix_dispos = moteur.getProperty("voices") or []
                for item in voix_dispos:
                    desc = f"{getattr(item, 'name', '')} {getattr(item, 'id', '')}".lower()
                    if "fr" in desc or "french" in desc or "hortense" in desc or "denise" in desc:
                        moteur.setProperty("voice", item.id)
                        break
            except Exception:
                pass
            moteur.save_to_file(texte, str(tmp_wav))
            moteur.runAndWait()
            if not tmp_wav.is_file() or tmp_wav.stat().st_size == 0:
                raise RuntimeError("pyttsx3 n'a produit aucun flux audio.")
            tmp_wav.replace(destination)
            return destination
        finally:
            tmp_wav.unlink(missing_ok=True)
            if pythoncom_mod is not None:
                try:
                    pythoncom_mod.CoUninitialize()
                except Exception:
                    pass


async def synthetiser(texte: str, voix: str = VOIX_DEFAUT) -> Path:
    """Synthétise un texte en fichier audio dans ``RUNTIME/tts/`` avec cache par hash.

    Utilise ``edge-tts`` en priorité (MP3), puis bascule sur ``pyttsx3`` (WAV) si ``edge-tts``
    échoue (ex. absence de réseau).

    Args:
        texte: Texte brut à lire (nettoyé du Markdown avant synthèse).
        voix: Nom de voix Edge TTS (défaut ``fr-FR-DeniseNeural``).

    Returns:
        Chemin absolu du fichier audio généré ou récupéré depuis le cache.

    Raises:
        ValueError: Si le texte est vide après nettoyage.
        RuntimeError: Si ``edge-tts`` et ``pyttsx3`` échouent tous les deux.

    Effets de bord:
        Crée ``RUNTIME/tts/``, peut contacter le service Edge TTS ou SAPI5, et écrit le cache.
    """
    voix_active = (voix or VOIX_DEFAUT).strip() or VOIX_DEFAUT
    propre = nettoyer_markdown_pour_tts(texte)
    if not propre:
        raise ValueError("Le texte à synthétiser est vide.")

    tts_dir = config.RUNTIME / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    cle = _cle_cache(propre, voix_active)
    chemin_mp3 = tts_dir / f"{cle}.mp3"
    chemin_wav = tts_dir / f"{cle}.wav"

    if chemin_mp3.is_file() and chemin_mp3.stat().st_size > 0:
        return chemin_mp3

    tmp_mp3 = tts_dir / f"{cle}.{uuid.uuid4().hex[:8]}.tmp.mp3"
    try:
        comm = edge_tts.Communicate(propre, voix_active)
        await comm.save(str(tmp_mp3))
        if not tmp_mp3.is_file() or tmp_mp3.stat().st_size == 0:
            raise RuntimeError("edge-tts a renvoyé un fichier audio vide.")
        tmp_mp3.replace(chemin_mp3)
        return chemin_mp3
    except Exception as exc:
        tmp_mp3.unlink(missing_ok=True)
        logger.warning("edge-tts indisponible (%s), bascule sur pyttsx3.", exc)

    if chemin_wav.is_file() and chemin_wav.stat().st_size > 0:
        return chemin_wav

    try:
        return await asyncio.to_thread(_synthetiser_pyttsx3, propre, chemin_wav)
    except Exception as exc:
        raise RuntimeError(f"Synthèse vocale impossible : {exc}") from exc
