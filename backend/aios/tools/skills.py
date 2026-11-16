"""Outil de création de skills AIOS depuis un agent."""

import asyncio
from typing import Any

from backend.aios import skills_engine
from backend.aios.tools_registry import ContexteOutil, outil


@outil(
    nom="skill.creer",
    description="Crée un nouveau skill Markdown réutilisable dans aios/skills/<nom>/SKILL.md.",
    parametres={
        "nom": "str — nom du skill (sera transformé en slug sûr)",
        "description": "str — objectif du skill",
        "declencheurs": "list[str] — mots-clés déclencheurs",
        "outils": "list[str] — noms des outils nécessaires",
        "etapes": "list[str] — étapes numérotées du workflow",
    },
    risque="ecriture",
)
async def creer_skill_outil(
    ctx: ContexteOutil,
    nom: str,
    description: str,
    declencheurs: list[str],
    outils: list[str],
    etapes: list[str],
) -> dict[str, Any]:
    """Crée un fichier ``SKILL.md`` via ``skills_engine.creer_skill``."""
    slug = await asyncio.to_thread(
        skills_engine.creer_skill,
        nom,
        description,
        declencheurs,
        outils,
        etapes,
        None,
        False,
        ctx.agent_id or "jarvis",
    )
    return {"nom": slug, "ok": True}
