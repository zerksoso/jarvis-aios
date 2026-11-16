"""Routes API de gestion des mises à jour GitHub (``/api/maj/*``)."""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.update import service
from backend.update.downloader import ErreurMiseAJour

router = APIRouter(prefix="/maj", tags=["maj"])


class MajModeRequest(BaseModel):
    """Corps de requête pour modifier la politique de mise à jour."""

    mode: Literal["demander", "auto", "jamais"]


@router.get("/etat")
async def lire_etat_maj() -> dict[str, Any]:
    """Retourne la version locale, la dernière release détectée, la disponibilité et le mode.

    Returns:
        Dictionnaire ``{"version", "derniere", "disponible", "verifie_le", "mode"}``.
    """
    return service.etat_courant()


@router.post("/verifier")
async def verifier_maj() -> dict[str, Any]:
    """Interroge immédiatement GitHub Releases et met à jour l'état des mises à jour.

    Returns:
        Nouvel état des mises à jour (format ``GET /api/maj/etat``).

    Raises:
        HTTPException: 502 si GitHub est inaccessible ou la release invalide.

    Effets de bord:
        Émet une requête HTTPS vers GitHub et diffuse ``maj_disponible`` en WS si une
        version plus récente est détectée.
    """
    try:
        return await service.verifier(forcer=True)
    except (ErreurMiseAJour, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/installer")
async def installer_maj() -> dict[str, Any]:
    """Démarre en tâche de fond le téléchargement et l'installation de la mise à jour disponible.

    Refuse explicitement en mode développement (``APP_DIR/installed.flag`` absent).

    Returns:
        Confirmation ``{"ok": True, "job_id": ..., "statut": "en_cours"}``.

    Raises:
        HTTPException: 409 si le serveur tourne en mode dev ou aucune mise à jour n'est prête.

    Effets de bord:
        Démarre une tâche de fond téléchargeant et appliquant la mise à jour.
    """
    try:
        return await service.demarrer_installation()
    except ErreurMiseAJour as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/progression")
async def lire_progression_maj() -> dict[str, Any]:
    """Retourne la progression courante du téléchargement et de l'installation.

    Returns:
        Dictionnaire ``{"etape": str, "pourcent": int, "erreur": str | None}``.
    """
    return service.progression_courante()


@router.post("/mode")
async def modifier_mode_maj(body: MajModeRequest) -> dict[str, Any]:
    """Enregistre la politique de mise à jour (``demander``, ``auto`` ou ``jamais``).

    Args:
        body: Nouveau mode souhaité.

    Returns:
        ``{"ok": True, "mode": str}``.

    Effets de bord:
        Persiste ``maj.mode`` dans ``RUNTIME/parametres.json``.
    """
    try:
        mode_enregistre = service.changer_mode(body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "mode": mode_enregistre}
