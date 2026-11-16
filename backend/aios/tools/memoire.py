"""Outil de mémorisation durable dans ``second-brain/memories.json`` et ``SOUVENIRS.md``."""

import asyncio
from typing import Any

from backend.aios.tools_registry import ContexteOutil, outil
from backend.brain import memories


@outil(
    nom="memoriser",
    description="Enregistre un souvenir durable (préférence, correction, règle ou fait).",
    parametres={
        "type": "str — preference | correction | regle | fait",
        "texte": "str — énoncé clair et concis à mémoriser",
    },
    risque="ecriture",
)
async def memoriser(ctx: ContexteOutil, type: str, texte: str) -> dict[str, Any]:
    """Ajoute un souvenir dédupliqué pour l'agent courant."""
    cree = await asyncio.to_thread(
        memories.ajouter_souvenir,
        type,
        texte,
        ctx.conversation_id or "outil",
        ctx.agent_id,
    )
    if cree is None:
        return {"ajoute": False, "raison": "Souvenir déjà présent (dédupliqué)"}
    return {"ajoute": True, "souvenir": cree}
