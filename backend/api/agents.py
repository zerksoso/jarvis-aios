"""Routes API de gestion des agents spécialisés et branchement du traiteur de chat."""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.agents import agent_runtime, channel_orchestrator, factory
from backend.agents.models import Avatar
from backend.api import chat
from backend.lifecycle import register_startup
from backend.llm.base import Effort
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


async def traiteur_chat(
    conv_id: str,
    cible: str,
    id: str,
    message: str,
    moteur: str,
    modele: str | None,
    effort: Effort,
) -> None:
    """Aiguille les messages de ``POST /api/chat`` vers le runtime agent ou l'orchestrateur de canal.

    Conserve le protocole WebSocket ``chat_debut/delta/fin/erreur`` et ``etat_jarvis``.

    Args:
        conv_id: Identifiant de conversation (``"<cible>:<id>"``).
        cible: Type de cible (``"agent"`` ou ``"canal"``).
        id: Identifiant de l'agent ou du canal.
        message: Contenu du message utilisateur.
        moteur: Préférence moteur LLM.
        modele: Modèle catalogue optionnel.
        effort: Niveau d'effort de raisonnement.
    """
    if cible == "canal":
        try:
            await channel_orchestrator.traiter_message_canal(
                canal_id=id,
                message=message,
                moteur=moteur,
                modele=modele,
                effort=effort,
                _persister_user=False,
            )
        except Exception:
            # L'erreur et etat_jarvis=repos sont déjà diffusés par traiter_message_canal.
            return
        return

    await hub.broadcast("etat_jarvis", {"etat": "reflexion"})
    await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": id})

    try:
        async def _sur_delta(delta: str) -> None:
            await hub.broadcast(
                "chat_delta",
                {"conversation_id": conv_id, "auteur": id, "delta": delta},
            )

        texte = await agent_runtime.repondre(
            agent_id=id,
            conversation_id=conv_id,
            message=message,
            moteur=moteur,
            modele=modele,
            effort=effort,
            on_delta=_sur_delta,
            _persister_user=False,
            _persister_assistant=True,
        )
        detail = agent_runtime.dernier_detail_reponse(conv_id)
        await hub.broadcast(
            "chat_fin",
            {
                "conversation_id": conv_id,
                "auteur": id,
                "message_id": detail.message_id if detail else None,
                "contenu": texte,
                "moteur": detail.moteur if detail else "agy",
                "modele": detail.modele if detail else (modele or "hydrogen"),
                "bascules": detail.bascules if detail else [],
            },
        )
    except Exception as exc:
        logger.exception("Erreur lors du traitement chat agent %s", conv_id)
        payload_err: dict[str, Any] = {
            "conversation_id": conv_id,
            "auteur": id,
            "erreur": str(exc),
        }
        code_err = getattr(exc, "code", None)
        if code_err:
            payload_err["code"] = code_err
        await hub.broadcast("chat_erreur", payload_err)
    finally:
        await hub.broadcast("etat_jarvis", {"etat": "repos"})


@register_startup
async def _initialiser_agents_et_traiteur() -> None:
    """Crée l'équipe initiale d'agents si absente et branche le traiteur de chat."""
    await asyncio.to_thread(agent_runtime.initialiser_equipe_initiale)
    chat.set_traiteur(traiteur_chat)


class CreerAgentDepuisTexteRequest(BaseModel):
    """Corps de requête pour la création d'un agent en langage naturel."""

    description: str = Field(min_length=1)


class AgentCreateRequest(BaseModel):
    """Schéma de création manuelle d'un agent spécialisé."""

    id: str = Field(min_length=1)
    nom: str = Field(min_length=1)
    titre: str = "Spécialiste"
    avatar: Avatar = Field(default_factory=Avatar)
    instructions: str = ""
    modele_principal: str = "hydrogen"
    effort_raisonnement: Effort = "medium"
    modele_secours: str = "claude-sonnet"
    souvenirs: list[str] = Field(default_factory=list)
    skills_assignes: list[str] = Field(default_factory=list)
    acces_second_cerveau: bool = True
    outils: list[str] = Field(default_factory=list)
    fichiers_generes: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)
    cree_le: str | None = None


class AgentUpdateRequest(BaseModel):
    """Schéma de mise à jour partielle ou complète d'un agent existant."""

    nom: str | None = None
    titre: str | None = None
    avatar: dict[str, Any] | None = None
    instructions: str | None = None
    modele_principal: str | None = None
    effort_raisonnement: Effort | None = None
    modele_secours: str | None = None
    souvenirs: list[str] | None = None
    skills_assignes: list[str] | None = None
    acces_second_cerveau: bool | None = None
    outils: list[str] | None = None
    fichiers_generes: list[str] | None = None
    routines: list[str] | None = None


@router.get("")
async def lister_agents() -> list[dict[str, Any]]:
    """Retourne tous les agents avec leur état temps réel ``avatar.etat``."""
    return await asyncio.to_thread(agent_runtime.lister_agents)


@router.post("/creer-depuis-texte")
async def creer_agent_depuis_texte(body: CreerAgentDepuisTexteRequest) -> dict[str, Any]:
    """Génère et enregistre un nouvel agent à partir d'une description en français."""
    try:
        return await factory.creer_agent_depuis_texte(body.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{agent_id}")
async def obtenir_agent(agent_id: str) -> dict[str, Any]:
    """Retourne la fiche complète d'un agent par son identifiant."""
    try:
        agent = await asyncio.to_thread(agent_runtime.charger_agent, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent introuvable")
    return agent.model_dump()


@router.post("")
async def creer_agent(body: AgentCreateRequest) -> dict[str, Any]:
    """Crée un nouvel agent dans ``agents/<id>.json``."""
    donnees = body.model_dump(exclude_none=True)
    try:
        agent = await asyncio.to_thread(agent_runtime.enregistrer_agent, donnees, False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return agent.model_dump()


@router.put("/{agent_id}")
async def modifier_agent(agent_id: str, body: AgentUpdateRequest) -> dict[str, Any]:
    """Met à jour la fiche d'un agent existant."""
    try:
        existant = await asyncio.to_thread(agent_runtime.charger_agent, agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if existant is None:
        raise HTTPException(status_code=404, detail="Agent introuvable")

    fusion = existant.model_dump()
    patch = body.model_dump(exclude_unset=True)
    if "avatar" in patch and isinstance(patch["avatar"], dict):
        fusion["avatar"] = {**fusion.get("avatar", {}), **patch.pop("avatar")}
    fusion.update(patch)
    fusion["id"] = agent_id

    try:
        maj = await asyncio.to_thread(agent_runtime.enregistrer_agent, fusion, True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return maj.model_dump()


@router.delete("/{agent_id}")
async def supprimer_agent(agent_id: str) -> dict[str, Any]:
    """Envoie le fichier d'un agent à la corbeille système (``jarvis`` est protégé)."""
    try:
        supprime = await asyncio.to_thread(agent_runtime.supprimer_agent, agent_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not supprime:
        raise HTTPException(status_code=404, detail="Agent introuvable")
    return {"ok": True, "id": agent_id}
