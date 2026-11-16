"""Routes API de gestion des souvenirs persistants (``memories.json`` / ``SOUVENIRS.md``)."""

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.brain import memories
from backend.brain.memories import _cle_dedup

router = APIRouter(prefix="/memoires", tags=["memoires"])


class MemoireCreateRequest(BaseModel):
    """Corps de requête pour l'ajout d'un souvenir."""

    type: Literal["preference", "correction", "regle", "fait"]
    texte: str = Field(min_length=1)
    source: str | None = None
    agent: str | None = None


@router.get("")
async def lister_memoires() -> list[dict[str, Any]]:
    """Retourne la liste des souvenirs persistés dans ``second-brain/memories.json``."""
    return await asyncio.to_thread(memories.lister_souvenirs)


@router.post("")
async def ajouter_memoire(body: MemoireCreateRequest) -> dict[str, Any]:
    """Ajoute un souvenir dédupliqué et régénère ``SOUVENIRS.md``."""
    try:
        cree = await asyncio.to_thread(
            memories.ajouter_souvenir,
            body.type,
            body.texte,
            body.source or "manuel",
            body.agent or "jarvis",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if cree is not None:
        return cree

    cle = _cle_dedup(body.texte)
    for existant in await asyncio.to_thread(memories.lister_souvenirs):
        if _cle_dedup(str(existant.get("texte") or "")) == cle:
            return {**existant, "doublon": True}
    return {"doublon": True}


@router.delete("/{souvenir_id}")
async def supprimer_memoire(souvenir_id: str) -> dict[str, Any]:
    """Supprime un souvenir par son identifiant et régénère ``SOUVENIRS.md``."""
    supprime = await asyncio.to_thread(memories.supprimer_souvenir, souvenir_id)
    if not supprime:
        raise HTTPException(status_code=404, detail="Souvenir introuvable")
    return {"ok": True, "id": souvenir_id}
