"""Orchestrateur de canaux multi-agents : routage coordinateur, tours @mentions, synthèse et mémoire."""

import asyncio
import json
import logging
import re
from collections import deque
from pathlib import Path
from typing import Any, Literal

from send2trash import send2trash

from backend import config, db
from backend.agents import agent_runtime
from backend.agents.models import Canal
from backend.agents.prompts import REGLES_ABSOLUES
from backend.agents.streaming import ExtracteurReponse
from backend.brain.parser import normaliser_texte
from backend.llm import router as llm_router
from backend.llm.base import Effort, LLMRequest
from backend.llm.jsonparse import extraire_json
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_MENTION_RE = re.compile(r"(?<!\w)@([a-zA-Z0-9_\u00C0-\u017F-]+)")
MAX_PUCES_MEMOIRE_PAR_ECHANGE = 5
LIMITE_HISTORIQUE_CANAL = 20

CANAUX_INITIAUX: dict[str, dict[str, Any]] = {
    "general": {
        "id": "general",
        "nom": "general",
        "description": "Canal principal réunissant toute l'équipe d'agents coordonnée par Jarvis.",
        "membres": ["jarvis", "atlas", "nova", "maya", "remi"],
        "coordinateur": "jarvis",
        "max_tours_par_agent": 2,
        "routines": ["brief-du-matin"],
        "cree_le": "2026-11-16T09:00:00",
    },
    "veille": {
        "id": "veille",
        "nom": "veille",
        "description": "Cellule de veille stratégique, analyse chiffrée et synthèse éditoriale.",
        "membres": ["atlas", "remi", "nova"],
        "coordinateur": "atlas",
        "max_tours_par_agent": 2,
        "routines": ["veille-hebdo"],
        "cree_le": "2026-11-16T09:00:00",
    },
}

MEMOIRES_CANAUX_INITIALES: dict[str, str] = {
    "general": (
        "# Mémoire du canal #general\n\n"
        "- Canal transversal de coordination entre Jarvis, Atlas, Nova, Maya et Rémi.\n"
        "- Privilégier des synthèses actionnables reliées aux projets actifs du second cerveau.\n"
    ),
    "veille": (
        "# Mémoire du canal #veille\n\n"
        "- Croiser les sources web (Atlas), l'impact chiffré (Rémi) et l'angle éditorial (Nova).\n"
    ),
}


def valider_id_canal(canal_id: str) -> str:
    """Valide qu'un identifiant de canal est un slug sûr confiné sous ``canaux/``.

    Args:
        canal_id: Identifiant de canal brut.

    Returns:
        Slug normalisé en minuscules.

    Raises:
        ValueError: Si l'identifiant est vide ou contient des séquences interdites.
    """
    if not isinstance(canal_id, str) or not canal_id.strip():
        raise ValueError("Identifiant de canal vide.")
    propre = canal_id.strip().lower()
    if not _SLUG_RE.match(propre):
        raise ValueError(f"Identifiant de canal invalide : {canal_id!r}")
    return propre


def _dossier_canal(canal_id: str) -> Path:
    """Résout le dossier ``CANAUX_DIR/<id>`` avec vérification de confinement."""
    slug = valider_id_canal(canal_id)
    base = config.CANAUX_DIR.resolve()
    cible = (base / slug).resolve()
    if not cible.is_relative_to(base) or cible.parent != base:
        raise ValueError("Chemin de canal hors de CANAUX_DIR.")
    return cible


def _chemin_canal_json(canal_id: str) -> Path:
    """Retourne le chemin vers ``canaux/<id>/canal.json``."""
    return _dossier_canal(canal_id) / "canal.json"


def _chemin_memoire_canal(canal_id: str) -> Path:
    """Retourne le chemin vers ``canaux/<id>/memoire.md``."""
    return _dossier_canal(canal_id) / "memoire.md"


def lire_memoire_canal(canal_id: str) -> str:
    """Lit le contenu Markdown de ``canaux/<id>/memoire.md``.

    Args:
        canal_id: Identifiant du canal.

    Returns:
        Contenu du fichier ``memoire.md`` ou chaîne vide s'il est absent.
    """
    chemin = _chemin_memoire_canal(canal_id)
    if not chemin.is_file():
        return ""
    return chemin.read_text(encoding="utf-8", errors="replace")


def ajouter_puces_memoire_canal(canal_id: str, puces: list[str]) -> str:
    """Ajoute jusqu'à 5 puces pertinentes dans ``canaux/<id>/memoire.md`` sans doublon.

    Args:
        canal_id: Identifiant du canal.
        puces: Liste de points clés extraits par le coordinateur.

    Returns:
        Contenu complet mis à jour de ``memoire.md``.
    """
    slug = valider_id_canal(canal_id)
    chemin = _chemin_memoire_canal(slug)
    chemin.parent.mkdir(parents=True, exist_ok=True)

    existant = chemin.read_text(encoding="utf-8", errors="replace") if chemin.is_file() else ""
    if not existant.strip():
        existant = f"# Mémoire du canal #{slug}\n\n"

    lignes_existantes = {
        normaliser_texte(ligne.lstrip("-* ").strip())
        for ligne in existant.splitlines()
        if ligne.strip().startswith(("-", "*"))
    }

    ajouts: list[str] = []
    for puce in puces[:MAX_PUCES_MEMOIRE_PAR_ECHANGE]:
        propre = str(puce or "").strip().lstrip("-* ").strip()
        if not propre:
            continue
        cle = normaliser_texte(propre)
        if cle and cle not in lignes_existantes:
            lignes_existantes.add(cle)
            ajouts.append(f"- {propre}")

    if ajouts:
        sep = "" if existant.endswith("\n") else "\n"
        existant = f"{existant}{sep}" + "\n".join(ajouts) + "\n"
        chemin.write_text(existant, encoding="utf-8")

    return existant


def initialiser_canaux_initiaux() -> list[str]:
    """Crée les canaux initiaux ``general`` et ``veille`` s'ils n'existent pas encore.

    Returns:
        Liste des identifiants de canaux nouvellement créés.
    """
    config.CANAUX_DIR.mkdir(parents=True, exist_ok=True)
    crees: list[str] = []
    for canal_id, definition in CANAUX_INITIAUX.items():
        chemin_json = _chemin_canal_json(canal_id)
        chemin_mem = _chemin_memoire_canal(canal_id)
        if not chemin_json.exists():
            chemin_json.parent.mkdir(parents=True, exist_ok=True)
            modele = Canal.model_validate(definition)
            donnees = modele.model_dump(exclude={"memoire_md"})
            chemin_json.write_text(
                json.dumps(donnees, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            crees.append(canal_id)
        if not chemin_mem.exists():
            chemin_mem.parent.mkdir(parents=True, exist_ok=True)
            chemin_mem.write_text(
                MEMOIRES_CANAUX_INITIALES.get(canal_id, f"# Mémoire du canal #{canal_id}\n\n"),
                encoding="utf-8",
            )
    return crees


def charger_canal(canal_id: str) -> Canal | None:
    """Charge un canal depuis ``canaux/<id>/canal.json`` avec sa ``memoire_md``.

    Args:
        canal_id: Identifiant slug du canal.

    Returns:
        Instance ``Canal`` enrichie de ``memoire_md``, ou ``None`` si introuvable.
    """
    slug = valider_id_canal(canal_id)
    chemin = _chemin_canal_json(slug)
    if chemin.is_file():
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        canal = Canal.model_validate(brut)
        canal.memoire_md = lire_memoire_canal(slug)
        return canal

    # Repli sur les canaux initiaux si aucun canal n'existe encore sur disque
    dossiers = [d for d in config.CANAUX_DIR.iterdir() if d.is_dir()] if config.CANAUX_DIR.exists() else []
    if not dossiers and slug in CANAUX_INITIAUX:
        canal = Canal.model_validate(CANAUX_INITIAUX[slug])
        canal.memoire_md = MEMOIRES_CANAUX_INITIALES.get(slug, "")
        return canal

    return None


def lister_canaux() -> list[dict[str, Any]]:
    """Liste tous les canaux définis sous ``canaux/*/canal.json``.

    Returns:
        Liste de dictionnaires de canaux incluant ``memoire_md``.
    """
    canaux_dir = config.CANAUX_DIR
    if not canaux_dir.exists():
        return []

    fichiers = sorted(canaux_dir.glob("*/canal.json"))
    if not fichiers:
        initialiser_canaux_initiaux()
        fichiers = sorted(canaux_dir.glob("*/canal.json"))

    par_id: dict[str, dict[str, Any]] = {}
    for fichier in fichiers:
        try:
            brut = json.loads(fichier.read_text(encoding="utf-8"))
            canal = Canal.model_validate(brut)
            canal.memoire_md = lire_memoire_canal(canal.id)
            par_id[canal.id] = canal.model_dump()
        except Exception as exc:
            logger.warning("Fichier canal illisible %s : %s", fichier, exc)

    ordonnes: list[dict[str, Any]] = []
    for defaut_id in ("general", "veille"):
        if defaut_id in par_id:
            ordonnes.append(par_id.pop(defaut_id))
    for reste_id in sorted(par_id.keys()):
        ordonnes.append(par_id[reste_id])
    return ordonnes


def enregistrer_canal(donnees: dict[str, Any] | Canal, ecraser: bool = True) -> Canal:
    """Valide et persiste un canal dans ``canaux/<id>/canal.json`` et ``memoire.md``.

    Args:
        donnees: Dictionnaire ou instance ``Canal``.
        ecraser: Si ``False``, lève ``FileExistsError`` si le canal existe déjà.

    Returns:
        Instance ``Canal`` validée et enrichie de ``memoire_md``.
    """
    brut = donnees.model_dump() if isinstance(donnees, Canal) else dict(donnees)
    slug = valider_id_canal(str(brut.get("id") or brut.get("nom") or ""))
    brut["id"] = slug
    if not brut.get("nom"):
        brut["nom"] = slug

    membres = [str(m).strip().lower() for m in (brut.get("membres") or []) if str(m).strip()]
    if not membres:
        membres = ["jarvis"]
    brut["membres"] = membres

    coord = str(brut.get("coordinateur") or "").strip().lower()
    if coord not in membres:
        coord = membres[0]
    brut["coordinateur"] = coord

    chemin_json = _chemin_canal_json(slug)
    if chemin_json.exists() and not ecraser:
        raise FileExistsError(f"Le canal '{slug}' existe déjà.")

    memoire_fournie = brut.pop("memoire_md", None)
    canal = Canal.model_validate(brut)

    chemin_json.parent.mkdir(parents=True, exist_ok=True)
    chemin_json.write_text(
        json.dumps(canal.model_dump(exclude={"memoire_md"}), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    chemin_mem = _chemin_memoire_canal(slug)
    if isinstance(memoire_fournie, str):
        chemin_mem.write_text(memoire_fournie, encoding="utf-8")
    elif not chemin_mem.exists():
        chemin_mem.write_text(f"# Mémoire du canal #{canal.nom}\n\n", encoding="utf-8")

    canal.memoire_md = lire_memoire_canal(slug)
    return canal


def supprimer_canal(canal_id: str) -> bool:
    """Envoie le dossier ``canaux/<id>`` à la corbeille système.

    Args:
        canal_id: Identifiant slug du canal.

    Returns:
        ``True`` si le dossier existait et a été envoyé à la corbeille.
    """
    dossier = _dossier_canal(canal_id)
    if not dossier.is_dir():
        return False
    send2trash(str(dossier))
    return True


async def _lire_fil_canal(conv_id: str, limite: int = LIMITE_HISTORIQUE_CANAL) -> list[dict[str, Any]]:
    """Lit les ``limite`` derniers messages du canal dans l'ordre chronologique."""
    lignes = await db.async_query(
        """
        SELECT id, auteur, role, contenu, meta, cree_le
        FROM (
            SELECT id, auteur, role, contenu, meta, cree_le
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) sub
        ORDER BY id ASC
        """,
        (conv_id, limite),
    )
    return [dict(r) for r in lignes]


def _formater_fil_pour_prompt(messages_fil: list[dict[str, Any]], memoire_md: str) -> str:
    """Construit le bloc de contexte de canal (mémoire + 20 derniers messages)."""
    blocs: list[str] = []
    if memoire_md.strip():
        blocs.append(f"MÉMOIRE DU CANAL :\n{memoire_md.strip()}")
    if messages_fil:
        lignes = [
            f"[{row.get('auteur') or row.get('role')}] : {str(row.get('contenu') or '')}"
            for row in messages_fil
        ]
        blocs.append("FIL RÉCENT DU CANAL :\n" + "\n".join(lignes))
    return "\n\n".join(blocs)


def _detecter_mentions_membres(texte: str, membres: list[str], auteur_courant: str) -> list[str]:
    """Détecte les mentions ``@membre`` dans ``texte`` dans l'ordre d'apparition."""
    if not texte or "@" not in texte:
        return []

    # Table de correspondance normalisée (id et nom d'agent -> id membre)
    table_alias: dict[str, str] = {}
    for m_id in membres:
        table_alias[normaliser_texte(m_id)] = m_id
        ag = agent_runtime.charger_agent(m_id)
        if ag is not None:
            table_alias[normaliser_texte(ag.nom)] = m_id

    cibles: list[str] = []
    for match in _MENTION_RE.finditer(texte):
        brut = normaliser_texte(match.group(1))
        cible_id = table_alias.get(brut)
        if cible_id and cible_id != auteur_courant and cible_id not in cibles:
            cibles.append(cible_id)
    return cibles


async def _choisir_intervenants(
    canal: Canal,
    message: str,
    memoire_md: str,
    moteur: Literal["auto", "gemini", "claude", "chatgpt"] = "auto",
    modele: str | None = None,
    effort: Effort = "medium",
) -> tuple[list[str], dict[str, str]]:
    """Appelle le coordinateur pour sélectionner l'ordre des agents et leurs consignes."""
    descriptions_membres: list[str] = []
    for m_id in canal.membres:
        ag = await asyncio.to_thread(agent_runtime.charger_agent, m_id)
        if ag is not None:
            descriptions_membres.append(f"- {ag.id} ({ag.nom} — {ag.titre})")
        else:
            descriptions_membres.append(f"- {m_id}")

    systeme_coord = (
        f"Tu es {canal.coordinateur}, coordinateur du canal #{canal.nom}.\n"
        f"{REGLES_ABSOLUES}\n\n"
        "Membres disponibles dans ce canal :\n"
        + "\n".join(descriptions_membres)
        + "\n\n"
        "Choisis les membres les plus pertinents (1 à N) dans un ordre logique d'intervention "
        "et rédige une consigne précise pour chacun.\n"
        'Réponds UNIQUEMENT en JSON valide : {"ordre": ["id1", "id2"], "consignes": {"id1": "...", "id2": "..."}}.'
    )
    prompt_coord = f"Message utilisateur dans #{canal.nom} :\n{message}"
    if memoire_md.strip():
        prompt_coord = f"Mémoire du canal :\n{memoire_md.strip()}\n\n{prompt_coord}"

    ag_coord = await asyncio.to_thread(agent_runtime.charger_agent, canal.coordinateur)
    modele_coord = modele or (ag_coord.modele_principal if ag_coord else "hydrogen")

    res = await llm_router.generer(
        LLMRequest(
            prompt=prompt_coord,
            systeme=systeme_coord,
            moteur=moteur,
            modele=modele_coord,
            effort=effort,
            session_cle=None,
        )
    )

    donnees = extraire_json(res.texte)
    ordre_valide: list[str] = []
    consignes_valides: dict[str, str] = {}

    if isinstance(donnees, dict):
        brut_ordre = donnees.get("ordre")
        if isinstance(brut_ordre, list):
            for item in brut_ordre:
                m_id = str(item or "").strip().lower()
                if m_id in canal.membres and m_id not in ordre_valide:
                    ordre_valide.append(m_id)

        brut_consignes = donnees.get("consignes")
        if isinstance(brut_consignes, dict):
            for k, v in brut_consignes.items():
                cle = str(k).strip().lower()
                if cle in canal.membres and isinstance(v, str):
                    consignes_valides[cle] = v.strip()

    if not ordre_valide:
        # Repli déterministe si le coordinateur n'a pas renvoyé d'ordre exploitable
        autres = [m for m in canal.membres if m != canal.coordinateur]
        ordre_valide = autres[:2] if autres else [canal.coordinateur]

    return ordre_valide, consignes_valides


async def _produire_synthese_coordinateur(
    canal: Canal,
    conv_id: str,
    message_initial: str,
    interventions: list[dict[str, str]],
    memoire_md: str,
    moteur: Literal["auto", "gemini", "claude", "chatgpt"] = "auto",
    modele: str | None = None,
    effort: Effort = "medium",
) -> dict[str, Any]:
    """Génère, streame et persiste la synthèse finale du coordinateur (``meta.synthese = True``)."""
    coord_id = canal.coordinateur
    ag_coord = await asyncio.to_thread(agent_runtime.charger_agent, coord_id)
    modele_coord = modele or (ag_coord.modele_principal if ag_coord else "hydrogen")
    secours_coord = ag_coord.modele_secours if ag_coord else "claude-sonnet"

    resume_echanges = "\n\n".join(
        f"[{item['auteur']}] :\n{item['contenu']}" for item in interventions
    )
    systeme = (
        f"Tu es {coord_id}, coordinateur du canal #{canal.nom}.\n"
        f"{REGLES_ABSOLUES}\n\n"
        "Rédige la synthèse finale structurée des interventions des agents pour répondre à l'utilisateur, "
        "et extrais jusqu'à 5 puces de mémoire durable pour le canal.\n"
        'Réponds en JSON : {"reponse": "synthèse finale en Markdown", "memoire": ["puce 1", "puce 2"]}.'
    )
    prompt = (
        f"Demande initiale de l'utilisateur : {message_initial}\n\n"
        f"Interventions de l'équipe :\n{resume_echanges}"
    )
    if memoire_md.strip():
        prompt = f"Mémoire actuelle du canal :\n{memoire_md.strip()}\n\n{prompt}"

    await agent_runtime.definir_etat_agent(coord_id, "travaille")
    await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": coord_id})

    extracteur = ExtracteurReponse()
    deja_streame: list[str] = []

    async def _sur_delta_synth(delta_brut: str) -> None:
        morceau = extracteur.alimenter(delta_brut)
        if morceau:
            deja_streame.append(morceau)
            await hub.broadcast(
                "chat_delta",
                {"conversation_id": conv_id, "auteur": coord_id, "delta": morceau},
            )

    try:
        res = await llm_router.generer(
            LLMRequest(
                prompt=prompt,
                systeme=systeme,
                moteur=moteur,
                modele=modele_coord,
                modele_secours=secours_coord,
                effort=effort,
                session_cle=f"canal:{canal.id}:{coord_id}",
            ),
            on_delta=_sur_delta_synth,
        )

        donnees = extraire_json(res.texte)
        puces_memoire: list[str] = []
        if isinstance(donnees, dict):
            texte_synthese = str(
                donnees.get("reponse") or donnees.get("synthese") or res.texte
            ).strip()
            brut_mem = donnees.get("memoire")
            if isinstance(brut_mem, list):
                puces_memoire = [
                    str(p).strip() for p in brut_mem[:MAX_PUCES_MEMOIRE_PAR_ECHANGE] if str(p).strip()
                ]
        else:
            texte_synthese = res.texte.strip()

        if not deja_streame and texte_synthese:
            await hub.broadcast(
                "chat_delta",
                {"conversation_id": conv_id, "auteur": coord_id, "delta": texte_synthese},
            )

        if puces_memoire:
            await asyncio.to_thread(ajouter_puces_memoire_canal, canal.id, puces_memoire)

        meta_dict = {
            "moteur": res.moteur,
            "modele": res.modele,
            "bascules": res.bascules,
            "synthese": True,
        }
        msg_id = await db.async_execute(
            """
            INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
            VALUES (?, 'assistant', ?, ?, ?, ?)
            """,
            (
                conv_id,
                coord_id,
                texte_synthese,
                json.dumps(meta_dict, ensure_ascii=False),
                db.maintenant_iso(),
            ),
        )

        await hub.broadcast(
            "chat_fin",
            {
                "conversation_id": conv_id,
                "auteur": coord_id,
                "message_id": msg_id,
                "contenu": texte_synthese,
                "moteur": res.moteur,
                "modele": res.modele,
                "bascules": res.bascules,
                "synthese": True,
            },
        )
        await agent_runtime.definir_etat_agent(coord_id, "fini")
        return {
            "auteur": coord_id,
            "contenu": texte_synthese,
            "message_id": msg_id,
            "synthese": True,
            "memoire": puces_memoire,
        }
    except Exception:
        await agent_runtime.definir_etat_agent(coord_id, "erreur")
        raise


async def traiter_message_canal(
    canal_id: str,
    message: str,
    moteur: str | None = None,
    modele: str | None = None,
    effort: Effort | None = None,
    *,
    _persister_user: bool = True,
) -> dict[str, Any]:
    """Orchestre un échange complet dans un canal multi-agents conformément à §11.9.

    1. Le coordinateur choisit l'ordre des agents pertinents et leurs consignes.
    2. Chaque agent répond à tour de rôle avec le fil récent (20 messages) et sa consigne.
    3. Toute mention ``@autre`` déclenche un tour supplémentaire dans la limite des plafonds.
    4. Le coordinateur produit une synthèse finale (``meta.synthese = True``) et enrichit ``memoire.md``.

    Args:
        canal_id: Identifiant du canal cible.
        message: Message envoyé par l'utilisateur.
        moteur: Préférence moteur LLM optionnelle.
        modele: Modèle catalogue optionnel.
        effort: Niveau d'effort optionnel.

    Returns:
        Dictionnaire récapitulatif ``{"conversation_id", "reponses", "synthese"}``.
    """
    slug = valider_id_canal(canal_id)
    canal = await asyncio.to_thread(charger_canal, slug)
    if canal is None:
        raise ValueError(f"Canal introuvable : {canal_id}")

    conv_id = f"canal:{slug}"
    horodatage = db.maintenant_iso()
    await db.async_execute(
        """
        INSERT OR IGNORE INTO conversations (id, type, cible_id, titre, cree_le)
        VALUES (?, 'canal', ?, ?, ?)
        """,
        (conv_id, slug, canal.nom, horodatage),
    )
    if _persister_user:
        await db.async_execute(
            """
            INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
            VALUES (?, 'user', 'utilisateur', ?, '{}', ?)
            """,
            (conv_id, message, horodatage),
        )

    moteur_valide: Literal["auto", "gemini", "claude", "chatgpt"] = (
        moteur  # type: ignore[assignment]
        if moteur in ("auto", "gemini", "claude", "chatgpt")
        else "auto"
    )
    effort_valide: Effort = (
        effort if effort in ("low", "medium", "high", "xhigh") else "medium"
    )

    await hub.broadcast("etat_jarvis", {"etat": "reflexion"})
    try:
        memoire_md = await asyncio.to_thread(lire_memoire_canal, slug)
        ordre_initial, consignes = await _choisir_intervenants(
            canal=canal,
            message=message,
            memoire_md=memoire_md,
            moteur=moteur_valide,
            modele=modele,
            effort=effort_valide,
        )

        max_par_agent = max(1, int(canal.max_tours_par_agent or 2))
        plafond_total = 2 * max(1, len(canal.membres))
        tours_par_agent: dict[str, int] = {m: 0 for m in canal.membres}
        tours_totaux = 0

        file_intervenants: deque[tuple[str, str]] = deque(
            (agent_id, consignes.get(agent_id, "")) for agent_id in ordre_initial
        )
        reponses_agents: list[dict[str, Any]] = []

        while file_intervenants and tours_totaux < plafond_total:
            agent_id, consigne_agent = file_intervenants.popleft()
            if agent_id not in canal.membres:
                continue
            if tours_par_agent.get(agent_id, 0) >= max_par_agent:
                continue

            tours_par_agent[agent_id] = tours_par_agent.get(agent_id, 0) + 1
            tours_totaux += 1

            fil_recent = await _lire_fil_canal(conv_id, limite=LIMITE_HISTORIQUE_CANAL)
            contexte_canal = _formater_fil_pour_prompt(fil_recent, memoire_md)
            if consigne_agent:
                contexte_canal = (
                    f"{contexte_canal}\n\n"
                    f"CONSIGNE DU COORDINATEUR ({canal.coordinateur}) POUR TOI ({agent_id}) :\n"
                    f"{consigne_agent}"
                ).strip()

            await hub.broadcast(
                "chat_debut",
                {"conversation_id": conv_id, "auteur": agent_id},
            )

            async def _sur_delta_membre(delta: str, _auteur: str = agent_id) -> None:
                await hub.broadcast(
                    "chat_delta",
                    {"conversation_id": conv_id, "auteur": _auteur, "delta": delta},
                )

            texte_rep = await agent_runtime.repondre(
                agent_id=agent_id,
                conversation_id=conv_id,
                message=message,
                moteur=moteur_valide,
                modele=modele,
                effort=effort,
                on_delta=_sur_delta_membre,
                historique_canal=contexte_canal,
                _persister_user=False,
                _persister_assistant=True,
                _session_cle_override=f"canal:{slug}:{agent_id}",
            )

            detail = agent_runtime.dernier_detail_reponse(conv_id)
            await hub.broadcast(
                "chat_fin",
                {
                    "conversation_id": conv_id,
                    "auteur": agent_id,
                    "message_id": detail.message_id if detail else None,
                    "contenu": texte_rep,
                    "moteur": detail.moteur if detail else "agy",
                    "modele": detail.modele if detail else (modele or "hydrogen"),
                    "bascules": detail.bascules if detail else [],
                },
            )

            reponses_agents.append(
                {
                    "auteur": agent_id,
                    "contenu": texte_rep,
                    "message_id": detail.message_id if detail else None,
                }
            )

            # Détection des mentions @autre dans la réponse de l'agent
            mentions = await asyncio.to_thread(
                _detecter_mentions_membres, texte_rep, canal.membres, agent_id
            )
            for cible_mention in mentions:
                if (
                    tours_par_agent.get(cible_mention, 0) < max_par_agent
                    and tours_totaux + len(file_intervenants) < plafond_total
                ):
                    file_intervenants.append(
                        (
                            cible_mention,
                            f"Réponds à l'interpellation de @{agent_id} : {texte_rep[:300]}",
                        )
                    )

        synthese = await _produire_synthese_coordinateur(
            canal=canal,
            conv_id=conv_id,
            message_initial=message,
            interventions=reponses_agents,
            memoire_md=memoire_md,
            moteur=moteur_valide,
            modele=modele,
            effort=effort_valide,
        )

        return {
            "conversation_id": conv_id,
            "reponses": reponses_agents,
            "synthese": synthese,
        }
    except Exception as exc:
        logger.exception("Erreur lors du traitement du canal %s", slug)
        payload_err: dict[str, Any] = {
            "conversation_id": conv_id,
            "auteur": canal.coordinateur,
            "erreur": str(exc),
        }
        code_err = getattr(exc, "code", None)
        if code_err:
            payload_err["code"] = code_err
        await hub.broadcast("chat_erreur", payload_err)
        raise
    finally:
        await hub.broadcast("etat_jarvis", {"etat": "repos"})
