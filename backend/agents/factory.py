"""Fabrique d'agents AIOS en langage naturel via Gemini 4 Hydrogen (effort high)."""

import asyncio
import re
from typing import Any

from backend import config, db
from backend.agents import agent_runtime
from backend.agents.models import FORMES_VALIDES, YEUX_VALIDES, Effort
from backend.aios import skills_engine, tools_registry
from backend.brain.parser import normaliser_texte
from backend.llm import router as llm_router
from backend.llm.base import LLMRequest
from backend.llm.catalog import CATALOGUE
from backend.llm.jsonparse import extraire_json

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EFFORTS_VALIDES: tuple[Effort, ...] = ("low", "medium", "high", "xhigh")


def _slugifier_id_agent(brut: str) -> str:
    """Transforme une chaîne en slug d'identifiant d'agent valide."""
    norm = normaliser_texte(brut or "")
    slug = _NON_ALNUM_RE.sub("-", norm).strip("-")
    return slug or "agent"


def _generer_id_unique(base_slug: str) -> str:
    """Garantit un identifiant unique parmi les fichiers ``agents/*.json`` existants."""
    racine = _slugifier_id_agent(base_slug)
    agents_dir = config.AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)

    candidat = racine
    compteur = 2
    while (agents_dir / f"{candidat}.json").exists():
        candidat = f"{racine}-{compteur}"
        compteur += 1
    return candidat


def _normaliser_avatar(avatar_brut: Any) -> dict[str, str]:
    """Assure la cohérence des champs ``forme``, ``couleur``, ``yeux`` et ``etat`` de l'avatar."""
    src = avatar_brut if isinstance(avatar_brut, dict) else {}
    forme = str(src.get("forme") or "sphere").strip().lower()
    if forme not in FORMES_VALIDES:
        forme = "sphere"

    yeux = str(src.get("yeux") or "ronds").strip().lower()
    if yeux not in YEUX_VALIDES:
        yeux = "ronds"

    couleur = str(src.get("couleur") or "#22d3ee").strip()
    if not _HEX_COLOR_RE.match(couleur):
        couleur = "#22d3ee"

    return {
        "forme": forme,
        "couleur": couleur,
        "yeux": yeux,
        "etat": "libre",
    }


async def creer_agent_depuis_texte(description: str) -> dict[str, Any]:
    """Conçoit, valide et enregistre un nouvel agent à partir d'une description en langage naturel.

    Interroge le modèle ``hydrogen`` en effort ``high``, garantit un identifiant slug unique,
    un avatar cohérent et filtre strictement les skills et outils existants.

    Args:
        description: Description en français du rôle, de la personnalité et des besoins de l'agent.

    Returns:
        Dictionnaire complet de l'agent créé et persisté dans ``agents/<id>.json``.

    Raises:
        ValueError: Si la description est vide.
    """
    desc_propre = (description or "").strip()
    if not desc_propre:
        raise ValueError("La description de l'agent ne peut pas être vide.")

    registre_outils = await asyncio.to_thread(tools_registry.charger_outils)
    outils_existants = set(registre_outils.keys())
    skills_liste = await asyncio.to_thread(skills_engine.lister_skills)
    skills_existants = {s["nom"] for s in skills_liste}

    systeme = (
        "Tu es l'architecte d'agents de Jarvis AIOS. "
        "À partir de la description de l'utilisateur, conçois un agent spécialisé complet en français.\n"
        "Réponds UNIQUEMENT par un objet JSON valide suivant exactement ce schéma :\n"
        "{\n"
        '  "id": "slug-court",\n'
        '  "nom": "Prénom",\n'
        '  "titre": "Rôle concis",\n'
        '  "avatar": {"forme": "sphere|cube|diamant|anneau|goutte", "couleur": "#RRGGBB", "yeux": "ronds|fentes|points|visiere"},\n'
        '  "instructions": "Instructions riches en français (8 à 15 lignes détaillant mission, méthode et ton)",\n'
        '  "modele_principal": "hydrogen",\n'
        '  "effort_raisonnement": "low|medium|high|xhigh",\n'
        '  "modele_secours": "claude-sonnet",\n'
        '  "skills_assignes": [],\n'
        '  "acces_second_cerveau": true,\n'
        '  "outils": []\n'
        "}\n"
        f"Outils existants autorisés : {sorted(outils_existants)}\n"
        f"Skills existants autorisés : {sorted(skills_existants)}"
    )

    res = await llm_router.generer(
        LLMRequest(
            prompt=f"Crée l'agent correspondant à cette demande : {desc_propre}",
            systeme=systeme,
            modele="hydrogen",
            effort="high",
            session_cle=None,
        )
    )

    donnees = extraire_json(res.texte)
    if not isinstance(donnees, dict):
        donnees = {}

    nom = str(donnees.get("nom") or "Agent").strip() or "Agent"
    titre = str(donnees.get("titre") or "Spécialiste").strip() or "Spécialiste"
    base_slug = str(donnees.get("id") or nom).strip()
    agent_id = await asyncio.to_thread(_generer_id_unique, base_slug)

    instructions = str(donnees.get("instructions") or "").strip()
    if not instructions:
        instructions = (
            f"Tu es {nom}, {titre} au sein de l'équipe Jarvis AIOS.\n"
            f"Mission confiée : {desc_propre}\n"
            "Réponds toujours en français de manière structurée, rigoureuse et actionnable."
        )

    modele_principal = str(donnees.get("modele_principal") or "hydrogen").strip()
    if modele_principal not in CATALOGUE:
        modele_principal = "hydrogen"

    modele_secours = str(donnees.get("modele_secours") or "claude-sonnet").strip()
    if modele_secours not in CATALOGUE:
        modele_secours = "claude-sonnet"

    effort_brut = str(donnees.get("effort_raisonnement") or "medium").strip().lower()
    effort: Effort = effort_brut if effort_brut in _EFFORTS_VALIDES else "medium"  # type: ignore[assignment]

    skills_bruts = donnees.get("skills_assignes")
    skills_valides = (
        [str(s).strip() for s in skills_bruts if str(s).strip() in skills_existants]
        if isinstance(skills_bruts, list)
        else []
    )

    outils_bruts = donnees.get("outils")
    outils_valides = (
        [str(o).strip() for o in outils_bruts if str(o).strip() in outils_existants]
        if isinstance(outils_bruts, list)
        else []
    )

    acces_cerveau = bool(donnees.get("acces_second_cerveau", True))

    fiche: dict[str, Any] = {
        "id": agent_id,
        "nom": nom,
        "titre": titre,
        "avatar": _normaliser_avatar(donnees.get("avatar")),
        "instructions": instructions,
        "modele_principal": modele_principal,
        "effort_raisonnement": effort,
        "modele_secours": modele_secours,
        "souvenirs": [],
        "skills_assignes": skills_valides,
        "acces_second_cerveau": acces_cerveau,
        "outils": outils_valides,
        "fichiers_generes": [],
        "routines": [],
        "cree_le": db.maintenant_iso(),
    }

    agent = await asyncio.to_thread(agent_runtime.enregistrer_agent, fiche, False)
    return agent.model_dump()
