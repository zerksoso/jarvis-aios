"""Gestion des souvenirs persistants (``memories.json``), ``SOUVENIRS.md`` et extraction auto."""

import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from backend import config, db, settings_store
from backend.brain import indexer
from backend.brain.parser import normaliser_texte
from backend.llm import router as llm_router
from backend.llm.base import LLMRequest
from backend.llm.jsonparse import extraire_json

logger = logging.getLogger(__name__)

TYPES_SOUVENIRS: tuple[str, ...] = ("preference", "correction", "regle", "fait")

_TITRES_SECTIONS: dict[str, str] = {
    "preference": "Préférences",
    "correction": "Corrections",
    "regle": "Règles",
    "fait": "Faits",
}

_lock = threading.Lock()
_PONCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _chemin_memories() -> Path:
    """Retourne le chemin vers ``BRAIN_DIR/memories.json``."""
    return config.BRAIN_DIR / "memories.json"


def _chemin_souvenirs_md() -> Path:
    """Retourne le chemin vers ``BRAIN_DIR/SOUVENIRS.md``."""
    return config.BRAIN_DIR / "SOUVENIRS.md"


def _cle_dedup(texte: str) -> str:
    """Normalise un texte de souvenir pour la déduplication insensible à la casse/ponctuation."""
    norm = normaliser_texte(texte)
    sans_ponct = _PONCTUATION_RE.sub(" ", norm)
    return " ".join(sans_ponct.split())


def _lire_brut() -> list[dict[str, Any]]:
    """Lit la liste brute des souvenirs depuis ``memories.json`` sans verrou externe."""
    chemin = _chemin_memories()
    if not chemin.is_file():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Fichier memories.json illisible (%s) ; retour liste vide.", exc)
        return []
    if not isinstance(donnees, list):
        return []
    return [item for item in donnees if isinstance(item, dict)]


def _ecrire_brut(souvenirs: list[dict[str, Any]]) -> None:
    """Écrit ``memories.json`` en UTF-8 formaté."""
    chemin = _chemin_memories()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps(souvenirs, indent=2, ensure_ascii=False), encoding="utf-8")


def _ecrire_souvenirs_md(souvenirs: list[dict[str, Any]]) -> Path:
    """Génère ``SOUVENIRS.md`` groupé par type et le réindexe si la base est prête."""
    aujourd_hui = date.today().isoformat()
    lignes: list[str] = [
        "---",
        "titre: Souvenirs et préférences",
        "tags: [souvenirs]",
        "branche: autres",
        "statut: actif",
        f"date: {aujourd_hui}",
        "---",
        "",
        "# Souvenirs de Jarvis",
        "",
        "Ce document est généré automatiquement à partir de `memories.json`.",
        "",
    ]

    for type_s in TYPES_SOUVENIRS:
        entrees = [s for s in souvenirs if s.get("type") == type_s]
        lignes.append(f"## {_TITRES_SECTIONS[type_s]}")
        lignes.append("")
        if not entrees:
            lignes.append("_Aucun souvenir enregistré dans cette catégorie._")
        else:
            for item in entrees:
                texte = str(item.get("texte") or "").strip()
                agent = str(item.get("agent") or "jarvis").strip()
                cree_le = str(item.get("cree_le") or "")[:10]
                meta_txt = f" _(agent : {agent}, {cree_le})_" if cree_le else f" _(agent : {agent})_"
                lignes.append(f"- {texte}{meta_txt}")
        lignes.append("")

    chemin_md = _chemin_souvenirs_md()
    chemin_md.parent.mkdir(parents=True, exist_ok=True)
    chemin_md.write_text("\n".join(lignes), encoding="utf-8")

    try:
        indexer.indexer_fichier(chemin_md)
    except sqlite3.Error:
        pass
    return chemin_md


def lister_souvenirs() -> list[dict[str, Any]]:
    """Retourne tous les souvenirs persistés dans ``second-brain/memories.json``.

    Returns:
        Liste de dictionnaires ``{"id", "type", "texte", "source", "agent", "cree_le"}``.
    """
    with _lock:
        return _lire_brut()


def regenerer_souvenirs_md(souvenirs: list[dict[str, Any]] | None = None) -> Path:
    """Régénère ``second-brain/SOUVENIRS.md`` à partir de la liste fournie ou du JSON.

    Args:
        souvenirs: Liste optionnelle de souvenirs ; si ``None``, lit ``memories.json``.

    Returns:
        Chemin absolu du fichier ``SOUVENIRS.md`` généré.
    """
    with _lock:
        donnees = souvenirs if souvenirs is not None else _lire_brut()
        return _ecrire_souvenirs_md(donnees)


def ajouter_souvenir(
    type_souvenir: str,
    texte: str,
    source: str = "manuel",
    agent: str = "jarvis",
) -> dict[str, Any] | None:
    """Ajoute un souvenir dédupliqué dans ``memories.json`` et régénère ``SOUVENIRS.md``.

    Un souvenir dont le texte normalisé (minuscules, sans accents ni ponctuation) existe déjà
    est ignoré silencieusement et la fonction retourne ``None``.

    Args:
        type_souvenir: Catégorie parmi ``preference``, ``correction``, ``regle``, ``fait``.
        texte: Contenu textuel du souvenir.
        source: Origine (``manuel``, id de conversation, etc.).
        agent: Identifiant de l'agent concerné.

    Returns:
        Dictionnaire du souvenir créé, ou ``None`` s'il s'agit d'un doublon.

    Raises:
        ValueError: Si le type est inconnu ou si le texte est vide.
    """
    type_propre = type_souvenir.strip().lower()
    if type_propre not in TYPES_SOUVENIRS:
        raise ValueError(f"Type de souvenir invalide : {type_souvenir!r}")

    texte_propre = " ".join(texte.strip().split())
    if not texte_propre:
        raise ValueError("Le texte du souvenir ne peut pas être vide.")

    cle_nouvelle = _cle_dedup(texte_propre)

    with _lock:
        souvenirs = _lire_brut()
        for existant in souvenirs:
            if _cle_dedup(str(existant.get("texte") or "")) == cle_nouvelle:
                return None

        nouveau: dict[str, Any] = {
            "id": f"mem-{uuid.uuid4().hex[:10]}",
            "type": type_propre,
            "texte": texte_propre,
            "source": source.strip() or "manuel",
            "agent": agent.strip() or "jarvis",
            "cree_le": db.maintenant_iso(),
        }
        souvenirs.append(nouveau)
        _ecrire_brut(souvenirs)
        _ecrire_souvenirs_md(souvenirs)
        return nouveau


def supprimer_souvenir(souvenir_id: str) -> bool:
    """Supprime un souvenir par son identifiant et régénère ``SOUVENIRS.md``.

    Args:
        souvenir_id: Identifiant du souvenir à retirer.

    Returns:
        ``True`` si un souvenir a été supprimé, ``False`` s'il est introuvable.
    """
    with _lock:
        souvenirs = _lire_brut()
        filtres = [s for s in souvenirs if s.get("id") != souvenir_id]
        if len(filtres) == len(souvenirs):
            return False
        _ecrire_brut(filtres)
        _ecrire_souvenirs_md(filtres)
        return True


async def extraire_souvenirs(conversation_id: str) -> list[dict[str, Any]]:
    """Extrait automatiquement des souvenirs durables depuis les 12 derniers messages.

    N'agit que si ``memoire_auto`` est activé dans les paramètres. Ne bloque jamais et
    journalise toute erreur sans la propager.

    Args:
        conversation_id: Identifiant de la conversation (ex. ``"agent:jarvis"``).

    Returns:
        Liste des nouveaux souvenirs effectivement ajoutés.
    """
    try:
        params = settings_store.lire()
        if not params.get("memoire_auto", True):
            return []

        lignes = await db.async_query(
            """
            SELECT role, auteur, contenu
            FROM (
                SELECT id, role, auteur, contenu
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC
                LIMIT 12
            ) sub
            ORDER BY id ASC
            """,
            (conversation_id,),
        )
        if not lignes:
            return []

        extrait_conv = "\n".join(
            f"[{row.get('auteur') or row.get('role')}] : {str(row.get('contenu') or '')[:600]}"
            for row in lignes
        )
        systeme = (
            "Tu extrais uniquement les souvenirs durables et utiles pour un assistant personnel "
            "(préférences utilisateur, corrections, règles explicites, faits stables). "
            'Réponds UNIQUEMENT en JSON valide : {"souvenirs":[{"type":"preference|correction|regle|fait","texte":"..."}]}. '
            "Si rien de durable n'apparaît, retourne {\"souvenirs\": []}."
        )
        res = await llm_router.generer(
            LLMRequest(
                prompt=f"Conversation récente :\n{extrait_conv}",
                systeme=systeme,
                modele="hydrogen",
                effort="low",
                session_cle=None,
                timeout_s=60,
            )
        )
        donnees = extraire_json(res.texte)
        if not isinstance(donnees, dict):
            return []

        bruts = donnees.get("souvenirs")
        if not isinstance(bruts, list):
            return []

        agent_nom = conversation_id.split(":", 1)[1] if ":" in conversation_id else "jarvis"
        ajoutes: list[dict[str, Any]] = []
        for item in bruts:
            if not isinstance(item, dict):
                continue
            type_s = str(item.get("type") or "").strip().lower()
            texte = str(item.get("texte") or "").strip()
            if type_s not in TYPES_SOUVENIRS or not texte:
                continue
            cree = ajouter_souvenir(type_s, texte, source=conversation_id, agent=agent_nom)
            if cree is not None:
                ajoutes.append(cree)
        return ajoutes
    except Exception as exc:
        logger.warning("Échec de l'extraction automatique de souvenirs (%s) : %s", conversation_id, exc)
        return []
