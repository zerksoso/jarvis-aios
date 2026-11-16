"""Routes API de gestion, déclenchement manuel et historique des routines AIOS."""

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend import db
from backend.aios import scheduler
from backend.brain.paths import chemin_sur
from backend.lifecycle import register_shutdown, register_startup

router = APIRouter(prefix="/routines", tags=["routines"])


@register_startup
async def _demarrer_scheduler() -> None:
    """Démarre le planificateur APScheduler une fois la boucle FastAPI active."""
    scheduler.demarrer()


@register_shutdown
def _arreter_scheduler() -> None:
    """Arrête proprement le planificateur APScheduler à l'extinction du serveur."""
    scheduler.arreter()


class RoutineCreateRequest(BaseModel):
    """Schéma de création d'une nouvelle routine."""

    id: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    cron: str = Field(min_length=5)
    agent: str = "jarvis"
    skill: str | None = None
    sortie: str | None = None
    actif: bool = True
    description: str = ""
    consigne: str = ""


class RoutineUpdateRequest(BaseModel):
    """Schéma de mise à jour d'une routine existante."""

    nom: str | None = None
    cron: str | None = None
    agent: str | None = None
    skill: str | None = None
    sortie: str | None = None
    actif: bool | None = None
    description: str | None = None
    consigne: str | None = None


@router.get("")
async def lister_routines() -> list[dict[str, Any]]:
    """Liste toutes les routines avec ``prochaine_execution`` et ``dernier_run``."""
    return await asyncio.to_thread(scheduler.lister_routines)


@router.get("/{routine_id}")
async def obtenir_routine(routine_id: str) -> dict[str, Any]:
    """Retourne le détail complet d'une routine par son identifiant."""
    try:
        routine = await asyncio.to_thread(scheduler.charger_routine, routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if routine is None:
        raise HTTPException(status_code=404, detail="Routine introuvable")
    return routine


@router.post("")
async def creer_routine(body: RoutineCreateRequest) -> dict[str, Any]:
    """Crée une nouvelle routine ``aios/routines/<id>.md`` et recharge le planificateur."""
    try:
        return scheduler.enregistrer_routine(body.model_dump(), ecraser=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{routine_id}")
async def modifier_routine(routine_id: str, body: RoutineUpdateRequest) -> dict[str, Any]:
    """Met à jour une routine existante et recharge le planificateur."""
    try:
        existante = scheduler.charger_routine(routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if existante is None:
        raise HTTPException(status_code=404, detail="Routine introuvable")

    fusion = dict(existante)
    patch = body.model_dump(exclude_unset=True)
    fusion.update(patch)
    fusion["id"] = routine_id

    try:
        return scheduler.enregistrer_routine(fusion, ecraser=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{routine_id}")
async def supprimer_routine(routine_id: str) -> dict[str, Any]:
    """Envoie le fichier d'une routine à la corbeille système."""
    try:
        supprime = scheduler.supprimer_routine(routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not supprime:
        raise HTTPException(status_code=404, detail="Routine introuvable")
    return {"ok": True, "id": routine_id}


@router.post("/{routine_id}/lancer")
async def lancer_routine_manuellement(routine_id: str) -> dict[str, int]:
    """Déclenche immédiatement une routine et retourne son ``run_id``."""
    try:
        scheduler.valider_id_routine(routine_id)
        run_id = await scheduler.lancer_routine(routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Routine introuvable") from exc
    return {"run_id": run_id}


@router.get("/{routine_id}/historique")
async def historique_routine(
    routine_id: str,
    limite: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[dict[str, Any]]:
    """Retourne les dernières exécutions d'une routine avec le contenu Markdown produit."""
    try:
        slug = scheduler.valider_id_routine(routine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    lignes = await db.async_query(
        """
        SELECT id, routine_id, debut, fin, statut, sortie_path, resume, erreur
        FROM runs_routines
        WHERE routine_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (slug, limite),
    )

    def _enrichir() -> list[dict[str, Any]]:
        resultat: list[dict[str, Any]] = []
        for row in lignes:
            item = dict(row)
            contenu_md: str | None = None
            sortie_path = item.get("sortie_path")
            if isinstance(sortie_path, str) and sortie_path.strip():
                try:
                    chemin = chemin_sur(sortie_path)
                    if chemin.is_file():
                        contenu_md = chemin.read_text(encoding="utf-8", errors="replace")
                except ValueError:
                    contenu_md = None
            item["contenu_md"] = contenu_md
            resultat.append(item)
        return resultat

    return await asyncio.to_thread(_enrichir)
