"""Routes API de gestion des canaux multi-agents et consultation de leurs messages."""

import asyncio
import json
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend import db
from backend.agents import channel_orchestrator
from backend.lifecycle import register_startup

router = APIRouter(prefix="/canaux", tags=["canaux"])


@register_startup
async def _initialiser_canaux() -> None:
    """Crée les canaux initiaux ``general`` et ``veille`` s'ils n'existent pas encore."""
    await asyncio.to_thread(channel_orchestrator.initialiser_canaux_initiaux)


class CanalCreateRequest(BaseModel):
    """Schéma de création d'un nouveau canal multi-agents."""

    id: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    description: str = ""
    membres: list[str] = Field(default_factory=lambda: ["jarvis"])
    coordinateur: str = "jarvis"
    max_tours_par_agent: int = Field(default=2, ge=1, le=10)
    routines: list[str] = Field(default_factory=list)
    cree_le: str | None = None
    memoire_md: str | None = None


class CanalUpdateRequest(BaseModel):
    """Schéma de mise à jour d'un canal existant."""

    nom: str | None = None
    description: str | None = None
    membres: list[str] | None = None
    coordinateur: str | None = None
    max_tours_par_agent: int | None = Field(default=None, ge=1, le=10)
    routines: list[str] | None = None
    memoire_md: str | None = None


@router.get("")
async def lister_canaux() -> list[dict[str, Any]]:
    """Liste tous les canaux disponibles avec leur mémoire Markdown."""
    return await asyncio.to_thread(channel_orchestrator.lister_canaux)


@router.get("/{canal_id}")
async def obtenir_canal(canal_id: str) -> dict[str, Any]:
    """Retourne le détail d'un canal et sa mémoire ``memoire_md``."""
    try:
        canal = await asyncio.to_thread(channel_orchestrator.charger_canal, canal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if canal is None:
        raise HTTPException(status_code=404, detail="Canal introuvable")
    return canal.model_dump()


@router.post("")
async def creer_canal(body: CanalCreateRequest) -> dict[str, Any]:
    """Crée un nouveau canal ``canaux/<id>/canal.json`` et son fichier ``memoire.md``."""
    donnees = body.model_dump(exclude_none=True)
    try:
        canal = await asyncio.to_thread(channel_orchestrator.enregistrer_canal, donnees, False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return canal.model_dump()


@router.put("/{canal_id}")
async def modifier_canal(canal_id: str, body: CanalUpdateRequest) -> dict[str, Any]:
    """Met à jour un canal existant et sa mémoire."""
    try:
        existant = await asyncio.to_thread(channel_orchestrator.charger_canal, canal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if existant is None:
        raise HTTPException(status_code=404, detail="Canal introuvable")

    fusion = existant.model_dump()
    patch = body.model_dump(exclude_unset=True)
    fusion.update(patch)
    fusion["id"] = canal_id

    try:
        maj = await asyncio.to_thread(channel_orchestrator.enregistrer_canal, fusion, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return maj.model_dump()


@router.delete("/{canal_id}")
async def supprimer_canal(canal_id: str) -> dict[str, Any]:
    """Envoie le dossier d'un canal à la corbeille système."""
    try:
        supprime = await asyncio.to_thread(channel_orchestrator.supprimer_canal, canal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not supprime:
        raise HTTPException(status_code=404, detail="Canal introuvable")
    return {"ok": True, "id": canal_id}


@router.get("/{canal_id}/messages")
async def lister_messages_canal(
    canal_id: str,
    limite: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[dict[str, Any]]:
    """Retourne les messages récents d'un canal dans l'ordre chronologique."""
    try:
        slug = channel_orchestrator.valider_id_canal(canal_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    conv_id = f"canal:{slug}"
    lignes = await db.async_query(
        """
        SELECT id, conversation_id, role, auteur, contenu, meta, cree_le
        FROM (
            SELECT id, conversation_id, role, auteur, contenu, meta, cree_le
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) sub
        ORDER BY id ASC
        """,
        (conv_id, limite),
    )

    messages: list[dict[str, Any]] = []
    for row in lignes:
        item = dict(row)
        meta_brut = item.get("meta")
        if isinstance(meta_brut, str):
            try:
                item["meta"] = json.loads(meta_brut)
            except json.JSONDecodeError:
                item["meta"] = {}
        messages.append(item)
    return messages
