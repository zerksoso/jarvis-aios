"""Routes API de gestion des tâches quotidiennes."""

import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.ws_hub import hub

router = APIRouter(prefix="/taches", tags=["taches"])


class TacheCreateRequest(BaseModel):
    """Schéma de création d'une tâche."""

    titre: str = Field(min_length=1)
    date: str | None = None
    source: str | None = None
    ordre: int = 0


class TachePatchRequest(BaseModel):
    """Schéma de mise à jour partielle d'une tâche."""

    fait: bool | None = None
    titre: str | None = None
    ordre: int | None = None


def _formater_tache(row: dict[str, Any]) -> dict[str, Any]:
    """Convertit une ligne SQLite ``taches`` en dictionnaire JSON normalisé."""
    return {
        "id": int(row["id"]),
        "titre": str(row["titre"]),
        "fait": bool(row["fait"]),
        "date": str(row["date"] or ""),
        "source": str(row["source"] or "manuel"),
        "ordre": int(row["ordre"] or 0),
    }


@router.get("")
async def lister_taches(date: str | None = None) -> list[dict[str, Any]]:
    """Retourne les tâches d'une date ``AAAA-MM-JJ`` (aujourd'hui par défaut)."""
    jour = (date or datetime.date.today().isoformat()).strip()[:10]
    lignes = await db.async_query(
        "SELECT id, titre, fait, date, source, ordre FROM taches WHERE date = ? ORDER BY ordre ASC, id ASC",
        (jour,),
    )
    return [_formater_tache(row) for row in lignes]


@router.post("")
async def creer_tache(body: TacheCreateRequest) -> dict[str, Any]:
    """Ajoute une tâche pour la date indiquée (aujourd'hui par défaut) et diffuse ``taches_maj``."""
    titre = body.titre.strip()
    if not titre:
        raise HTTPException(status_code=400, detail="Le titre de la tâche ne peut pas être vide.")

    jour = (body.date or datetime.date.today().isoformat()).strip()[:10]
    source = (body.source or "manuel").strip() or "manuel"
    tache_id = await db.async_execute(
        "INSERT INTO taches (titre, fait, date, source, ordre) VALUES (?, 0, ?, ?, ?)",
        (titre, jour, source, int(body.ordre)),
    )
    ligne = await db.async_query_one(
        "SELECT id, titre, fait, date, source, ordre FROM taches WHERE id = ?",
        (tache_id,),
    )
    await hub.broadcast("taches_maj", {})
    assert ligne is not None
    return _formater_tache(ligne)


@router.patch("/{tache_id}")
async def modifier_tache(tache_id: int, body: TachePatchRequest) -> dict[str, Any]:
    """Met à jour l'état, le titre ou l'ordre d'une tâche existante et diffuse ``taches_maj``."""
    existante = await db.async_query_one(
        "SELECT id, titre, fait, date, source, ordre FROM taches WHERE id = ?",
        (tache_id,),
    )
    if existante is None:
        raise HTTPException(status_code=404, detail="Tâche introuvable")

    nouveau_titre = body.titre.strip() if body.titre is not None else str(existante["titre"])
    if not nouveau_titre:
        raise HTTPException(status_code=400, detail="Le titre de la tâche ne peut pas être vide.")
    nouveau_fait = (1 if body.fait else 0) if body.fait is not None else int(existante["fait"])
    nouvel_ordre = int(body.ordre) if body.ordre is not None else int(existante["ordre"] or 0)

    await db.async_execute(
        "UPDATE taches SET titre = ?, fait = ?, ordre = ? WHERE id = ?",
        (nouveau_titre, nouveau_fait, nouvel_ordre, tache_id),
    )
    await hub.broadcast("taches_maj", {})
    ligne = await db.async_query_one(
        "SELECT id, titre, fait, date, source, ordre FROM taches WHERE id = ?",
        (tache_id,),
    )
    assert ligne is not None
    return _formater_tache(ligne)


@router.delete("/{tache_id}")
async def supprimer_tache(tache_id: int) -> dict[str, Any]:
    """Supprime une tâche par son identifiant et diffuse ``taches_maj``."""
    supprimees = await db.async_execute("DELETE FROM taches WHERE id = ?", (tache_id,))
    if supprimees == 0:
        raise HTTPException(status_code=404, detail="Tâche introuvable")
    await hub.broadcast("taches_maj", {})
    return {"ok": True, "id": tache_id}
