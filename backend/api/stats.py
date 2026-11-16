"""Route API de statistiques agrégées pour le tableau de bord Jarvis AIOS."""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter

from backend import db
from backend.aios import scheduler
from backend.brain import graph, memories

router = APIRouter(prefix="/stats", tags=["stats"])


def _calculer_stats() -> dict[str, Any]:
    """Agrège les métriques du cerveau, des routines, tâches, messages et usage LLM."""
    maintenant = datetime.now()
    il_y_a_24h = (maintenant - timedelta(hours=24)).isoformat(timespec="seconds")
    il_y_a_7j = (maintenant - timedelta(days=7)).isoformat(timespec="seconds")
    aujourd_hui = date.today().isoformat()

    graphe = graph.construire_graphe()
    branches = {b["id"]: int(b["nb"]) for b in graphe["branches"]}
    nb_notes = len(graphe["noeuds"])
    nb_liens = len(graphe["liens"])

    projets_row = db.query_one(
        "SELECT COUNT(*) AS n FROM notes WHERE branche = '01-Business-Projets' AND statut = 'actif'"
    )
    projets_actifs = int((projets_row or {}).get("n") or 0)

    routines = scheduler.lister_routines()
    routines_actives = sum(1 for r in routines if r.get("actif"))

    runs_rows = db.query(
        """
        SELECT statut, COUNT(*) AS n
        FROM runs_routines
        WHERE debut >= ?
        GROUP BY statut
        """,
        (il_y_a_7j,),
    )
    runs_7j = {"ok": 0, "erreur": 0}
    for row in runs_rows:
        st = str(row.get("statut") or "")
        if st in runs_7j:
            runs_7j[st] = int(row.get("n") or 0)

    taches_row = db.query_one(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN fait = 1 THEN 1 ELSE 0 END), 0) AS faites
        FROM taches
        WHERE date = ?
        """,
        (aujourd_hui,),
    )
    taches_jour = {
        "faites": int((taches_row or {}).get("faites") or 0),
        "total": int((taches_row or {}).get("total") or 0),
    }

    msg_row = db.query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE cree_le >= ?",
        (il_y_a_24h,),
    )
    messages_24h = int((msg_row or {}).get("n") or 0)

    usage_rows = db.query(
        """
        SELECT
            modele,
            COUNT(*) AS appels,
            COALESCE(SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)), 0) AS tokens
        FROM usage_llm
        WHERE ts >= ?
        GROUP BY modele
        """,
        (il_y_a_24h,),
    )
    total_appels = 0
    total_tokens = 0
    par_modele: dict[str, int] = {}
    for row in usage_rows:
        appels = int(row.get("appels") or 0)
        tokens = int(row.get("tokens") or 0)
        modele = str(row.get("modele") or "inconnu")
        total_appels += appels
        total_tokens += tokens
        par_modele[modele] = appels

    return {
        "notes": nb_notes,
        "liens": nb_liens,
        "branches": branches,
        "routines_actives": routines_actives,
        "runs_7j": runs_7j,
        "taches_jour": taches_jour,
        "messages_24h": messages_24h,
        "usage_llm_24h": {
            "appels": total_appels,
            "tokens": total_tokens,
            "par_modele": par_modele,
        },
        "projets_actifs": projets_actifs,
        "souvenirs": len(memories.lister_souvenirs()),
    }


@router.get("")
async def obtenir_stats() -> dict[str, Any]:
    """Retourne les statistiques globales temps réel du système Jarvis AIOS."""
    return await asyncio.to_thread(_calculer_stats)
