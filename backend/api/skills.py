"""Routes API de consultation, création et mise à jour des skills AIOS."""

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.aios import skills_engine, tools_registry
from backend.lifecycle import register_startup

router = APIRouter(prefix="/skills", tags=["skills"])


@register_startup
def _charger_registre_outils() -> None:
    """Charge l'ensemble des outils AIOS au démarrage du serveur."""
    tools_registry.charger_outils()


class SkillPayload(BaseModel):
    """Schéma de création ou mise à jour d'un skill Markdown."""

    nom: str | None = None
    description: str = ""
    declencheurs: list[str] = Field(default_factory=list)
    outils: list[str] = Field(default_factory=list)
    etapes: list[str] = Field(default_factory=list)
    corps_md: str | None = None
    agent_par_defaut: str = "jarvis"


@router.get("")
async def lister_skills() -> list[dict[str, Any]]:
    """Liste l'ensemble des skills enregistrés dans ``aios/skills/``."""
    return await asyncio.to_thread(skills_engine.lister_skills)


@router.get("/{nom}")
async def obtenir_skill(nom: str) -> dict[str, Any]:
    """Retourne le détail complet d'un skill avec ``contenu_md`` et ``etapes``."""
    try:
        skill = await asyncio.to_thread(skills_engine.charger_skill, nom)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill introuvable")
    return skill


@router.post("")
async def creer_skill(body: SkillPayload) -> dict[str, Any]:
    """Crée un nouveau skill ``aios/skills/<slug>/SKILL.md``."""
    if not body.nom or not body.nom.strip():
        raise HTTPException(status_code=400, detail="Le champ 'nom' est requis.")
    try:
        slug = await asyncio.to_thread(
            skills_engine.creer_skill,
            body.nom,
            body.description,
            body.declencheurs,
            body.outils,
            body.etapes,
            body.corps_md,
            False,
            body.agent_par_defaut,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detail = await asyncio.to_thread(skills_engine.charger_skill, slug)
    return detail or {"nom": slug}


@router.put("/{nom}")
async def modifier_skill(nom: str, body: SkillPayload) -> dict[str, Any]:
    """Crée ou remplace un skill existant sous ``aios/skills/<nom>/SKILL.md``."""
    try:
        slug = await asyncio.to_thread(
            skills_engine.creer_skill,
            nom,
            body.description,
            body.declencheurs,
            body.outils,
            body.etapes,
            body.corps_md,
            True,
            body.agent_par_defaut,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    detail = await asyncio.to_thread(skills_engine.charger_skill, slug)
    return detail or {"nom": slug}
