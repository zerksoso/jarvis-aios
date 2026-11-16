"""Outils de lecture, ajout et validation des tâches quotidiennes."""

from datetime import date
from typing import Any

from backend import db
from backend.aios.tools_registry import ContexteOutil, outil
from backend.ws_hub import hub


@outil(
    nom="taches.lister",
    description="Liste les tâches du jour (ou d'une date AAAA-MM-JJ donnée).",
    parametres={"date": "str — date AAAA-MM-JJ (optionnel, défaut aujourd'hui)"},
    risque="lecture",
)
async def lister_taches(ctx: ContexteOutil, date_cible: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    """Retourne les tâches enregistrées pour la date demandée."""
    del ctx
    jour = str(kwargs.get("date") or date_cible or date.today().isoformat())[:10]
    lignes = await db.async_query(
        "SELECT id, titre, fait, date, source, ordre FROM taches WHERE date = ? ORDER BY ordre ASC, id ASC",
        (jour,),
    )
    return [
        {
            "id": int(row["id"]),
            "titre": str(row["titre"]),
            "fait": bool(row["fait"]),
            "date": str(row["date"]),
            "source": str(row["source"] or "manuel"),
            "ordre": int(row["ordre"] or 0),
        }
        for row in lignes
    ]


@outil(
    nom="taches.ajouter",
    description="Ajoute une nouvelle tâche à la liste du jour.",
    parametres={"titre": "str — intitulé de la tâche", "date": "str — date optionnelle AAAA-MM-JJ"},
    risque="ecriture",
)
async def ajouter_tache(
    ctx: ContexteOutil,
    titre: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Insère une tâche dans SQLite et diffuse ``taches_maj``."""
    propre = titre.strip()
    if not propre:
        raise ValueError("Le titre de la tâche ne peut pas être vide.")
    jour = str(kwargs.get("date") or date.today().isoformat())[:10]
    tache_id = await db.async_execute(
        "INSERT INTO taches (titre, fait, date, source, ordre) VALUES (?, 0, ?, ?, 0)",
        (propre, jour, ctx.agent_id),
    )
    await hub.broadcast("taches_maj", {})
    return {"id": tache_id, "titre": propre, "fait": False, "date": jour, "source": ctx.agent_id}


@outil(
    nom="taches.cocher",
    description="Coche ou décoche une tâche existante par son identifiant.",
    parametres={"id": "int — identifiant de la tâche", "fait": "bool — true par défaut"},
    risque="ecriture",
)
async def cocher_tache(ctx: ContexteOutil, id: int, fait: bool = True) -> dict[str, Any]:
    """Met à jour l'état ``fait`` d'une tâche et diffuse ``taches_maj``."""
    del ctx
    modifies = await db.async_execute(
        "UPDATE taches SET fait = ? WHERE id = ?",
        (1 if fait else 0, int(id)),
    )
    if modifies == 0:
        raise ValueError(f"Tâche introuvable : {id}")
    await hub.broadcast("taches_maj", {})
    return {"id": int(id), "fait": bool(fait)}
