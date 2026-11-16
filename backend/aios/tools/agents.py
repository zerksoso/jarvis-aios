"""Outil AIOS permettant à un agent de créer un nouvel agent spécialisé."""

from typing import Any

from backend.agents import factory
from backend.aios.tools_registry import ContexteOutil, outil


@outil(
    nom="agent.creer",
    description="Crée un nouvel agent spécialisé Jarvis à partir d'une description en langage naturel.",
    parametres={
        "description": "str — description du rôle, de la personnalité et des compétences souhaitées",
    },
    risque="ecriture",
)
async def creer_agent_outil(ctx: ContexteOutil, description: str) -> dict[str, Any]:
    """Génère et enregistre un nouvel agent dans ``agents/<id>.json`` via ``factory``."""
    del ctx
    return await factory.creer_agent_depuis_texte(description)
