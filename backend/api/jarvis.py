"""Routes API du moteur Jarvis : captures écran, fenêtre active, Professeur, PC et test overlay."""

from __future__ import annotations

import asyncio
import ctypes
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import config
from backend.aios import tools_registry
from backend.jarvis_engine import dpi, hands, teacher, uia, vision
from backend.lifecycle import register_shutdown, register_startup
from backend.ws_hub import hub

router = APIRouter(tags=["jarvis"])


@register_startup
def _demarrer_jarvis_engine() -> None:
    """Active le DPI Per-Monitor V2, charge les outils et branche ``guide_clic`` WS."""
    dpi.activer()
    hub.on_client_message("guide_clic", teacher.handle_ws_guide_clic)
    tools_registry.charger_outils()


@register_shutdown
def _arreter_jarvis_engine() -> None:
    """Interrompt proprement les guidages Professeur actifs lors de l'arrêt du serveur."""
    teacher.reset_etat_pour_tests()


class CaptureRequest(BaseModel):
    """Paramètres optionnels de capture d'écran."""

    ecran: int = 1
    largeur_max: int = 1280


class ProfesseurTrouverRequest(BaseModel):
    """Requête de localisation visuelle/UIA d'un élément d'interface."""

    objectif: str = Field(min_length=1)


class ProfesseurGuiderRequest(BaseModel):
    """Requête de démarrage d'un guidage visuel pas-à-pas en mode Professeur."""

    objectif: str = Field(min_length=1)
    etapes: list[str] | None = None


class ActionPCRequest(BaseModel):
    """Requête d'exécution d'une action PC sécurisée par les garde-fous."""

    action: dict[str, Any]
    attendu: dict[str, Any] | None = None


def _geometrie_ecran_principal() -> tuple[int, int, int, int]:
    """Retourne ``(x, y, largeur, hauteur)`` physiques de l'écran principal avec repli."""
    try:
        import mss

        with mss.mss() as sct:
            moniteurs = sct.monitors
            if moniteurs:
                mon = moniteurs[1] if len(moniteurs) > 1 else moniteurs[0]
                return (
                    int(mon.get("left", 0)),
                    int(mon.get("top", 0)),
                    max(1, int(mon.get("width", 1920))),
                    max(1, int(mon.get("height", 1080))),
                )
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0) or 1920)
            h = int(user32.GetSystemMetrics(1) or 1080)
            return 0, 0, max(1, w), max(1, h)
        except Exception:
            pass

    return 0, 0, 1920, 1080


@router.post("/ecran/capture")
async def capturer_ecran(body: CaptureRequest | None = Body(default=None)) -> dict[str, Any]:  # noqa: B008
    """Prend une capture d'écran PNG dans ``RUNTIME/captures/`` et renvoie son URL sécurisée."""
    ecran = body.ecran if body is not None else 1
    largeur_max = body.largeur_max if body is not None else 1280
    try:
        cap = await asyncio.to_thread(vision.capturer, ecran, largeur_max)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Capture d'écran impossible : {exc}") from exc

    nom = cap.chemin.name
    return {
        "nom": nom,
        "url": f"/api/ecran/image/{nom}",
        "largeur": cap.largeur,
        "hauteur": cap.hauteur,
    }


@router.get("/ecran/image/{nom}")
async def lire_image_capture(nom: str, token: str | None = None) -> FileResponse:
    """Sert une capture PNG depuis ``RUNTIME/captures/`` (authentification jeton ou ``?token=``)."""
    del token
    if not nom or Path(nom).name != nom or "/" in nom or "\\" in nom or ".." in nom:
        raise HTTPException(status_code=400, detail="Nom de capture invalide.")

    dossier = (config.RUNTIME / "captures").resolve()
    cible = (dossier / nom).resolve()
    if not cible.is_relative_to(dossier) or cible.parent != dossier:
        raise HTTPException(status_code=400, detail="Chemin de capture hors répertoire autorisé.")
    if not cible.is_file():
        raise HTTPException(status_code=404, detail="Capture introuvable.")

    return FileResponse(cible, media_type="image/png")


@router.get("/ecran/fenetre")
async def obtenir_fenetre_active() -> dict[str, Any]:
    """Retourne les informations de la fenêtre Windows actuellement au premier plan."""
    return await asyncio.to_thread(uia.fenetre_active)


@router.post("/professeur/trouver")
async def professeur_trouver(body: ProfesseurTrouverRequest) -> dict[str, Any]:
    """Localise un élément d'interface à l'écran via UIA + OCR + repli vision."""
    try:
        return await teacher.trouver(body.objectif)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur de localisation : {exc}") from exc


@router.post("/professeur/guider")
async def professeur_guider(body: ProfesseurGuiderRequest) -> dict[str, str]:
    """Démarre un guidage visuel pas-à-pas sur le calque HUD et renvoie son ``guide_id``."""
    guide_id = teacher.demarrer_guidage(body.objectif, etapes=body.etapes)
    return {"guide_id": guide_id}


@router.post("/pc/action")
async def executer_action_pc(body: ActionPCRequest) -> dict[str, Any]:
    """Exécute une action PC sécurisée avec vérifications avant/après et garde-fous."""
    return await hands.executer_action(body.action, body.attendu)


@router.post("/overlay/test")
async def tester_overlay() -> dict[str, Any]:
    """Diffuse une boîte centrée « Test du calque · 1/1 », une narration et une carte HUD de test."""
    ox, oy, sw, sh = await asyncio.to_thread(_geometrie_ecran_principal)
    w_box = min(360, max(120, sw // 3))
    h_box = min(140, max(80, sh // 5))
    x_box = ox + (sw - w_box) // 2
    y_box = oy + (sh - h_box) // 2

    guide_id = f"test-{uuid.uuid4().hex[:8]}"
    boite = {
        "x": x_box,
        "y": y_box,
        "w": w_box,
        "h": h_box,
        "label": "Test du calque · 1/1",
        "couleur": "#22d3ee",
    }
    await hub.broadcast(
        "guide",
        {
            "id": guide_id,
            "boites": [boite],
            "attendre_clic": False,
        },
    )
    await hub.broadcast(
        "narration",
        {
            "texte": "Test du calque overlay : boîte centrale et carte de démonstration affichées.",
            "niveau": "info",
        },
    )
    carte = {
        "id": f"carte-test-{uuid.uuid4().hex[:8]}",
        "titre": "Test du calque HUD",
        "contenu_md": (
            "Le calque overlay transparent est actif.\n\n"
            "- Boîte de guidage centrée (`Test du calque · 1/1`)\n"
            "- Bandeau de narration temps réel\n"
            "- Carte HUD de démonstration"
        ),
        "type": "info",
    }
    await hub.broadcast("carte", carte)

    return {"ok": True, "guide_id": guide_id, "boite": boite, "carte": carte}
