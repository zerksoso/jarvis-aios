"""Routes de conversation et traitement asynchrone des messages avec streaming WS."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, WebSocket
from pydantic import BaseModel, Field, ValidationError

from backend import db, settings_store
from backend.brain.memories import extraire_souvenirs
from backend.llm import router as llm_router
from backend.llm.base import Effort, LLMRequest
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

TraiteurFn = Callable[[str, str, str, str, str, str | None, Effort], Awaitable[None]]

PERSONA_JARVIS_MINIMAL = (
    "Tu es Jarvis, l'assistant personnel de l'utilisateur. "
    "Réponds en français, de façon concise et utile."
)

_traiteur_custom: TraiteurFn | None = None
_taches_actives: set[asyncio.Task[Any]] = set()


class ChatRequest(BaseModel):
    """Corps de requête pour l'envoi d'un message de chat."""

    cible: Literal["agent", "canal"] = "agent"
    id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    moteur: Literal["auto", "gemini", "claude", "chatgpt"] | None = None
    modele: str | None = None
    effort: Effort | None = None


def set_traiteur(fn: TraiteurFn | None) -> None:
    """Remplace le gestionnaire de traitement de messages (hook pour les lots suivants).

    Args:
        fn: Coroutine ``(conv_id, cible, id, message, moteur, modele, effort)``,
            ou ``None`` pour restaurer ``traiter_message`` par défaut.
    """
    global _traiteur_custom
    _traiteur_custom = fn


async def traiter_message(
    conv_id: str,
    cible: str,
    id: str,
    message: str,
    moteur: str,
    modele: str | None,
    effort: Effort,
) -> None:
    """Traite un message entrant via le routeur LLM et diffuse le streaming sur le hub WS.

    Args:
        conv_id: Identifiant de conversation (``"<cible>:<id>"``).
        cible: Type de cible (``"agent"`` ou ``"canal"``).
        id: Identifiant de l'agent ou du canal destinataire.
        message: Contenu du message utilisateur.
        moteur: Préférence moteur (``auto``, ``gemini``, ``claude``, ``chatgpt``).
        modele: Identifiant de modèle catalogue ou ``None``.
        effort: Niveau d'effort demandé.

    Effets de bord:
        Diffuse les événements ``etat_jarvis``, ``chat_debut``, ``chat_delta``,
        ``chat_fin`` / ``chat_erreur`` et enregistre la réponse dans ``messages``.
    """
    await hub.broadcast("etat_jarvis", {"etat": "reflexion"})
    await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": id})

    try:
        moteur_valide: Literal["auto", "gemini", "claude", "chatgpt"] = (
            moteur if moteur in ("auto", "gemini", "claude", "chatgpt") else "auto"
        )
        req = LLMRequest(
            prompt=message,
            systeme=PERSONA_JARVIS_MINIMAL,
            moteur=moteur_valide,
            modele=modele,
            effort=effort,
            session_cle=f"{cible}:{id}",
        )

        async def _sur_delta(delta: str) -> None:
            await hub.broadcast(
                "chat_delta",
                {"conversation_id": conv_id, "auteur": id, "delta": delta},
            )

        res = await llm_router.generer(req, on_delta=_sur_delta)

        meta_json = json.dumps(
            {
                "moteur": res.moteur,
                "modele": res.modele,
                "bascules": res.bascules,
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
            },
            ensure_ascii=False,
        )
        msg_id = await db.async_execute(
            """
            INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
            VALUES (?, 'assistant', ?, ?, ?, ?)
            """,
            (conv_id, id, res.texte, meta_json, db.maintenant_iso()),
        )

        await hub.broadcast(
            "chat_fin",
            {
                "conversation_id": conv_id,
                "auteur": id,
                "message_id": msg_id,
                "contenu": res.texte,
                "moteur": res.moteur,
                "modele": res.modele,
            },
        )
        if settings_store.lire().get("memoire_auto", True):
            compte = await db.async_query_one(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND role = 'user'",
                (conv_id,),
            )
            nb_user = int((compte or {}).get("n") or 0)
            if nb_user > 0 and nb_user % 6 == 0:
                tache_mem = asyncio.create_task(extraire_souvenirs(conv_id))
                _taches_actives.add(tache_mem)
                tache_mem.add_done_callback(_taches_actives.discard)
    except Exception as exc:
        logger.exception("Erreur lors du traitement chat %s", conv_id)
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


def _resoudre_options_llm(body: ChatRequest) -> tuple[str, str | None, Effort]:
    """Détermine moteur, modèle et effort en combinant la requête et les paramètres."""
    params = settings_store.lire()
    moteur = body.moteur or str(params.get("moteur") or "auto")
    if body.modele is not None:
        modele: str | None = body.modele
    elif body.moteur is None:
        modele = params.get("modele")
    else:
        modele = None

    effort_brut = body.effort or str(params.get("effort") or "medium")
    effort: Effort = (
        effort_brut if effort_brut in ("low", "medium", "high", "xhigh") else "medium"
    )
    return moteur, modele, effort


async def demarrer_chat(body: ChatRequest) -> str:
    """Persiste le message utilisateur puis lance le traitement LLM en tâche de fond.

    Args:
        body: Données validées de la requête chat.

    Returns:
        Identifiant ``conversation_id`` (``"<cible>:<id>"``).

    Effets de bord:
        Crée la conversation si besoin, insère le message utilisateur en base SQLite
        et planifie une tâche asyncio asynchrone.
    """
    conv_id = f"{body.cible}:{body.id}"
    horodatage = db.maintenant_iso()

    await db.async_execute(
        """
        INSERT OR IGNORE INTO conversations (id, type, cible_id, titre, cree_le)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conv_id, body.cible, body.id, body.id, horodatage),
    )
    await db.async_execute(
        """
        INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
        VALUES (?, 'user', 'utilisateur', ?, '{}', ?)
        """,
        (conv_id, body.message, horodatage),
    )

    moteur, modele, effort = _resoudre_options_llm(body)
    traiteur = _traiteur_custom or traiter_message

    tache = asyncio.create_task(
        traiteur(conv_id, body.cible, body.id, body.message, moteur, modele, effort)
    )
    _taches_actives.add(tache)
    tache.add_done_callback(_taches_actives.discard)
    return conv_id


async def handle_ws_chat(ws: WebSocket, data: dict[str, Any]) -> None:
    """Traite une commande ``chat`` reçue directement via WebSocket.

    Args:
        ws: WebSocket émettrice.
        data: Données du message client.
    """
    try:
        body = ChatRequest.model_validate(data)
    except ValidationError as exc:
        await hub.send_personal(
            ws,
            "chat_erreur",
            {"conversation_id": "", "auteur": "", "erreur": str(exc)},
        )
        return
    await demarrer_chat(body)


@router.post("/chat")
async def envoyer_message(body: ChatRequest) -> dict[str, str]:
    """Enregistre un message utilisateur et démarre la réponse assistante en arrière-plan.

    Args:
        body: Requête ``ChatRequest``.

    Returns:
        Dictionnaire ``{"conversation_id": ...}``.
    """
    conv_id = await demarrer_chat(body)
    return {"conversation_id": conv_id}


@router.get("/conversations/{conv_type}/{cible_id}/messages")
async def lire_messages_conversation(
    conv_type: Literal["agent", "canal"],
    cible_id: str,
    limite: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:
    """Retourne les messages d'une conversation dans l'ordre chronologique.

    Args:
        conv_type: Type de conversation (``"agent"`` ou ``"canal"``).
        cible_id: Identifiant de la cible.
        limite: Nombre maximal de messages récents à renvoyer (1 à 1000).

    Returns:
        Dictionnaire ``{"conversation_id": ..., "messages": [...]}``.
    """
    conv_id = f"{conv_type}:{cible_id}"
    lignes = await db.async_query(
        """
        SELECT id, conversation_id, role, auteur, contenu, meta, cree_le
        FROM (
            SELECT id, conversation_id, role, auteur, contenu, meta, cree_le
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT ?
        ) sub
        ORDER BY id ASC
        """,
        (conv_id, limite),
    )

    messages: list[dict[str, Any]] = []
    for ligne in lignes:
        item = dict(ligne)
        meta_brut = item.get("meta")
        if isinstance(meta_brut, str):
            try:
                item["meta"] = json.loads(meta_brut)
            except json.JSONDecodeError:
                item["meta"] = {}
        messages.append(item)

    return {"conversation_id": conv_id, "messages": messages}
