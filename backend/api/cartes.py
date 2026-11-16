"""Routes API de gestion des cartes d'information du tableau de bord."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import db
from backend.ws_hub import hub

router = APIRouter(prefix="/cartes", tags=["cartes"])


class CarteCreateRequest(BaseModel):
    """Schéma de création d'une carte Markdown."""

    titre: str = Field(min_length=1)
    contenu_md: str
    type: str = "info"
    epinglee: bool = True


class CarteEpinglerRequest(BaseModel):
    """Schéma d'épinglage/désépinglage d'une carte."""

    epinglee: bool


def _formater_carte(row: dict[str, Any]) -> dict[str, Any]:
    """Convertit une ligne SQLite ``cartes`` en dictionnaire JSON normalisé."""
    return {
        "id": str(row["id"]),
        "titre": str(row["titre"]),
        "contenu_md": str(row["contenu_md"] or ""),
        "type": str(row["type"] or "info"),
        "epinglee": bool(row["epinglee"]),
        "cree_le": str(row["cree_le"] or ""),
    }


@router.get("")
async def lister_cartes(epinglees: bool | None = None) -> list[dict[str, Any]]:
    """Liste les cartes persistées, avec filtre optionnel ``?epinglees=1``."""
    if epinglees:
        lignes = await db.async_query(
            "SELECT id, titre, contenu_md, type, epinglee, cree_le FROM cartes "
            "WHERE epinglee = 1 ORDER BY cree_le DESC"
        )
    else:
        lignes = await db.async_query(
            "SELECT id, titre, contenu_md, type, epinglee, cree_le FROM cartes ORDER BY cree_le DESC"
        )
    return [_formater_carte(row) for row in lignes]


@router.post("")
async def creer_carte(body: CarteCreateRequest) -> dict[str, Any]:
    """Crée une carte, l'enregistre en base SQLite et la diffuse sur le hub WebSocket."""
    titre = body.titre.strip()
    if not titre:
        raise HTTPException(status_code=400, detail="Le titre de la carte ne peut pas être vide.")

    carte_id = f"carte-{uuid.uuid4().hex[:10]}"
    cree_le = db.maintenant_iso()
    type_carte = body.type.strip() or "info"
    epinglee_int = 1 if body.epinglee else 0

    await db.async_execute(
        """
        INSERT INTO cartes (id, titre, contenu_md, type, epinglee, cree_le)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (carte_id, titre, body.contenu_md, type_carte, epinglee_int, cree_le),
    )
    await hub.broadcast(
        "carte",
        {
            "id": carte_id,
            "titre": titre,
            "contenu_md": body.contenu_md,
            "type": type_carte,
        },
    )
    return {
        "id": carte_id,
        "titre": titre,
        "contenu_md": body.contenu_md,
        "type": type_carte,
        "epinglee": body.epinglee,
        "cree_le": cree_le,
    }


@router.post("/{carte_id}/epingler")
async def epingler_carte(carte_id: str, body: CarteEpinglerRequest) -> dict[str, Any]:
    """Modifie l'état ``epinglee`` d'une carte existante."""
    modifies = await db.async_execute(
        "UPDATE cartes SET epinglee = ? WHERE id = ?",
        (1 if body.epinglee else 0, carte_id),
    )
    if modifies == 0:
        raise HTTPException(status_code=404, detail="Carte introuvable")
    ligne = await db.async_query_one(
        "SELECT id, titre, contenu_md, type, epinglee, cree_le FROM cartes WHERE id = ?",
        (carte_id,),
    )
    assert ligne is not None
    return _formater_carte(ligne)


@router.delete("/{carte_id}")
async def supprimer_carte(carte_id: str) -> dict[str, Any]:
    """Supprime définitivement une carte du tableau de bord."""
    supprimees = await db.async_execute("DELETE FROM cartes WHERE id = ?", (carte_id,))
    if supprimees == 0:
        raise HTTPException(status_code=404, detail="Carte introuvable")
    return {"ok": True, "id": carte_id}
