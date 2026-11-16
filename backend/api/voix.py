"""Routes API de transcription vocale (STT), écoute micro et synthèse vocale (TTS)."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import config, settings_store
from backend.lifecycle import register_startup
from backend.voice import pont_web, stt, tts
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voix", tags=["voix"])

_RE_BOUNDARY = re.compile(r'boundary=(?:"([^"]+)"|([^;\s]+))', re.IGNORECASE)
_RE_FIELD_NAME = re.compile(r'\bname=(?:"([^"]+)"|([^;\s]+))', re.IGNORECASE)
_RE_FILENAME = re.compile(r'\bfilename=(?:"([^"]*)"|([^;\s]+))', re.IGNORECASE)


TEXTE_TEST_VOIX_DEFAUT = "Bonjour, ici Jarvis. Ma voix est bien configurée."


class ParlerRequest(BaseModel):
    """Corps de requête pour la synthèse vocale ``POST /api/voix/parler``."""

    texte: str = Field(min_length=1)
    voix: str | None = None


class TesterVoixRequest(BaseModel):
    """Corps optionnel pour tester la synthèse vocale via ``POST /api/voix/tester``."""

    texte: str | None = None
    voix: str | None = None
    moteur_tts: str | None = None
    tts_url: str | None = None


class OuvrirPontWebRequest(BaseModel):
    """Corps optionnel pour ouvrir ou activer la fenêtre du pont web vocal."""

    service: Literal["chatgpt", "gemini"] | None = None
    navigateur: Literal["defaut", "chrome", "edge"] | None = None


async def _handle_ws_ecoute(ws: WebSocket, data: dict[str, Any]) -> None:
    """Traite les commandes WebSocket ``ecoute{action: debut|fin}`` de l'overlay."""
    del ws
    action = str(data.get("action") or "").strip().lower()
    if action == "fin":
        stt.arreter_ecoute()


@register_startup
def _enregistrer_handlers_voix() -> None:
    """Enregistre le handler WebSocket ``ecoute`` au démarrage du serveur."""
    hub.register_handler("ecoute", _handle_ws_ecoute)


def _extraire_fichier_multipart(
    content_type: str,
    body: bytes,
    champ: str = "audio",
) -> tuple[str, bytes] | None:
    """Extrait le nom de fichier et les octets d'un champ ``multipart/form-data``.

    Parse directement la charge utile RFC 7578 sans nécessiter ``python-multipart``.

    Args:
        content_type: Valeur brute de l'en-tête HTTP ``Content-Type``.
        body: Corps binaire complet de la requête HTTP.
        champ: Nom du champ formulaire recherché (``"audio"`` par défaut).

    Returns:
        Tuple ``(nom_fichier, octets)`` ou ``None`` si le champ est absent.
    """
    match_boundary = _RE_BOUNDARY.search(content_type)
    if not match_boundary or not body:
        return None

    boundary = match_boundary.group(1) or match_boundary.group(2)
    if not boundary:
        return None

    delim = b"--" + boundary.encode("latin-1", errors="ignore")
    parties = body.split(delim)
    for partie in parties[1:]:
        if partie.startswith(b"--"):
            break
        if partie.startswith(b"\r\n"):
            partie = partie[2:]
        elif partie.startswith(b"\n"):
            partie = partie[1:]

        sep_idx = partie.find(b"\r\n\r\n")
        sep_len = 4
        if sep_idx == -1:
            sep_idx = partie.find(b"\n\n")
            sep_len = 2
        if sep_idx == -1:
            continue

        entetes_txt = partie[:sep_idx].decode("utf-8", errors="replace")
        donnees = partie[sep_idx + sep_len :]
        if donnees.endswith(b"\r\n"):
            donnees = donnees[:-2]
        elif donnees.endswith(b"\n"):
            donnees = donnees[:-1]

        for ligne in entetes_txt.splitlines():
            if not ligne.lower().startswith("content-disposition:"):
                continue
            m_name = _RE_FIELD_NAME.search(ligne)
            if not m_name:
                continue
            nom_champ = m_name.group(1) or m_name.group(2)
            if nom_champ != champ:
                continue
            m_file = _RE_FILENAME.search(ligne)
            nom_fichier = (m_file.group(1) or m_file.group(2) or "") if m_file else ""
            return (nom_fichier or "audio.wav"), donnees

    return None


def _extension_sure(nom_fichier: str) -> str:
    """Retourne une extension audio sûre (ex. ``.wav``, ``.webm``) pour le fichier temporaire."""
    suffixe = Path(nom_fichier).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{1,8}", suffixe):
        return suffixe
    return ".wav"


@router.post("/transcrire")
async def transcrire_audio(request: Request) -> dict[str, str]:
    """Transcrit un fichier audio envoyé en ``multipart/form-data`` sous le champ ``audio``.

    Args:
        request: Requête HTTP contenant le fichier audio uploadé.

    Returns:
        Dictionnaire ``{"texte": ...}``.

    Raises:
        HTTPException: 400 si le format ou le champ ``audio`` manque, 500 si la transcription échoue.
    """
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type.lower():
        raise HTTPException(
            status_code=400,
            detail="Requête multipart/form-data attendue avec le champ 'audio'.",
        )

    body = await request.body()
    extrait = _extraire_fichier_multipart(content_type, body, champ="audio")
    if extrait is None:
        raise HTTPException(status_code=400, detail="Champ 'audio' manquant dans la requête.")

    nom_fichier, octets = extrait
    if not octets:
        raise HTTPException(status_code=400, detail="Le fichier audio transmis est vide.")

    dossier_voix = config.RUNTIME / "voice"
    dossier_voix.mkdir(parents=True, exist_ok=True)
    tmp_path = dossier_voix / f"upload-{uuid.uuid4().hex[:10]}{_extension_sure(nom_fichier)}"
    try:
        await asyncio.to_thread(tmp_path.write_bytes, octets)
        texte = await asyncio.to_thread(stt.transcrire, tmp_path)
    except EOFError as exc:  # av.error.EOFError hérite d'EOFError : fichier sans audio exploitable
        logger.warning("Enregistrement vide ou trop court (%d octets) : %s", len(octets), exc)
        raise HTTPException(
            status_code=400, detail="Enregistrement vide ou trop court : parle un peu plus longtemps."
        ) from exc
    except Exception as exc:
        logger.exception("Erreur lors de la transcription audio")
        raise HTTPException(status_code=500, detail=f"Erreur de transcription : {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"texte": texte}


@router.post("/ecouter")
async def ecouter_micro() -> dict[str, str]:
    """Enregistre le microphone du PC dans un thread puis transcrit la parole en français.

    Diffuse ``etat_jarvis{etat:"ecoute"}`` pendant la capture, ``etat_jarvis{etat:"reflexion"}``
    pendant la transcription, puis revient à ``repos``.

    Returns:
        Dictionnaire ``{"texte": ...}``.

    Raises:
        HTTPException: 503 si le microphone est indisponible (``Micro indisponible : …``).
    """
    chemin_wav: Path | None = None
    await hub.broadcast("etat_jarvis", {"etat": "ecoute"})
    try:
        try:
            chemin_wav = await asyncio.to_thread(stt.enregistrer_micro)
        except stt.MicroIndisponibleError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Micro indisponible : {exc}") from exc

        await hub.broadcast("etat_jarvis", {"etat": "reflexion"})
        try:
            texte = await asyncio.to_thread(stt.transcrire, chemin_wav)
        except Exception as exc:
            logger.exception("Erreur de transcription après écoute micro")
            raise HTTPException(status_code=500, detail=f"Erreur de transcription : {exc}") from exc
        return {"texte": texte}
    finally:
        if chemin_wav is not None:
            chemin_wav.unlink(missing_ok=True)
        await hub.broadcast("etat_jarvis", {"etat": "repos"})


@router.post("/parler")
async def parler_texte(body: ParlerRequest) -> dict[str, str]:
    """Synthétise ``body.texte`` en audio, expose l'URL et diffuse ``voix_parler`` sur le hub.

    Args:
        body: Texte à prononcer et voix optionnelle.

    Returns:
        Dictionnaire ``{"url": "/api/voix/audio/<fichier>"}``.

    Raises:
        HTTPException: 400 si le texte est vide après nettoyage, 503 si la synthèse échoue.
    """
    voix_cfg = settings_store.lire().get("voix")
    voix_defaut = (
        str(voix_cfg.get("nom") or tts.VOIX_DEFAUT)
        if isinstance(voix_cfg, dict)
        else tts.VOIX_DEFAUT
    )
    voix_choisie = (body.voix or voix_defaut).strip() or tts.VOIX_DEFAUT

    try:
        chemin = await tts.synthetiser(body.texte, voix=voix_choisie)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Échec de synthèse vocale")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    url = f"/api/voix/audio/{chemin.name}"
    await hub.broadcast("voix_parler", {"url": url, "texte": body.texte})
    return {"url": url}


@router.get("/audio/{nom}")
async def obtenir_audio(nom: str) -> FileResponse:
    """Sert un fichier audio synthétisé stocké dans ``RUNTIME/tts/``.

    Args:
        nom: Nom de fichier audio (sans chemin relatif ni traversée).

    Returns:
        Réponse fichier audio MP3 ou WAV.

    Raises:
        HTTPException: 400 si le nom de fichier est invalide, 404 s'il n'existe pas.
    """
    if not nom or Path(nom).name != nom or "/" in nom or "\\" in nom or ".." in nom:
        raise HTTPException(status_code=400, detail="Nom de fichier audio invalide.")

    tts_dir = (config.RUNTIME / "tts").resolve()
    cible = (tts_dir / nom).resolve()
    if not cible.is_relative_to(tts_dir):
        raise HTTPException(status_code=400, detail="Chemin audio hors répertoire autorisé.")
    if not cible.is_file():
        raise HTTPException(status_code=404, detail="Fichier audio introuvable.")

    suffixe = cible.suffix.lower()
    if suffixe == ".mp3":
        media_type = "audio/mpeg"
    elif suffixe == ".wav":
        media_type = "audio/wav"
    else:
        media_type = "application/octet-stream"
    return FileResponse(cible, media_type=media_type)


@router.get("/micros")
async def lister_microphones() -> list[dict[str, Any]]:
    """Liste les périphériques d'entrée audio détectés sur la machine.

    Returns:
        Liste ``[{"index": int, "nom": str, "defaut": bool}]``.
    """
    return await asyncio.to_thread(stt.lister_micros)


@router.get("/etat")
async def lire_etat_voix() -> dict[str, Any]:
    """Retourne l'état courant du pipeline vocal (modèle STT, périphérique, moteur TTS).

    Returns:
        Dictionnaire ``{"stt_modele", "stt_peripherique", "stt_charge", "tts_moteur", "tts_http_joignable"}``.
    """
    etat_stt = await asyncio.to_thread(stt.etat_stt)
    voix_cfg = settings_store.lire().get("voix")
    cfg_dict = voix_cfg if isinstance(voix_cfg, dict) else {}

    tts_moteur = str(cfg_dict.get("moteur_tts") or "edge").strip().lower() or "edge"
    tts_url = cfg_dict.get("tts_url")
    if tts_moteur == "http":
        tts_http_joignable: bool | None = await tts.tester_tts_http(
            str(tts_url) if isinstance(tts_url, str) else None
        )
    else:
        tts_http_joignable = None

    return {
        "stt_modele": etat_stt["stt_modele"],
        "stt_peripherique": etat_stt["stt_peripherique"],
        "stt_charge": bool(etat_stt["stt_charge"]),
        "tts_moteur": tts_moteur,
        "tts_http_joignable": tts_http_joignable,
    }


@router.post("/tester")
async def tester_voix(body: TesterVoixRequest | None = None) -> FileResponse:
    """Synthétise une courte phrase de test et renvoie directement le flux audio.

    Args:
        body: Texte et surcharges TTS optionnelles (``voix``, ``moteur_tts``, ``tts_url``).

    Returns:
        Fichier audio ``audio/mpeg`` ou ``audio/wav`` prêt à être lu par le navigateur.

    Raises:
        HTTPException: 400 si le texte est invalide, 503 si la synthèse vocale échoue.
    """
    req = body or TesterVoixRequest()
    texte_effectif = (req.texte or "").strip() or TEXTE_TEST_VOIX_DEFAUT

    voix_cfg = settings_store.lire().get("voix")
    cfg_dict = voix_cfg if isinstance(voix_cfg, dict) else {}
    voix_choisie = (req.voix or str(cfg_dict.get("nom") or "") or tts.VOIX_DEFAUT).strip() or tts.VOIX_DEFAUT

    kwargs: dict[str, Any] = {"voix": voix_choisie}
    if req.moteur_tts is not None:
        kwargs["moteur_tts"] = req.moteur_tts
    if req.tts_url is not None:
        kwargs["tts_url"] = req.tts_url

    try:
        chemin = await tts.synthetiser(texte_effectif, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Échec du test de synthèse vocale")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    media_type = "audio/wav" if chemin.suffix.lower() == ".wav" else "audio/mpeg"
    return FileResponse(chemin, media_type=media_type)


@router.post("/web/ouvrir")
async def ouvrir_pont_web(body: OuvrirPontWebRequest | None = None) -> dict[str, Any]:
    """Ouvre ou ramène au premier plan la fenêtre ChatGPT ou Gemini du pont web.

    N'effectue aucun pilotage du site : seules les deux URL fixes de la liste blanche
    (ChatGPT et Gemini) peuvent être ouvertes.

    Args:
        body: Surcharges optionnelles ``service`` et ``navigateur`` ; par défaut, utilise
            ``voix.web_service`` et ``voix.web_navigateur`` depuis ``parametres.json``.

    Returns:
        Dictionnaire ``{"ok": bool, "action": "premier_plan" | "ouverture", "service": str}``.

    Raises:
        HTTPException: 400 si le service ou navigateur est invalide, 503 si l'ouverture échoue.

    Effets de bord:
        Place la fenêtre correspondante au premier plan ou lance le navigateur avec l'URL autorisée.
    """
    req = body or OuvrirPontWebRequest()
    voix_cfg = settings_store.lire().get("voix")
    cfg_dict = voix_cfg if isinstance(voix_cfg, dict) else {}

    service = str(req.service or cfg_dict.get("web_service") or "chatgpt").strip().lower()
    navigateur = str(req.navigateur or cfg_dict.get("web_navigateur") or "defaut").strip().lower()

    try:
        return await asyncio.to_thread(
            pont_web.ouvrir_service_web,
            service=service,
            navigateur=navigateur,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Échec d'ouverture du pont web (%s / %s)", service, navigateur)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
