"""Routes d'introspection des moteurs LLM, du catalogue et des quotas."""

import asyncio
from typing import Any

from fastapi import APIRouter

from backend.llm import quota
from backend.llm import router as llm_router

router = APIRouter(tags=["moteurs"])


@router.get("/moteurs")
async def lire_moteurs() -> dict[str, Any]:
    """Retourne le catalogue public des modèles ainsi que l'état des backends.

    Returns:
        Dictionnaire ``{"catalogue": [...], "moteurs": {...}}``.
    """
    return {
        "catalogue": llm_router.catalogue_public(),
        "moteurs": await llm_router.etat_moteurs(),
    }


@router.get("/quotas")
async def lire_quotas() -> dict[str, Any]:
    """Retourne les quotas Antigravity courants et l'estimation 5 h de Claude CLI.

    Returns:
        Dictionnaire regroupant les buckets agy et ``claude_cli``.
    """
    donnees = await quota.lire_quotas()
    resultat = dict(donnees)
    if "claude_cli" not in resultat:
        resultat["claude_cli"] = await asyncio.to_thread(quota.estimation_claude_cli)
    return resultat
