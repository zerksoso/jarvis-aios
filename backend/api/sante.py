"""Route de vérification d'état de santé du serveur Jarvis."""

from typing import Any

from fastapi import APIRouter

from backend import __version__
from backend.llm import router as llm_router
from backend.ws_hub import hub

router = APIRouter(tags=["sante"])


@router.get("/sante")
async def lire_sante() -> dict[str, Any]:
    """Retourne l'état général du serveur, la version, l'état overlay et des moteurs.

    Returns:
        Dictionnaire ``{ok, version, overlay_connecte, moteurs}``.
    """
    etats = await llm_router.etat_moteurs()
    return {
        "ok": True,
        "version": __version__,
        "overlay_connecte": hub.overlay_connecte,
        "moteurs": {
            "agy": bool(etats["agy"]["ok"]),
            "claude_cli": etats["claude_cli"],
            "codex": bool(etats["codex"]["ok"]),
        },
    }
