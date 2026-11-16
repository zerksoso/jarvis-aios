"""Synthèse vocale TTS : edge-tts, serveur HTTP local (OpenAI-compatible) et repli pyttsx3."""

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
import httpx
import pyttsx3

from backend import config, settings_store

logger = logging.getLogger(__name__)

VOIX_DEFAUT = "fr-FR-HenriNeural"
MOTEURS_TTS_VALIDES: tuple[str, ...] = ("edge", "windows", "http")
MAX_CARACTERES_TTS = 1200

_pyttsx3_lock = threading.Lock()
_http_transport_test: httpx.AsyncBaseTransport | None = None

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


def set_http_transport_for_tests(transport: httpx.AsyncBaseTransport | None) -> None:
    """Injecte un transport ``httpx`` simulé pour les tests du moteur TTS HTTP.

    Args:
        transport: Instance ``httpx.AsyncBaseTransport`` (ex. ``httpx.MockTransport``)
            ou ``None`` pour revenir au réseau réel.
    """
    global _http_transport_test
    _http_transport_test = transport


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


def _cle_cache(texte_nettoye: str, cle_voix: str) -> str:
    """Calcule une clé SHA-256 tronquée déterministe à partir du contexte vocal et du texte."""
    empreinte = hashlib.sha256(f"{cle_voix}\n{texte_nettoye}".encode()).hexdigest()
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


async def _synthetiser_http(
    texte: str,
    voix: str,
    tts_url: str,
    tts_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Appelle un serveur TTS local compatible OpenAI (``POST <tts_url>/v1/audio/speech``)."""
    base = tts_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ValueError("URL TTS HTTP invalide (http:// ou https:// requis).")

    cle = _cle_cache(texte, f"http:{base}:{voix}")
    chemin_mp3 = tts_dir / f"{cle}.mp3"
    chemin_wav = tts_dir / f"{cle}.wav"
    if chemin_mp3.is_file() and chemin_mp3.stat().st_size > 0:
        return chemin_mp3
    if chemin_wav.is_file() and chemin_wav.stat().st_size > 0:
        return chemin_wav

    transport_actif = transport or _http_transport_test
    async with httpx.AsyncClient(timeout=15.0, transport=transport_actif) as client:
        reponse = await client.post(
            f"{base}/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": texte,
                "voice": voix,
                "response_format": "mp3",
            },
        )
        reponse.raise_for_status()
        octets = reponse.content
        content_type = str(reponse.headers.get("content-type") or "").lower()

    if not octets:
        raise RuntimeError("Le serveur TTS HTTP a renvoyé une réponse audio vide.")

    ext = ".wav" if "wav" in content_type else ".mp3"
    cible = tts_dir / f"{cle}{ext}"
    tmp = tts_dir / f"{cle}.{uuid.uuid4().hex[:8]}.tmp{ext}"
    try:
        tmp.write_bytes(octets)
        tmp.replace(cible)
        return cible
    finally:
        tmp.unlink(missing_ok=True)


async def tester_tts_http(
    tts_url: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Teste rapidement si un serveur TTS HTTP local répond.

    Args:
        tts_url: URL de base du serveur TTS local (ex. ``http://127.0.0.1:8880``).
        transport: Transport ``httpx`` optionnel pour les tests unitaires.

    Returns:
        ``True`` si le serveur répond avec un code HTTP < 500, sinon ``False``.
    """
    base = (tts_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        return False

    transport_actif = transport or _http_transport_test
    try:
        async with httpx.AsyncClient(timeout=2.0, transport=transport_actif) as client:
            try:
                rep = await client.get(f"{base}/v1/models")
            except httpx.HTTPError:
                rep = await client.get(base)
            return rep.status_code < 500
    except Exception:
        return False


def _resoudre_options_tts(
    voix: str | None,
    moteur_tts: str | None,
    tts_url: str | None,
) -> tuple[str, str, str | None]:
    """Résout ``(voix_active, moteur_tts, tts_url)`` depuis les arguments ou ``settings_store``."""
    try:
        voix_cfg = settings_store.lire().get("voix")
    except Exception:
        voix_cfg = None

    cfg_dict = voix_cfg if isinstance(voix_cfg, dict) else {}
    voix_active = (voix or str(cfg_dict.get("nom") or "") or VOIX_DEFAUT).strip() or VOIX_DEFAUT
    moteur_brut = (moteur_tts or str(cfg_dict.get("moteur_tts") or "") or "edge").strip().lower()
    moteur_actif = moteur_brut if moteur_brut in MOTEURS_TTS_VALIDES else "edge"
    url_raw = tts_url if tts_url is not None else cfg_dict.get("tts_url")
    url_active = str(url_raw).strip() if isinstance(url_raw, str) and url_raw.strip() else None
    return voix_active, moteur_actif, url_active


async def synthetiser(
    texte: str,
    voix: str | None = None,
    *,
    moteur_tts: str | None = None,
    tts_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Path:
    """Synthétise un texte en fichier audio dans ``RUNTIME/tts/`` avec cache par hash.

    Prend en charge trois moteurs configurables via ``voix.moteur_tts`` :
    - ``"http"`` : serveur local compatible OpenAI (``POST <tts_url>/v1/audio/speech``),
      avec repli automatique sur ``edge-tts`` puis ``pyttsx3`` si injoignable.
    - ``"edge"`` : ``edge-tts`` (MP3) avec repli ``pyttsx3`` (WAV).
    - ``"windows"`` : synthèse locale ``pyttsx3`` (WAV).

    Args:
        texte: Texte brut à lire (nettoyé du Markdown avant synthèse).
        voix: Nom de voix TTS (défaut ``fr-FR-HenriNeural`` ou paramètre ``voix.nom``).
        moteur_tts: Surcharge explicite du moteur (``edge``, ``windows``, ``http``).
        tts_url: Surcharge explicite de l'URL du serveur TTS HTTP.
        transport: Transport ``httpx`` optionnel pour tester le mode ``http``.

    Returns:
        Chemin absolu du fichier audio généré ou récupéré depuis le cache.

    Raises:
        ValueError: Si le texte est vide après nettoyage.
        RuntimeError: Si tous les moteurs tentés échouent.

    Effets de bord:
        Crée ``RUNTIME/tts/``, peut contacter un serveur HTTP local, Edge TTS ou SAPI5,
        et écrit le cache disque.
    """
    voix_active, moteur_actif, url_active = _resoudre_options_tts(voix, moteur_tts, tts_url)
    propre = nettoyer_markdown_pour_tts(texte)
    if not propre:
        raise ValueError("Le texte à synthétiser est vide.")

    tts_dir = config.RUNTIME / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)

    if moteur_actif == "windows":
        cle_win = _cle_cache(propre, f"windows:{voix_active}")
        chemin_win = tts_dir / f"{cle_win}.wav"
        if chemin_win.is_file() and chemin_win.stat().st_size > 0:
            return chemin_win
        try:
            return await asyncio.to_thread(_synthetiser_pyttsx3, propre, chemin_win)
        except Exception as exc:
            raise RuntimeError(f"Synthèse vocale Windows impossible : {exc}") from exc

    if moteur_actif == "http":
        if url_active:
            try:
                return await _synthetiser_http(
                    propre,
                    voix_active,
                    url_active,
                    tts_dir,
                    transport=transport,
                )
            except Exception as exc:
                logger.warning("Serveur TTS HTTP indisponible (%s), repli sur edge-tts.", exc)
        else:
            logger.warning("moteur_tts='http' sans tts_url configurée, repli sur edge-tts.")

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
