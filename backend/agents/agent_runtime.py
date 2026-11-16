"""Runtime d'exécution des agents AIOS : cycle pensée/outils/réponse, états temps réel et persistance."""

import asyncio
import hashlib
import json
import logging
import re
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from send2trash import send2trash

from backend import config, db, settings_store
from backend.agents.models import Agent, EtatAgent
from backend.agents.prompts import construire_prompt_systeme
from backend.agents.streaming import ExtracteurReponse
from backend.aios import skills_engine, tools_registry
from backend.aios.tools_registry import ContexteOutil
from backend.brain import memories, search
from backend.brain.memories import extraire_souvenirs
from backend.jarvis_engine import approvals
from backend.llm import router as llm_router
from backend.llm.base import DeltaCallback, Effort, LLMRequest, LLMResult
from backend.llm.jsonparse import extraire_json
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

MAX_TOURS_OUTILS = 8
MAX_CHARS_RESULTATS_PROMPT = 6000
MAX_MESSAGES_RECAP_SESSION = 10
MAX_CHARS_MESSAGE_RECAP = 500
DELAI_RETOUR_LIBRE_S = 3.0

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

EQUIPE_INITIALE: dict[str, dict[str, Any]] = {
    "jarvis": {
        "id": "jarvis",
        "nom": "Jarvis",
        "titre": "Coordinateur",
        "avatar": {
            "forme": "sphere",
            "couleur": "#22d3ee",
            "yeux": "ronds",
            "etat": "libre",
        },
        "instructions": (
            "Tu es Jarvis, le coordinateur central et chef d'orchestre bienveillant de l'utilisateur.\n"
            "Ta mission est de comprendre immédiatement l'intention réelle derrière chaque demande et d'y répondre avec clarté, concision et proactivité.\n"
            "Tu disposes d'une vue d'ensemble sur les projets, les tâches quotidiennes, les souvenirs et le second cerveau Obsidian.\n"
            "Lorsque la question touche à la recherche documentaire, la création éditoriale, la planification ou l'analyse chiffrée, tu intègres mentalement la rigueur des spécialistes de l'équipe (Atlas, Nova, Maya, Rémi).\n"
            "Avant d'affirmer un fait lié aux projets de l'utilisateur, consulte le second cerveau via `cerveau.chercher` ou `cerveau.lire`.\n"
            "Propose systématiquement la prochaine étape concrète ou l'action immédiate qui fait gagner du temps.\n"
            "Lorsque tu identifies une préférence durable ou une règle métier, mémorise-la proprement avec l'outil `memoriser`.\n"
            "Garde un ton professionnel, chaleureux, direct et orienté résultats, sans formules creuses ni détours inutiles.\n"
            "Respecte scrupuleusement les garde-fous de sécurité et demande toujours confirmation avant toute action irréversible."
        ),
        "modele_principal": "hydrogen",
        "effort_raisonnement": "medium",
        "modele_secours": "claude-sonnet",
        "souvenirs": [],
        "skills_assignes": [
            "brief-du-matin",
            "creer-un-skill",
            "creer-un-agent",
            "audit-ecran",
        ],
        "acces_second_cerveau": True,
        "outils": [
            "cerveau.chercher",
            "cerveau.lire",
            "cerveau.ecrire",
            "memoriser",
            "web.chercher",
            "web.lire",
            "taches.lister",
            "taches.ajouter",
            "taches.cocher",
            "carte.afficher",
            "script.executer",
            "skill.creer",
            "agent.creer",
            "ecran.capturer",
            "ecran.fenetre_active",
            "ecran.trouver",
            "pc.action",
            "professeur.guider",
            "fichier.supprimer",
        ],
        "fichiers_generes": [],
        "routines": ["brief-du-matin"],
        "cree_le": "2026-11-16T09:00:00",
    },
    "atlas": {
        "id": "atlas",
        "nom": "Atlas",
        "titre": "Recherche & veille",
        "avatar": {
            "forme": "anneau",
            "couleur": "#a78bfa",
            "yeux": "visiere",
            "etat": "libre",
        },
        "instructions": (
            "Tu es Atlas, l'analyste de recherche documentaire et de veille stratégique de l'équipe Jarvis.\n"
            "Ta priorité absolue est l'exactitude factuelle, la vérification croisée des informations et la traçabilité des sources.\n"
            "Utilise `web.chercher` pour explorer le sujet sous plusieurs angles, puis `web.lire` pour analyser le contenu complet des pages les plus pertinentes.\n"
            "Cite systématiquement tes sources (titre, domaine ou URL, date de publication) dans tes synthèses.\n"
            "Distingue explicitement les faits vérifiés, les chiffres officiels, les estimations de marché et les simples rumeurs.\n"
            "Relie toujours tes trouvailles aux notes existantes du second cerveau (`01-Business-Projets`, `02-Connaissances-Methodes`) à l'aide de liens wiki `[[...]]`.\n"
            "Structure tes livrables avec un résumé exécutif en tête, suivi des points clés et des implications concrètes pour l'utilisateur.\n"
            "Enregistre les veilles importantes dans le second cerveau via `cerveau.ecrire` lorsqu'on te demande un rapport durable.\n"
            "Reste sobre, analytique et synthétique : bannis le remplissage et mets en évidence les signaux faibles utiles."
        ),
        "modele_principal": "hydrogen",
        "effort_raisonnement": "medium",
        "modele_secours": "claude-sonnet",
        "souvenirs": [],
        "skills_assignes": ["veille-web"],
        "acces_second_cerveau": True,
        "outils": [
            "web.chercher",
            "web.lire",
            "cerveau.chercher",
            "cerveau.lire",
            "cerveau.ecrire",
            "carte.afficher",
            "memoriser",
        ],
        "fichiers_generes": [],
        "routines": ["veille-hebdo"],
        "cree_le": "2026-11-16T09:00:00",
    },
    "nova": {
        "id": "nova",
        "nom": "Nova",
        "titre": "Création & visuels",
        "avatar": {
            "forme": "goutte",
            "couleur": "#f472b6",
            "yeux": "points",
            "etat": "libre",
        },
        "instructions": (
            "Tu es Nova, la directrice créative, plume éditoriale et conceptrice visuelle de l'équipe Jarvis.\n"
            "Tu excelles dans la rédaction de textes percutants, le storytelling de marque, l'idéation de campagnes et la direction artistique.\n"
            "Lorsque l'utilisateur cherche des idées ou un angle éditorial, propose 2 à 3 pistes contrastées (sobre/institutionnelle, audacieuse/moderne, narrative/incarnée).\n"
            "Pour chaque brief visuel ou demande d'illustration, détaille la composition, la palette chromatique, la lumière, la typographie et rédige des prompts d'images prêts à l'emploi.\n"
            "Adapte rigoureusement le registre de langue et le rythme des phrases à la cible visée (prospect B2B, présentation exécutive, note interne, landing page).\n"
            "Consulte le second cerveau pour respecter l'identité, le positionnement et le vocabulaire propre aux projets de l'utilisateur.\n"
            "Privilégie les formulations actives, les verbes précis et les accroches mémorables sans jargon artificiel.\n"
            "Présente tes propositions de manière aérée en Markdown afin qu'elles soient immédiatement copiables et exploitables.\n"
            "Explique brièvement le parti pris créatif derrière chaque variante pour éclairer le choix de l'utilisateur."
        ),
        "modele_principal": "hydrogen",
        "effort_raisonnement": "high",
        "modele_secours": "claude-sonnet",
        "souvenirs": [],
        "skills_assignes": [],
        "acces_second_cerveau": True,
        "outils": [
            "cerveau.chercher",
            "cerveau.lire",
            "cerveau.ecrire",
            "carte.afficher",
            "memoriser",
        ],
        "fichiers_generes": [],
        "routines": [],
        "cree_le": "2026-11-16T09:00:00",
    },
    "maya": {
        "id": "maya",
        "nom": "Maya",
        "titre": "Tâches & planning",
        "avatar": {
            "forme": "cube",
            "couleur": "#34d399",
            "yeux": "ronds",
            "etat": "libre",
        },
        "instructions": (
            "Tu es Maya, la responsable de l'organisation opérationnelle, de la gestion des tâches et de la planification.\n"
            "Ta spécialité est de transformer des objectifs flous ou ambitieux en plans d'action séquentiels, réalistes et immédiatement exécutables.\n"
            "Consulte systématiquement les tâches existantes avec `taches.lister` avant d'en ajouter de nouvelles afin d'éviter tout doublon.\n"
            "Priorise chaque action selon son impact réel et son urgence (matrice impact/effort, dépendances critiques, échéances fermes).\n"
            "Protège la charge mentale de l'utilisateur : limite le plan quotidien à un nombre réaliste de priorités majeures (3 à 5 blocs clés).\n"
            "Formule chaque tâche avec un verbe d'action précis et un résultat vérifiable (ex. « Envoyer le devis chiffré Alpha » plutôt que « Client Alpha »).\n"
            "Utilise `taches.ajouter` et `taches.cocher` pour maintenir la liste du jour parfaitement synchronisée avec l'avancement réel.\n"
            "Signale proactivement les goulets d'étranglement, les prérequis manquants ou les risques de glissement de planning.\n"
            "Tes réponses sont structurées sous forme de listes d'actions claires, jalons datés et séquences horaires pragmatiques."
        ),
        "modele_principal": "hydrogen",
        "effort_raisonnement": "low",
        "modele_secours": "claude-sonnet",
        "souvenirs": [],
        "skills_assignes": [],
        "acces_second_cerveau": True,
        "outils": [
            "taches.lister",
            "taches.ajouter",
            "taches.cocher",
            "cerveau.chercher",
            "cerveau.lire",
            "carte.afficher",
            "memoriser",
        ],
        "fichiers_generes": [],
        "routines": [],
        "cree_le": "2026-11-16T09:00:00",
    },
    "remi": {
        "id": "remi",
        "nom": "Rémi",
        "titre": "Analyse & chiffres",
        "avatar": {
            "forme": "diamant",
            "couleur": "#f59e0b",
            "yeux": "fentes",
            "etat": "libre",
        },
        "instructions": (
            "Tu es Rémi, l'analyste quantitatif, financier et stratégique de l'équipe Jarvis.\n"
            "Tu abordes chaque problème avec une rigueur mathématique et méthodologique irréprochable.\n"
            "Explicite systématiquement toutes tes hypothèses de calcul, tes formules, tes unités et tes marges d'incertitude.\n"
            "Présente les comparaisons, budgets, projections de rentabilité et métriques sous forme de tableaux Markdown propres et lisibles.\n"
            "Lorsque des données manquent, construis trois scénarios chiffrés (prudent, central, ambitieux) au lieu de donner une valeur arbitraire unique.\n"
            "Vérifie la cohérence des ordres de grandeur, des totaux et des pourcentages avant de livrer ta conclusion.\n"
            "Croise les métriques avec les notes de la branche `04-Analytics-Resultats` et `01-Business-Projets` du second cerveau.\n"
            "Mets en évidence le point mort, le retour sur investissement attendu (ROI) et les facteurs de sensibilité principaux.\n"
            "Termine chaque analyse par une recommandation chiffrée claire et directement exploitable pour la prise de décision."
        ),
        "modele_principal": "hydrogen",
        "effort_raisonnement": "xhigh",
        "modele_secours": "claude-sonnet",
        "souvenirs": [],
        "skills_assignes": [],
        "acces_second_cerveau": True,
        "outils": [
            "cerveau.chercher",
            "cerveau.lire",
            "cerveau.ecrire",
            "web.chercher",
            "carte.afficher",
            "memoriser",
        ],
        "fichiers_generes": [],
        "routines": [],
        "cree_le": "2026-11-16T09:00:00",
    },
}

_ORDRE_AGENTS_DEFAUT: tuple[str, ...] = ("jarvis", "atlas", "nova", "maya", "remi")

_etats_agents: dict[str, EtatAgent] = {}
_verrous_par_loop: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, asyncio.Lock]
] = weakref.WeakKeyDictionary()
_taches_fond: set[asyncio.Task[Any]] = set()
_compteur_user_par_conv: dict[str, int] = {}


@dataclass
class DetailReponseAgent:
    """Résultat enrichi d'un tour complet d'agent (texte final, message_id et métadonnées LLM)."""

    texte: str
    message_id: int | None
    moteur: str
    modele: str
    bascules: list[str]


_derniers_details: dict[str, DetailReponseAgent] = {}


def reset_etat_pour_tests() -> None:
    """Réinitialise les états agents, compteurs et tâches de fond pour les tests unitaires."""
    for tache in list(_taches_fond):
        if not tache.done():
            tache.cancel()
    _taches_fond.clear()
    _etats_agents.clear()
    _compteur_user_par_conv.clear()
    _derniers_details.clear()


def valider_id_agent(agent_id: str) -> str:
    """Valide qu'un identifiant d'agent est un slug sûr confiné au dossier ``agents/``.

    Args:
        agent_id: Identifiant d'agent brut.

    Returns:
        Slug normalisé en minuscules.

    Raises:
        ValueError: Si l'identifiant est vide ou contient des caractères interdits.
    """
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Identifiant d'agent vide.")
    propre = agent_id.strip().lower()
    if not _SLUG_RE.match(propre):
        raise ValueError(f"Identifiant d'agent invalide : {agent_id!r}")
    return propre


def _chemin_agent(agent_id: str) -> Path:
    """Résout le chemin ``AGENTS_DIR/<id>.json`` avec vérification de confinement."""
    slug = valider_id_agent(agent_id)
    base = config.AGENTS_DIR.resolve()
    cible = (base / f"{slug}.json").resolve()
    if not cible.is_relative_to(base) or cible.parent != base:
        raise ValueError("Chemin d'agent hors de AGENTS_DIR.")
    return cible


def _obtenir_verrou_agent(agent_id: str) -> asyncio.Lock:
    """Retourne le verrou asyncio exclusif de ``agent_id`` pour la boucle courante."""
    loop = asyncio.get_running_loop()
    table = _verrous_par_loop.get(loop)
    if table is None:
        table = {}
        _verrous_par_loop[loop] = table
    verrou = table.get(agent_id)
    if verrou is None:
        verrou = asyncio.Lock()
        table[agent_id] = verrou
    return verrou


def etat_agent(agent_id: str) -> EtatAgent:
    """Retourne l'état temps réel d'un agent tenu en mémoire (``libre`` par défaut).

    Args:
        agent_id: Identifiant de l'agent.

    Returns:
        État courant parmi ``libre``, ``travaille``, ``attend_accord``, ``fini``, ``erreur``.
    """
    return _etats_agents.get(agent_id.strip().lower(), "libre")


async def definir_etat_agent(agent_id: str, etat: EtatAgent) -> None:
    """Met à jour l'état temps réel d'un agent et diffuse ``agent_etat`` sur le hub WebSocket.

    Args:
        agent_id: Identifiant de l'agent.
        etat: Nouvel état à enregistrer et diffuser.
    """
    slug = agent_id.strip().lower()
    _etats_agents[slug] = etat
    await hub.broadcast("agent_etat", {"id": slug, "etat": etat})


async def _retour_libre_differe(agent_id: str, delai_s: float = DELAI_RETOUR_LIBRE_S) -> None:
    """Ramène l'agent de l'état ``fini`` à ``libre`` après ``delai_s`` secondes."""
    try:
        await asyncio.sleep(delai_s)
        if etat_agent(agent_id) == "fini":
            await definir_etat_agent(agent_id, "libre")
    except asyncio.CancelledError:
        return


def initialiser_equipe_initiale() -> list[str]:
    """Crée sur disque les 5 fichiers ``agents/*.json`` de l'équipe initiale s'ils n'existent pas.

    Returns:
        Liste des identifiants d'agents nouvellement écrits sur disque.
    """
    config.AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    crees: list[str] = []
    for agent_id in _ORDRE_AGENTS_DEFAUT:
        chemin = _chemin_agent(agent_id)
        if chemin.exists():
            continue
        modele = Agent.model_validate(EQUIPE_INITIALE[agent_id])
        chemin.write_text(
            json.dumps(modele.model_dump(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        crees.append(agent_id)
    return crees


def charger_agent(agent_id: str) -> Agent | None:
    """Charge un agent depuis ``agents/<id>.json`` (ou repli mémoire pour l'équipe initiale).

    Args:
        agent_id: Identifiant slug de l'agent.

    Returns:
        Instance ``Agent`` enrichie de son état temps réel, ou ``None`` si introuvable.
    """
    slug = valider_id_agent(agent_id)
    chemin = _chemin_agent(slug)
    if chemin.is_file():
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        agent = Agent.model_validate(brut)
        agent.avatar.etat = etat_agent(slug)
        return agent

    # Si aucun fichier n'a encore été initialisé dans AGENTS_DIR, repli sur l'équipe initiale
    fichiers_existants = list(config.AGENTS_DIR.glob("*.json")) if config.AGENTS_DIR.exists() else []
    if not fichiers_existants and slug in EQUIPE_INITIALE:
        agent = Agent.model_validate(EQUIPE_INITIALE[slug])
        agent.avatar.etat = etat_agent(slug)
        return agent

    return None


def lister_agents() -> list[dict[str, Any]]:
    """Liste tous les agents disponibles dans ``agents/*.json`` avec leur état temps réel.

    Returns:
        Liste de dictionnaires d'agents ordonnée (équipe initiale en tête puis ordre alphabétique).
    """
    agents_dir = config.AGENTS_DIR
    if not agents_dir.exists():
        return []

    fichiers = sorted(agents_dir.glob("*.json"))
    if not fichiers:
        initialiser_equipe_initiale()
        fichiers = sorted(agents_dir.glob("*.json"))

    par_id: dict[str, dict[str, Any]] = {}
    for fichier in fichiers:
        if not fichier.is_file():
            continue
        try:
            brut = json.loads(fichier.read_text(encoding="utf-8"))
            agent = Agent.model_validate(brut)
            agent.avatar.etat = etat_agent(agent.id)
            par_id[agent.id] = agent.model_dump()
        except Exception as exc:
            logger.warning("Fichier agent illisible %s : %s", fichier, exc)

    ordonnes: list[dict[str, Any]] = []
    for defaut_id in _ORDRE_AGENTS_DEFAUT:
        if defaut_id in par_id:
            ordonnes.append(par_id.pop(defaut_id))
    for reste_id in sorted(par_id.keys()):
        ordonnes.append(par_id[reste_id])
    return ordonnes


def enregistrer_agent(donnees: dict[str, Any] | Agent, ecraser: bool = True) -> Agent:
    """Valide un agent via Pydantic et l'enregistre dans ``agents/<id>.json``.

    Args:
        donnees: Dictionnaire ou instance ``Agent`` à enregistrer.
        ecraser: Si ``False``, lève ``FileExistsError`` si le fichier existe déjà.

    Returns:
        Instance ``Agent`` validée et persistée.

    Raises:
        ValueError: Si l'identifiant ou les champs sont invalides.
        FileExistsError: Si l'agent existe déjà et ``ecraser`` vaut ``False``.
    """
    brut = donnees.model_dump() if isinstance(donnees, Agent) else dict(donnees)
    slug = valider_id_agent(str(brut.get("id") or ""))
    brut["id"] = slug

    chemin = _chemin_agent(slug)
    if chemin.exists() and not ecraser:
        raise FileExistsError(f"L'agent '{slug}' existe déjà.")

    agent = Agent.model_validate(brut)
    # Persiste toujours l'état repos 'libre' sur disque ; l'état temps réel reste en mémoire.
    a_ecrire = agent.model_dump()
    a_ecrire["avatar"]["etat"] = "libre"

    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(a_ecrire, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    agent.avatar.etat = etat_agent(slug)
    return agent


def supprimer_agent(agent_id: str) -> bool:
    """Envoie le fichier ``agents/<id>.json`` à la corbeille système (``jarvis`` protégé).

    Args:
        agent_id: Identifiant slug de l'agent à supprimer.

    Returns:
        ``True`` si le fichier existait et a été envoyé à la corbeille.

    Raises:
        PermissionError: Si ``agent_id`` est ``"jarvis"``.
        ValueError: Si l'identifiant est invalide.
    """
    slug = valider_id_agent(agent_id)
    if slug == "jarvis":
        raise PermissionError("L'agent système 'jarvis' ne peut pas être supprimé.")

    chemin = _chemin_agent(slug)
    if not chemin.is_file():
        return False
    send2trash(str(chemin))
    _etats_agents.pop(slug, None)
    return True


def empreinte_agent(agent: Agent, nb_souvenirs_globaux: int = 0) -> str:
    """Calcule une empreinte SHA-1 courte (8 hex) de la configuration stable d'un agent.

    Sérialise les champs stables de l'agent (``instructions``, ``titre``, ``nom``,
    ``modele_principal``, ``effort_raisonnement``, ``skills_assignes``, ``outils``,
    ``acces_second_cerveau``, ``souvenirs``) ainsi que ``nb_souvenirs_globaux``.
    Exclut volontairement ``avatar.etat`` et ``fichiers_generes`` pour éviter toute
    invalidation de session lors des transitions d'état temps réel ou des sorties générées.

    Args:
        agent: Modèle Pydantic de l'agent.
        nb_souvenirs_globaux: Nombre de souvenirs globaux persistés dans le second cerveau.

    Returns:
        Les 8 premiers caractères hexadécimaux du condensat SHA-1.
    """
    charge = {
        "instructions": agent.instructions,
        "titre": agent.titre,
        "nom": agent.nom,
        "modele_principal": agent.modele_principal,
        "effort_raisonnement": agent.effort_raisonnement,
        "skills_assignes": list(agent.skills_assignes),
        "outils": list(agent.outils),
        "acces_second_cerveau": bool(agent.acces_second_cerveau),
        "souvenirs": list(agent.souvenirs),
        "nb_souvenirs_globaux": int(nb_souvenirs_globaux),
    }
    brut = json.dumps(charge, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(brut.encode("utf-8")).hexdigest()[:8]


def _preparer_contexte_skills(
    agent: Agent,
    message: str,
    skill_force: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Récupère les résumés des skills assignés et les détails complets des skills déclenchés."""
    tous_skills = skills_engine.lister_skills()
    par_nom = {s["nom"]: s for s in tous_skills}

    resumes = [par_nom[nom] for nom in agent.skills_assignes if nom in par_nom]

    declenches = set(skills_engine.trouver_skills(message))
    if skill_force:
        declenches.add(skill_force)

    actifs: list[dict[str, Any]] = []
    for nom_sk in sorted(declenches):
        # Charge le skill s'il est assigné à l'agent, forcé ou si l'agent est jarvis
        if (
            skill_force == nom_sk
            or nom_sk in agent.skills_assignes
            or agent.id == "jarvis"
            or not agent.skills_assignes
        ):
            try:
                detail = skills_engine.charger_skill(nom_sk)
            except ValueError:
                detail = None
            if detail is not None:
                actifs.append(detail)

    return resumes, actifs


async def _construire_recap_nouvelle_session(
    conversation_id: str,
    session_cle: str,
    message_courant: str,
    exclude_message_id: int | None = None,
) -> str | None:
    """Construit un récapitulatif des derniers échanges si aucune session moteur n'existe.

    Interroge ``sessions_moteur`` via ``backend.db.query_one`` sur la clé ``session_cle``
    (toutes valeurs de ``modele`` confondues). Si aucune ligne n'existe, lit les 10 derniers
    messages antérieurs de ``conversation_id`` en ordre chronologique (hors message courant
    déjà inséré) afin de réinjecter le fil de conversation au tour 0 d'une nouvelle session.

    Args:
        conversation_id: Identifiant de la conversation agent (ex. ``"agent:jarvis"``).
        session_cle: Clé de session complète incluant l'empreinte de configuration.
        message_courant: Message utilisateur en cours de traitement.
        exclude_message_id: Identifiant SQLite du message courant si déjà connu.

    Returns:
        Bloc récapitulatif titré ``Contexte des derniers échanges (nouvelle session) :``,
        ou ``None`` si une session existe déjà ou si aucun échange antérieur n'est trouvé.
    """
    session_existante = await asyncio.to_thread(
        db.query_one,
        "SELECT session_id FROM sessions_moteur WHERE cle = ? LIMIT 1",
        (session_cle,),
    )
    if session_existante is not None:
        return None

    if exclude_message_id is not None:
        lignes = await asyncio.to_thread(
            db.query,
            """
            SELECT id, role, auteur, contenu
            FROM messages
            WHERE conversation_id = ? AND id != ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, exclude_message_id, MAX_MESSAGES_RECAP_SESSION),
        )
    else:
        lignes = await asyncio.to_thread(
            db.query,
            """
            SELECT id, role, auteur, contenu
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, MAX_MESSAGES_RECAP_SESSION + 1),
        )
        # Quand le message courant a été inséré avant l'appel (ex. POST /api/chat), on l'écarte.
        if (
            lignes
            and lignes[0].get("role") == "user"
            and str(lignes[0].get("contenu") or "") == message_courant
        ):
            lignes = lignes[1:]
        else:
            lignes = lignes[:MAX_MESSAGES_RECAP_SESSION]

    if not lignes:
        return None

    entrees: list[str] = []
    for ligne in reversed(lignes):
        auteur = str(ligne.get("auteur") or ligne.get("role") or "inconnu").strip()
        contenu = str(ligne.get("contenu") or "").strip()
        if len(contenu) > MAX_CHARS_MESSAGE_RECAP:
            contenu = contenu[: MAX_CHARS_MESSAGE_RECAP - 1] + "…"
        entrees.append(f"- {auteur} : {contenu}")

    return "Contexte des derniers échanges (nouvelle session) :\n" + "\n".join(entrees)


async def _boucle_agent(
    agent: Agent,
    conversation_id: str | None,
    message: str,
    moteur: str | None = None,
    modele: str | None = None,
    effort: Effort | None = None,
    on_delta: DeltaCallback = None,
    contexte_supplementaire: str | None = None,
    skill_force: str | None = None,
    session_cle_override: str | None = None,
    exclude_message_id: int | None = None,
) -> DetailReponseAgent:
    """Exécute la boucle pensée → outils → résultats → réponse (max 8 tours).

    Construit le prompt système, calcule ``session_cle`` avec l'empreinte de l'agent,
    injecte un récapitulatif des 10 derniers messages au tour 0 si la session est nouvelle
    (conversations ``agent`` uniquement), puis enchaîne les appels LLM et l'exécution des outils.

    Args:
        agent: Modèle Pydantic de l'agent exécuté.
        conversation_id: Identifiant de conversation ou ``None`` pour une tâche autonome.
        message: Message utilisateur ou consigne initiale.
        moteur: Préférence moteur LLM optionnelle.
        modele: Modèle catalogue optionnel.
        effort: Niveau d'effort optionnel.
        on_delta: Callback asynchrone recevant les fragments streamés de ``"reponse"``.
        contexte_supplementaire: Contexte additionnel (ex. historique de canal).
        skill_force: Nom de skill à activer de force pour ce tour.
        session_cle_override: Clé de session imposée (utilisée par les canaux).
        exclude_message_id: Identifiant SQLite du message courant à exclure du récapitulatif.

    Returns:
        Instance ``DetailReponseAgent`` contenant le texte final et les métadonnées LLM.
    """
    params = await asyncio.to_thread(settings_store.lire)
    verbosite = str(params.get("verbosite") or "normal")
    souvenirs_globaux = await asyncio.to_thread(memories.lister_souvenirs)
    skills_resumes, skills_actifs = await asyncio.to_thread(
        _preparer_contexte_skills, agent, message, skill_force
    )
    desc_outils = await asyncio.to_thread(
        tools_registry.description_pour_prompt, list(agent.outils)
    )
    extraits_cerveau: list[dict[str, Any]] = []
    if agent.acces_second_cerveau and message.strip():
        try:
            extraits_cerveau = await asyncio.to_thread(search.rechercher, message, 3)
        except Exception as exc:
            logger.debug("Recherche FTS cerveau ignorée pour %s : %s", agent.id, exc)

    systeme = construire_prompt_systeme(
        agent=agent,
        verbosite=verbosite,
        souvenirs_globaux=souvenirs_globaux,
        skills_resumes=skills_resumes,
        skills_actifs_details=skills_actifs,
        description_outils=desc_outils,
        extraits_cerveau=extraits_cerveau,
        contexte_supplementaire=contexte_supplementaire,
    )

    moteur_req: Literal["auto", "gemini", "claude", "chatgpt"] = (
        moteur  # type: ignore[assignment]
        if moteur in ("auto", "gemini", "claude", "chatgpt")
        else "auto"
    )
    modele_req = modele or agent.modele_principal or "hydrogen"
    effort_req: Effort = (
        effort
        if effort in ("low", "medium", "high", "xhigh")
        else (agent.effort_raisonnement or "medium")
    )
    base_session = session_cle_override if session_cle_override is not None else conversation_id
    empreinte = empreinte_agent(agent, len(souvenirs_globaux))
    session_cle = f"{base_session}#{empreinte}" if base_session else None

    async def _demander_accord_agent(
        resume: str,
        action_dict: dict[str, Any],
        risque: str,
    ) -> bool:
        accord = await approvals.demander_accord(
            source=agent.id,
            resume=resume,
            action=action_dict,
            risque=risque,
        )
        # Dès que la décision est rendue, l'agent reprend son état de travail actif.
        await definir_etat_agent(agent.id, "travaille")
        return accord

    ctx_outil = ContexteOutil(
        agent_id=agent.id,
        conversation_id=conversation_id,
        acces_cerveau=bool(agent.acces_second_cerveau),
        demander_accord=_demander_accord_agent,
    )

    prompt_courant = message
    if (
        session_cle is not None
        and conversation_id is not None
        and not conversation_id.startswith("canal:")
        and contexte_supplementaire is None
    ):
        recap = await _construire_recap_nouvelle_session(
            conversation_id=conversation_id,
            session_cle=session_cle,
            message_courant=message,
            exclude_message_id=exclude_message_id,
        )
        if recap:
            prompt_courant = f"{recap}\n\n{message}"

    reponse_finale = ""
    dernier_res: LLMResult | None = None

    for _tour in range(MAX_TOURS_OUTILS):
        extracteur = ExtracteurReponse()
        deja_streame: list[str] = []

        async def _sur_delta_brut(
            delta_brut: str,
            _ext: ExtracteurReponse = extracteur,
            _emis: list[str] = deja_streame,
        ) -> None:
            morceau = _ext.alimenter(delta_brut)
            if morceau:
                _emis.append(morceau)
                if on_delta is not None:
                    await on_delta(morceau)

        req = LLMRequest(
            prompt=prompt_courant,
            systeme=systeme,
            moteur=moteur_req,
            modele=modele_req,
            modele_secours=agent.modele_secours or None,
            effort=effort_req,
            session_cle=session_cle,
        )
        res = await llm_router.generer(req, on_delta=_sur_delta_brut)
        dernier_res = res

        donnees = extraire_json(res.texte)
        if donnees is None:
            # JSON invalide -> le texte brut constitue la réponse finale et termine la boucle.
            reponse_finale = res.texte
            if on_delta is not None and not deja_streame and reponse_finale:
                await on_delta(reponse_finale)
            break

        actions_brutes = donnees.get("actions")
        actions = (
            [a for a in actions_brutes if isinstance(a, dict) and a.get("outil")]
            if isinstance(actions_brutes, list)
            else []
        )
        reponse_tour = str(donnees.get("reponse") or "")
        termine = bool(donnees.get("termine", not actions))

        if not actions:
            reponse_finale = reponse_tour if reponse_tour else res.texte
            if on_delta is not None and not deja_streame and reponse_finale:
                await on_delta(reponse_finale)
            break

        # Exécution séquentielle des actions demandées par l'agent
        resultats_outils: list[dict[str, Any]] = []
        for action_item in actions:
            nom_outil = str(action_item.get("outil") or "").strip()
            args_outil = (
                action_item.get("args") if isinstance(action_item.get("args"), dict) else {}
            )
            await hub.broadcast(
                "narration",
                {
                    "texte": f"{agent.nom} exécute {nom_outil}",
                    "niveau": "info",
                    "agent": agent.id,
                    "outil": nom_outil,
                },
            )
            res_outil = await tools_registry.executer(nom_outil, args_outil, ctx_outil)
            resultats_outils.append({"outil": nom_outil, **res_outil})

        reponse_finale = reponse_tour

        if termine and reponse_tour and len(actions) == 0:
            break

        serialise = json.dumps(resultats_outils, ensure_ascii=False, default=str)
        if len(serialise) > MAX_CHARS_RESULTATS_PROMPT:
            serialise = serialise[: MAX_CHARS_RESULTATS_PROMPT - 15] + "… [tronqué]"
        prompt_courant = f"Résultats des outils : {serialise}"

    if not reponse_finale and dernier_res is not None:
        donnees_fin = extraire_json(dernier_res.texte)
        if isinstance(donnees_fin, dict):
            reponse_finale = str(
                donnees_fin.get("reponse") or donnees_fin.get("pensee") or dernier_res.texte
            )
        else:
            reponse_finale = dernier_res.texte

    return DetailReponseAgent(
        texte=reponse_finale,
        message_id=None,
        moteur=dernier_res.moteur if dernier_res else "agy",
        modele=dernier_res.modele if dernier_res else modele_req,
        bascules=list(dernier_res.bascules) if dernier_res else [],
    )


async def _declencher_extraction_souvenirs_si_besoin(conversation_id: str) -> None:
    """Lance ``extraire_souvenirs`` en tâche de fond tous les 6 messages utilisateur."""
    params = await asyncio.to_thread(settings_store.lire)
    if not params.get("memoire_auto", True):
        return

    nb_mem = _compteur_user_par_conv.get(conversation_id, 0) + 1
    _compteur_user_par_conv[conversation_id] = nb_mem

    compte_db = await db.async_query_one(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND role = 'user'",
        (conversation_id,),
    )
    nb_db = int((compte_db or {}).get("n") or 0)

    if (nb_db > 0 and nb_db % 6 == 0) or (nb_db == 0 and nb_mem % 6 == 0):
        fn_extraction = extraire_souvenirs
        tache = asyncio.create_task(fn_extraction(conversation_id))
        _taches_fond.add(tache)
        tache.add_done_callback(_taches_fond.discard)


def dernier_detail_reponse(conversation_id: str) -> DetailReponseAgent | None:
    """Retourne les métadonnées du dernier appel ``repondre`` pour ``conversation_id``."""
    return _derniers_details.get(conversation_id)


async def attendre_taches_fond() -> None:
    """Attend la fin des tâches de fond en cours (retour libre, extraction souvenirs)."""
    if not _taches_fond:
        return
    await asyncio.gather(*list(_taches_fond), return_exceptions=True)


async def repondre(
    agent_id: str,
    conversation_id: str | None,
    message: str,
    moteur: str | None = None,
    modele: str | None = None,
    effort: Effort | None = None,
    on_delta: DeltaCallback = None,
    historique_canal: str | None = None,
    *,
    _persister_user: bool = True,
    _persister_assistant: bool = True,
    _session_cle_override: str | None = None,
) -> str:
    """Génère la réponse complète d'un agent avec outils, états temps réel et persistance.

    Un verrou asyncio par agent garantit qu'un même agent ne traite qu'un message à la fois.

    Args:
        agent_id: Identifiant de l'agent sollicité.
        conversation_id: Identifiant de la conversation (ex. ``"agent:jarvis"``).
        message: Message utilisateur ou consigne à traiter.
        moteur: Préférence moteur (``auto``, ``gemini``, ``claude``, ``chatgpt``).
        modele: Modèle catalogue optionnel (défaut : ``agent.modele_principal``).
        effort: Niveau d'effort (défaut : ``agent.effort_raisonnement``).
        on_delta: Callback asynchrone recevant les fragments streamés de ``"reponse"``.
        historique_canal: Contexte additionnel du fil de canal multi-agents.

    Returns:
        Texte final de la réponse de l'agent.
    """
    slug = valider_id_agent(agent_id)
    agent = await asyncio.to_thread(charger_agent, slug)
    if agent is None:
        agent = Agent(id=slug, nom=slug.capitalize(), titre="Spécialiste")

    conv_id = conversation_id or f"agent:{slug}"
    type_conv: Literal["agent", "canal"] = "canal" if conv_id.startswith("canal:") else "agent"
    cible_id = conv_id.split(":", 1)[1] if ":" in conv_id else slug

    msg_user_id: int | None = None
    if conversation_id and _persister_user:
        horodatage = db.maintenant_iso()
        await db.async_execute(
            """
            INSERT OR IGNORE INTO conversations (id, type, cible_id, titre, cree_le)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, type_conv, cible_id, cible_id, horodatage),
        )
        msg_user_id = await db.async_execute(
            """
            INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
            VALUES (?, 'user', 'utilisateur', ?, '{}', ?)
            """,
            (conv_id, message, horodatage),
        )

    verrou = _obtenir_verrou_agent(slug)
    async with verrou:
        await definir_etat_agent(slug, "travaille")
        try:
            detail = await _boucle_agent(
                agent=agent,
                conversation_id=conv_id,
                message=message,
                moteur=moteur,
                modele=modele,
                effort=effort,
                on_delta=on_delta,
                contexte_supplementaire=historique_canal,
                session_cle_override=_session_cle_override,
                exclude_message_id=msg_user_id,
            )

            if conversation_id and _persister_assistant:
                horodatage_fin = db.maintenant_iso()
                await db.async_execute(
                    """
                    INSERT OR IGNORE INTO conversations (id, type, cible_id, titre, cree_le)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (conv_id, type_conv, cible_id, cible_id, horodatage_fin),
                )
                meta_json = json.dumps(
                    {
                        "moteur": detail.moteur,
                        "modele": detail.modele,
                        "bascules": detail.bascules,
                    },
                    ensure_ascii=False,
                )
                msg_id = await db.async_execute(
                    """
                    INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
                    VALUES (?, 'assistant', ?, ?, ?, ?)
                    """,
                    (conv_id, slug, detail.texte, meta_json, horodatage_fin),
                )
                detail.message_id = msg_id

            _derniers_details[conv_id] = detail

            if type_conv == "agent" and historique_canal is None:
                await _declencher_extraction_souvenirs_si_besoin(conv_id)

            await definir_etat_agent(slug, "fini")
            tache_libre = asyncio.create_task(_retour_libre_differe(slug))
            _taches_fond.add(tache_libre)
            tache_libre.add_done_callback(_taches_fond.discard)
            return detail.texte
        except Exception:
            await definir_etat_agent(slug, "erreur")
            raise


async def executer_tache(
    agent_id: str,
    consigne: str,
    skill: str | None = None,
) -> str:
    """Exécute une tâche autonome hors conversation (ex. routine planifiée).

    Args:
        agent_id: Identifiant de l'agent exécutant.
        consigne: Instructions détaillées de la tâche.
        skill: Nom optionnel du skill à activer pour cette tâche.

    Returns:
        Texte Markdown produit par l'agent.
    """
    slug = valider_id_agent(agent_id)
    agent = await asyncio.to_thread(charger_agent, slug)
    if agent is None:
        agent = Agent(id=slug, nom=slug.capitalize(), titre="Spécialiste")

    verrou = _obtenir_verrou_agent(slug)
    async with verrou:
        await definir_etat_agent(slug, "travaille")
        try:
            detail = await _boucle_agent(
                agent=agent,
                conversation_id=None,
                message=consigne,
                skill_force=skill,
                session_cle_override=None,
            )
            await definir_etat_agent(slug, "fini")
            tache_libre = asyncio.create_task(_retour_libre_differe(slug))
            _taches_fond.add(tache_libre)
            tache_libre.add_done_callback(_taches_fond.discard)
            return detail.texte
        except Exception:
            await definir_etat_agent(slug, "erreur")
            raise
