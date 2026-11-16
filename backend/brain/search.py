"""Recherche plein texte FTS5 et listage filtré des notes du second cerveau."""

import json
import re
from typing import Any

from backend import db

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _construire_requete_fts(q: str) -> str:
    """Nettoie une requête utilisateur et génère des préfixes FTS5 sûrs (``"mot"*``)."""
    tokens = _TOKEN_RE.findall(q)
    if not tokens:
        return ""
    return " AND ".join(f'"{tok}"*' for tok in tokens)


def rechercher(q: str, limite: int = 20) -> list[dict[str, Any]]:
    """Recherche les notes correspondantes via SQLite FTS5 (accents tolérés, préfixes).

    Args:
        q: Chaîne de recherche utilisateur.
        limite: Nombre maximal de résultats à renvoyer.

    Returns:
        Liste de dictionnaires ``{"id", "titre", "branche", "extrait"}`` avec balises ``<mark>``.
    """
    requete = _construire_requete_fts(q)
    if not requete:
        return []

    lignes = db.query(
        """
        SELECT
            n.id AS id,
            n.titre AS titre,
            n.branche AS branche,
            snippet(notes_fts, -1, '<mark>', '</mark>', '...', 24) AS extrait
        FROM notes_fts
        JOIN notes n ON n.id = notes_fts.id
        WHERE notes_fts MATCH ?
        ORDER BY bm25(notes_fts)
        LIMIT ?
        """,
        (requete, max(1, min(limite, 100))),
    )
    return [
        {
            "id": str(row["id"]),
            "titre": str(row["titre"] or ""),
            "branche": str(row["branche"] or "autres"),
            "extrait": str(row["extrait"] or ""),
        }
        for row in lignes
    ]


def lister_notes(
    branche: str | None = None,
    statut: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les notes du second cerveau triées par date décroissante.

    Args:
        branche: Filtre optionnel sur la branche.
        statut: Filtre optionnel sur le statut (``actif``, ``archive``, ``brouillon``).

    Returns:
        Liste de résumés ``{"id", "titre", "branche", "tags", "statut", "date", "extrait"}``.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if branche and branche.strip():
        clauses.append("n.branche = ?")
        params.append(branche.strip())
    if statut and statut.strip():
        clauses.append("n.statut = ?")
        params.append(statut.strip())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    lignes = db.query(
        f"""
        SELECT
            n.id AS id,
            n.titre AS titre,
            n.branche AS branche,
            n.tags AS tags,
            n.statut AS statut,
            n.date AS date,
            COALESCE(f.contenu, '') AS contenu
        FROM notes n
        LEFT JOIN notes_fts f ON f.id = n.id
        {where_sql}
        ORDER BY n.date DESC, n.id ASC
        """,
        params,
    )

    resultats: list[dict[str, Any]] = []
    for row in lignes:
        try:
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else []
        except json.JSONDecodeError:
            tags = []
        contenu = " ".join(str(row.get("contenu") or "").split())
        extrait = contenu[:200]
        resultats.append(
            {
                "id": str(row["id"]),
                "titre": str(row["titre"] or ""),
                "branche": str(row["branche"] or "autres"),
                "tags": tags if isinstance(tags, list) else [],
                "statut": str(row["statut"] or "actif"),
                "date": str(row["date"] or ""),
                "extrait": extrait,
            }
        )
    return resultats
