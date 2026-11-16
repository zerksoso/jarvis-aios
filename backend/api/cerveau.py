"""Routes API du second cerveau : graphe, recherche, lecture/écriture et ouverture Obsidian."""

import asyncio
import logging
import os
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend import db
from backend.brain import graph, indexer, search, watcher
from backend.brain.parser import lire_note
from backend.brain.paths import chemin_sur, id_depuis_chemin
from backend.lifecycle import register_shutdown, register_startup
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cerveau", tags=["cerveau"])


@register_startup
async def _demarrer_cerveau() -> None:
    """Indexe l'ensemble du coffre puis démarre la surveillance Watchdog."""
    await asyncio.to_thread(indexer.reindexer_tout)
    watcher.demarrer()


@register_shutdown
def _arreter_cerveau() -> None:
    """Arrête proprement la surveillance Watchdog du second cerveau."""
    watcher.arreter()


class NoteUpdateRequest(BaseModel):
    """Corps de requête pour la création ou mise à jour d'une note Markdown."""

    id: str = Field(min_length=1)
    contenu_md: str


class OuvrirNoteRequest(BaseModel):
    """Corps de requête pour ouvrir une note dans Obsidian ou l'application par défaut."""

    id: str = Field(min_length=1)


@router.get("/graphe")
async def obtenir_graphe() -> dict[str, Any]:
    """Retourne les branches, nœuds, liens résolus et liens cassés du second cerveau."""
    return await asyncio.to_thread(graph.construire_graphe)


@router.get("/recherche")
async def rechercher_notes(
    q: str = "",
    limite: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[dict[str, Any]]:
    """Recherche plein texte FTS5 avec extraits surlignés par ``<mark>``."""
    return await asyncio.to_thread(search.rechercher, q, limite)


@router.get("/notes")
async def lister_notes(
    branche: str | None = None,
    statut: str | None = None,
) -> list[dict[str, Any]]:
    """Liste les notes filtrées par branche/statut et triées par date décroissante."""
    return await asyncio.to_thread(search.lister_notes, branche, statut)


def _lire_note_detail(note_id: str) -> dict[str, Any]:
    """Lit une note sur disque et calcule ses liens sortants et entrants résolus."""
    chemin = chemin_sur(note_id)
    if not chemin.is_file():
        raise FileNotFoundError(note_id)

    note = lire_note(chemin)
    canonique_id = note["id"]

    lignes = db.query("SELECT id, titre, liens FROM notes")
    resolus, _ = indexer.resoudre_liens(lignes)
    sortants = [lien["cible"] for lien in resolus if lien["source"] == canonique_id]
    entrants = [lien["source"] for lien in resolus if lien["cible"] == canonique_id]

    return {
        "id": canonique_id,
        "titre": note["titre"],
        "branche": note["branche"],
        "tags": note["tags"],
        "statut": note["statut"],
        "date": note["date"],
        "contenu_md": note["contenu_md"],
        "liens_sortants": sortants,
        "liens_entrants": entrants,
        "chemin_absolu": str(chemin),
    }


@router.get("/note")
async def obtenir_note(id: str | None = None) -> dict[str, Any]:
    """Retourne le contenu complet et les liens d'une note (jeton ``X-Jarvis-Token`` requis)."""
    if id is None or not id.strip():
        raise HTTPException(status_code=404, detail="Note introuvable")
    try:
        return await asyncio.to_thread(_lire_note_detail, id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Note introuvable") from exc


@router.put("/note")
async def ecrire_note(body: NoteUpdateRequest) -> dict[str, Any]:
    """Crée ou remplace une note Markdown sous ``BRAIN_DIR`` puis la réindexe."""
    try:
        chemin = chemin_sur(body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _sauver() -> str:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(body.contenu_md, encoding="utf-8")
        indexer.indexer_fichier(chemin)
        return id_depuis_chemin(chemin)

    note_id = await asyncio.to_thread(_sauver)
    await hub.broadcast("cerveau_maj", {})
    return {"ok": True, "id": note_id}


@router.post("/ouvrir")
async def ouvrir_note(body: OuvrirNoteRequest) -> dict[str, Any]:
    """Ouvre une note via le protocole ``obsidian://`` avec repli sur le fichier local."""
    try:
        chemin = chemin_sur(body.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not chemin.is_file():
        raise HTTPException(status_code=404, detail="Note introuvable")

    note_id = id_depuis_chemin(chemin)
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        uri = f"obsidian://open?vault=second-brain&file={quote(note_id)}"
        try:
            startfile(uri)
        except OSError:
            startfile(str(chemin))
    return {"ok": True, "id": note_id}
