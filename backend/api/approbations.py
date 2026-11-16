"""Routes API de consultation et de résolution des demandes d'approbation."""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.jarvis_engine import approvals
from backend.lifecycle import register_startup
from backend.ws_hub import hub

router = APIRouter(prefix="/approbations", tags=["approbations"])


@register_startup
def _enregistrer_ws_approbations() -> None:
    """Branche la réception WebSocket ``approbation_reponse`` dans le hub au démarrage."""
    hub.on_client_message("approbation_reponse", approvals.handle_ws_approbation_reponse)


class ApprobationDecisionRequest(BaseModel):
    """Corps de requête pour autoriser ou refuser une approbation en attente."""

    decision: Literal["oui", "non"]


@router.get("")
async def lister_approbations(statut: str | None = "en_attente") -> list[dict[str, Any]]:
    """Retourne les demandes d'approbation filtrées par statut (``en_attente`` par défaut)."""
    return await asyncio.to_thread(approvals.lister_approbations, statut)


@router.post("/{approbation_id}")
async def decider_approbation(
    approbation_id: str,
    body: ApprobationDecisionRequest,
) -> dict[str, Any]:
    """Enregistre la décision ``oui`` ou ``non`` pour une approbation et notifie le hub WS."""
    try:
        resultat = await approvals.repondre_approbation(approbation_id, body.decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if resultat is None:
        raise HTTPException(status_code=404, detail="Approbation introuvable")
    return resultat
