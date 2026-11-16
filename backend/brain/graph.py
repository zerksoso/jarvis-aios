"""Construction du graphe de connaissances du second cerveau."""

import json
import time
from datetime import date, timedelta
from typing import Any

from backend import db
from backend.brain.indexer import resoudre_liens
from backend.brain.parser import BRANCHES_META


def _est_recente(date_str: str, mtime: float, seuil_jours: int = 30) -> bool:
    """Indique si une note a été datée ou modifiée il y a moins de ``seuil_jours`` jours."""
    seuil_date = date.today() - timedelta(days=seuil_jours)
    if date_str:
        try:
            if date.fromisoformat(date_str[:10]) >= seuil_date:
                return True
        except ValueError:
            pass
    return (time.time() - mtime) < (seuil_jours * 86400)


def construire_graphe() -> dict[str, Any]:
    """Construit la représentation complète du graphe pour l'API et la visualisation 3D.

    Calcule le nombre de notes par branche, les nœuds avec poids (``1 + entrants + sortants``)
    et état ``actif``, ainsi que les liens résolus et cassés.

    Returns:
        Dictionnaire ``{"branches", "noeuds", "liens", "liens_casses"}``.
    """
    lignes = db.query(
        "SELECT id, titre, branche, tags, statut, date, mtime, liens FROM notes ORDER BY id ASC"
    )
    liens_resolus, liens_casses = resoudre_liens(lignes)

    entrants: dict[str, int] = {}
    sortants: dict[str, int] = {}
    for lien in liens_resolus:
        src = lien["source"]
        dst = lien["cible"]
        sortants[src] = sortants.get(src, 0) + 1
        entrants[dst] = entrants.get(dst, 0) + 1

    compteur_branches: dict[str, int] = {b["id"]: 0 for b in BRANCHES_META}
    noeuds: list[dict[str, Any]] = []

    for row in lignes:
        note_id = str(row["id"])
        branche = str(row.get("branche") or "autres")
        if branche not in compteur_branches:
            branche = "autres"
        compteur_branches[branche] += 1

        try:
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else []
        except json.JSONDecodeError:
            tags = []

        statut = str(row.get("statut") or "actif")
        date_str = str(row.get("date") or "")
        mtime = float(row.get("mtime") or 0.0)

        actif = statut == "actif" and _est_recente(date_str, mtime)
        poids = 1 + entrants.get(note_id, 0) + sortants.get(note_id, 0)

        noeuds.append(
            {
                "id": note_id,
                "titre": str(row.get("titre") or note_id),
                "branche": branche,
                "tags": tags if isinstance(tags, list) else [],
                "poids": poids,
                "actif": actif,
                "date": date_str,
            }
        )

    branches = [
        {
            "id": meta["id"],
            "libelle": meta["libelle"],
            "couleur": meta["couleur"],
            "nb": compteur_branches.get(meta["id"], 0),
        }
        for meta in BRANCHES_META
    ]

    return {
        "branches": branches,
        "noeuds": noeuds,
        "liens": liens_resolus,
        "liens_casses": liens_casses,
    }
