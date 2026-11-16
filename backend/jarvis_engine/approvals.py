"""Gestionnaire d'approbations utilisateur bloquantes (SQLite + asyncio.Event + WebSocket)."""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from backend import db
from backend.ws_hub import hub, on_client_message

logger = logging.getLogger(__name__)


@dataclass
class _AttenteApprobation:
    """État interne d'une demande d'accord en attente de décision utilisateur."""

    event: asyncio.Event
    source: str
    decision: bool | None = None


_attentes: dict[str, _AttenteApprobation] = {}


def reset_etat_pour_tests() -> None:
    """Réinitialise la table mémoire des attentes d'approbation pour l'isolation des tests."""
    _attentes.clear()


def _formater_ligne_approbation(row: dict[str, Any]) -> dict[str, Any]:
    """Convertit une ligne SQLite ``approbations`` en dictionnaire JSON normalisé."""
    action_brute = row.get("action")
    action_dict: dict[str, Any] = {}
    if isinstance(action_brute, str) and action_brute.strip():
        try:
            parsed = json.loads(action_brute)
            if isinstance(parsed, dict):
                action_dict = parsed
        except json.JSONDecodeError:
            action_dict = {}
    elif isinstance(action_brute, dict):
        action_dict = action_brute

    return {
        "id": str(row["id"]),
        "source": str(row.get("source") or "jarvis"),
        "action": action_dict,
        "resume": str(row.get("resume") or ""),
        "risque": str(row.get("risque") or "irreversible"),
        "statut": str(row.get("statut") or "en_attente"),
        "cree_le": str(row.get("cree_le") or ""),
        "decide_le": row.get("decide_le"),
    }


async def _notifier_etat_agent(agent_id: str, etat: str) -> None:
    """Met à jour l'état temps réel de l'agent source et diffuse ``agent_etat``."""
    if not agent_id:
        return
    try:
        from backend.agents.agent_runtime import definir_etat_agent

        await definir_etat_agent(agent_id, etat)  # type: ignore[arg-type]
    except ImportError:
        await hub.broadcast("agent_etat", {"id": agent_id, "etat": etat})


def lister_approbations(statut: str | None = "en_attente") -> list[dict[str, Any]]:
    """Liste les demandes d'approbation enregistrées en base SQLite.

    Args:
        statut: Filtre optionnel sur le statut (ex. ``"en_attente"``). Passer ``None`` ou ``""``
            pour retourner toutes les approbations.

    Returns:
        Liste de dictionnaires d'approbation avec ``action`` désérialisé.
    """
    if statut and statut.strip():
        lignes = db.query(
            """
            SELECT id, source, action, resume, risque, statut, cree_le, decide_le
            FROM approbations
            WHERE statut = ?
            ORDER BY cree_le DESC, id DESC
            """,
            (statut.strip(),),
        )
    else:
        lignes = db.query(
            """
            SELECT id, source, action, resume, risque, statut, cree_le, decide_le
            FROM approbations
            ORDER BY cree_le DESC, id DESC
            """
        )
    return [_formater_ligne_approbation(row) for row in lignes]


async def repondre_approbation(approbation_id: str, decision: str) -> dict[str, Any] | None:
    """Enregistre la décision utilisateur (``oui``/``non``) et débloque la tâche en attente.

    Args:
        approbation_id: Identifiant de la demande d'approbation.
        decision: Décision de l'utilisateur (``"oui"`` ou ``"non"``).

    Returns:
        Dictionnaire de l'approbation mise à jour, ou ``None`` si introuvable.

    Raises:
        ValueError: Si ``decision`` n'est ni ``"oui"`` ni ``"non"``.

    Effets de bord:
        Met à jour la table ``approbations``, déclenche l'``asyncio.Event`` associé et
        diffuse ``approbation_resultat`` sur le hub WebSocket.
    """
    dec_norm = decision.strip().lower()
    if dec_norm not in ("oui", "non"):
        raise ValueError("La décision doit être 'oui' ou 'non'.")

    ligne = await db.async_query_one(
        "SELECT id, source, action, resume, risque, statut, cree_le, decide_le FROM approbations WHERE id = ?",
        (approbation_id,),
    )
    if ligne is None:
        return None

    autorise = dec_norm == "oui"
    nouveau_statut = "autorisee" if autorise else "refusee"
    decide_le = db.maintenant_iso()

    await db.async_execute(
        "UPDATE approbations SET statut = ?, decide_le = ? WHERE id = ?",
        (nouveau_statut, decide_le, approbation_id),
    )

    attente = _attentes.get(approbation_id)
    if attente is not None:
        attente.decision = autorise
        attente.event.set()

    await hub.broadcast(
        "approbation_resultat",
        {
            "id": approbation_id,
            "statut": nouveau_statut,
            "decision": dec_norm,
        },
    )

    maj = dict(ligne)
    maj["statut"] = nouveau_statut
    maj["decide_le"] = decide_le
    return _formater_ligne_approbation(maj)


async def demander_accord(
    source: str,
    resume: str,
    action: dict[str, Any],
    risque: str,
    timeout_s: float = 300.0,
) -> bool:
    """Crée une demande d'approbation et suspend l'exécution jusqu'à décision ou expiration.

    Args:
        source: Identifiant de l'agent ou composant émetteur.
        resume: Description courte et lisible de l'action soumise à validation.
        action: Détail technique structuré de l'action envisagée.
        risque: Niveau de risque (ex. ``"irreversible"``, ``"action_pc"``).
        timeout_s: Délai maximal d'attente en secondes avant expiration (défaut 300 s).

    Returns:
        ``True`` si l'utilisateur a accepté (``"oui"``), ``False`` en cas de refus ou d'expiration.

    Effets de bord:
        Insère une ligne dans ``approbations``, passe l'agent source à ``attend_accord``,
        diffuse ``approbation_demande`` puis ``approbation_resultat`` en cas d'expiration.
    """
    app_id = f"apr-{uuid.uuid4().hex[:12]}"
    cree_le = db.maintenant_iso()
    action_propre = dict(action or {})
    action_json = json.dumps(action_propre, ensure_ascii=False, default=str)
    source_propre = (source or "jarvis").strip() or "jarvis"
    risque_propre = (risque or "irreversible").strip() or "irreversible"

    await db.async_execute(
        """
        INSERT INTO approbations (id, source, action, resume, risque, statut, cree_le, decide_le)
        VALUES (?, ?, ?, ?, ?, 'en_attente', ?, NULL)
        """,
        (app_id, source_propre, action_json, resume, risque_propre, cree_le),
    )

    attente = _AttenteApprobation(event=asyncio.Event(), source=source_propre)
    _attentes[app_id] = attente

    await _notifier_etat_agent(source_propre, "attend_accord")
    await hub.broadcast(
        "approbation_demande",
        {
            "id": app_id,
            "source": source_propre,
            "resume": resume,
            "risque": risque_propre,
            "action": action_propre,
        },
    )

    try:
        await asyncio.wait_for(attente.event.wait(), timeout=timeout_s)
        return bool(attente.decision)
    except TimeoutError:
        decide_le = db.maintenant_iso()
        await db.async_execute(
            "UPDATE approbations SET statut = 'expiree', decide_le = ? WHERE id = ? AND statut = 'en_attente'",
            (decide_le, app_id),
        )
        await hub.broadcast(
            "approbation_resultat",
            {"id": app_id, "statut": "expiree"},
        )
        return False
    finally:
        _attentes.pop(app_id, None)


async def handle_ws_approbation_reponse(ws: WebSocket | None, data: dict[str, Any]) -> None:
    """Gestionnaire WebSocket pour le message client ``approbation_reponse``.

    Args:
        ws: Connexion WebSocket cliente (optionnelle).
        data: Charge utile ``{"id": str, "decision": "oui"|"non"}``.
    """
    del ws
    if not isinstance(data, dict):
        return
    app_id = str(data.get("id") or "").strip()
    decision = str(data.get("decision") or "").strip().lower()
    if not app_id or decision not in ("oui", "non"):
        return
    try:
        await repondre_approbation(app_id, decision)
    except Exception as exc:
        logger.warning("Erreur lors du traitement WS approbation_reponse (%s) : %s", app_id, exc)


on_client_message("approbation_reponse", handle_ws_approbation_reponse)
