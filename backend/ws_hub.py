"""Hub WebSocket centralisé pour la diffusion temps réel vers l'UI et l'overlay."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

WSHandler = Callable[..., Awaitable[None] | None]
EcouteurWS = Callable[[str, dict[str, Any]], Awaitable[None] | None]


def _enveloppe(msg_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Construit l'enveloppe JSON standard ``{"type", "data", "ts"}``."""
    return {
        "type": msg_type,
        "data": data if data is not None else {},
        "ts": datetime.now().isoformat(timespec="seconds"),
    }


async def _invoquer_handler(handler: WSHandler, ws: WebSocket | None, data: dict[str, Any]) -> None:
    """Invoque un handler client acceptant ``(ws, data)`` ou ``(data,)`` (sync ou async)."""
    try:
        sig = inspect.signature(handler)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        a_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in sig.parameters.values())
        if len(params) == 1 and not a_varargs:
            res = handler(data)
        else:
            res = handler(ws, data)
    except (ValueError, TypeError):
        res = handler(ws, data)

    if inspect.isawaitable(res):
        await res


class Hub:
    """Gestionnaire des connexions WebSocket UI/overlay et de la diffusion d'événements."""

    def __init__(self) -> None:
        """Initialise un hub vide sans clients ni boucle mémorisée."""
        self._clients: dict[WebSocket, str] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handlers: dict[str, WSHandler] = {}
        self._persistent_handlers: dict[str, WSHandler] = {}
        self._ecouteurs: list[EcouteurWS] = []
        self._taches_ecouteurs: set[asyncio.Task[Any]] = set()

    @property
    def overlay_connecte(self) -> bool:
        """Indique si au moins un client ``overlay`` est actuellement connecté.

        Returns:
            ``True`` si un client de type ``"overlay"`` est actif.
        """
        return any(client == "overlay" for client in self._clients.values())

    def set_loop(self, loop: asyncio.AbstractEventLoop | None) -> None:
        """Mémorise la boucle asyncio principale pour les diffusions depuis des threads.

        Args:
            loop: Boucle asyncio du serveur FastAPI, ou ``None`` à l'arrêt.
        """
        self._loop = loop

    def reset(self) -> None:
        """Réinitialise l'état dynamique du hub tout en conservant les handlers permanents."""
        self._clients.clear()
        self._handlers = dict(self._persistent_handlers)
        self._ecouteurs.clear()
        for tache in list(self._taches_ecouteurs):
            tache.cancel()
        self._taches_ecouteurs.clear()
        self._loop = None

    def ajouter_ecouteur(self, fn: EcouteurWS) -> None:
        """Enregistre un écouteur interne appelé à chaque diffusion ``broadcast``.

        Args:
            fn: Fonction synchrone ou coroutine ``fn(msg_type, data)``.

        Effets de bord:
            Ajoute ``fn`` à la liste interne des écouteurs s'il n'y est pas déjà.
        """
        if fn not in self._ecouteurs:
            self._ecouteurs.append(fn)

    def retirer_ecouteur(self, fn: EcouteurWS) -> None:
        """Retire un écouteur interne préalablement enregistré.

        Args:
            fn: Référence de la fonction ou coroutine à retirer.

        Effets de bord:
            Supprime ``fn`` de la liste interne s'il est présent.
        """
        while fn in self._ecouteurs:
            self._ecouteurs.remove(fn)

    async def _executer_ecouteur_async(self, awaitable: Awaitable[Any], msg_type: str) -> None:
        """Exécute un écouteur asynchrone en journalisant toute erreur sans la propager."""
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Erreur dans un écouteur asynchrone du hub (%s).", msg_type)

    def register_handler(self, msg_type: str, handler: WSHandler) -> None:
        """Enregistre un gestionnaire asynchrone pour un type de message client entrant.

        Args:
            msg_type: Nom du type de message client (ex. ``"chat"``, ``"ecoute"``).
            handler: Coroutine recevant ``(websocket, data)`` ou ``(data,)``.
        """
        self._handlers[msg_type] = handler

    def on_client_message(self, msg_type: str, handler: WSHandler) -> None:
        """Enregistre un gestionnaire permanent pour un type de message client WebSocket.

        Args:
            msg_type: Type du message client entrant (ex. ``"approbation_reponse"``).
            handler: Fonction ou coroutine recevant ``(websocket, data)`` ou ``(data,)``.
        """
        self._persistent_handlers[msg_type] = handler
        self._handlers[msg_type] = handler

    async def connect(self, ws: WebSocket, client: str = "ui") -> None:
        """Accepte une connexion WebSocket et l'enregistre sous son type client.

        Si le client est un ``overlay``, diffuse immédiatement ``overlay_statut``.

        Args:
            ws: Instance ``WebSocket`` FastAPI entrante.
            client: Rôle du client (``"ui"`` ou ``"overlay"``).

        Effets de bord:
            Accepte la socket et modifie la table interne des clients connectés.
        """
        role = "overlay" if client == "overlay" else "ui"
        await ws.accept()
        self._clients[ws] = role
        if role == "overlay":
            await self.broadcast("overlay_statut", {"connecte": self.overlay_connecte})

    async def disconnect(self, ws: WebSocket) -> None:
        """Retire un client WebSocket déconnecté et met à jour l'état overlay si besoin.

        Args:
            ws: Instance ``WebSocket`` à retirer.

        Effets de bord:
            Supprime la socket du registre et diffuse ``overlay_statut`` si un overlay part.
        """
        role = self._clients.pop(ws, None)
        if role == "overlay":
            await self.broadcast("overlay_statut", {"connecte": self.overlay_connecte})

    async def send_personal(
        self,
        ws: WebSocket,
        msg_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Envoie une enveloppe ciblée à un seul client WebSocket.

        Args:
            ws: Destinataire WebSocket.
            msg_type: Type d'événement.
            data: Charge utile JSON.
        """
        await ws.send_json(_enveloppe(msg_type, data))

    async def broadcast(
        self,
        msg_type: str,
        data: dict[str, Any] | None = None,
        cible: str = "tous",
    ) -> None:
        """Diffuse un événement vers tous les clients ou uniquement ``ui`` / ``overlay``.

        Nettoie silencieusement les sockets fermées ou en erreur.

        Args:
            msg_type: Type d'événement serveur → client.
            data: Dictionnaire de données associé.
            cible: Filtre de destination (``"tous"``, ``"ui"`` ou ``"overlay"``).
        """
        payload = _enveloppe(msg_type, data)
        morts: list[WebSocket] = []
        for ws, role in list(self._clients.items()):
            if cible != "tous" and role != cible:
                continue
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.debug("Suppression d'un client WebSocket injoignable (%s).", exc)
                morts.append(ws)

        for ws in morts:
            await self.disconnect(ws)

        donnees = payload["data"]
        for ecouteur in list(self._ecouteurs):
            try:
                res = ecouteur(msg_type, donnees)
                if inspect.isawaitable(res):
                    tache = asyncio.create_task(self._executer_ecouteur_async(res, msg_type))
                    self._taches_ecouteurs.add(tache)
                    tache.add_done_callback(self._taches_ecouteurs.discard)
            except Exception:
                logger.exception("Erreur dans un écouteur interne du hub (%s).", msg_type)

    def broadcast_threadsafe(
        self,
        msg_type: str,
        data: dict[str, Any] | None = None,
        cible: str = "tous",
    ) -> None:
        """Planifie une diffusion WebSocket depuis un thread synchrone secondaire.

        Sans effet si la boucle asyncio n'est pas encore mémorisée ou est fermée.

        Args:
            msg_type: Type d'événement.
            data: Charge utile JSON.
            cible: Destination (``"tous"``, ``"ui"`` ou ``"overlay"``).
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(msg_type, data, cible=cible), loop)
        except RuntimeError as exc:
            logger.debug("Impossible de diffuser depuis un thread (%s).", exc)

    async def handle_client_message(self, ws: WebSocket, raw: dict[str, Any]) -> None:
        """Aiguille un message JSON envoyé par un client vers le handler correspondant.

        Gère nativement ``ping`` → ``pong`` et ``overlay_hello``.

        Args:
            ws: WebSocket émettrice.
            raw: Dictionnaire JSON décodé.
        """
        if not isinstance(raw, dict):
            return
        msg_type = raw.get("type")
        if not isinstance(msg_type, str):
            return

        data = raw.get("data") if isinstance(raw.get("data"), dict) else raw

        if msg_type == "ping":
            await self.send_personal(ws, "pong", {})
            return

        if msg_type == "overlay_hello":
            precedent = self._clients.get(ws)
            self._clients[ws] = "overlay"
            if precedent != "overlay":
                await self.broadcast("overlay_statut", {"connecte": self.overlay_connecte})
            return

        handler = self._handlers.get(msg_type) or self._persistent_handlers.get(msg_type)
        if handler is not None:
            await _invoquer_handler(handler, ws, data)
        else:
            logger.debug("Message WebSocket client sans handler : %s", msg_type)


hub = Hub()


def on_client_message(msg_type: str, handler: WSHandler) -> None:
    """Enregistre sur le hub singleton un gestionnaire de message client WebSocket.

    Args:
        msg_type: Type du message client entrant (ex. ``"approbation_reponse"``).
        handler: Fonction ou coroutine appelée à la réception du message.
    """
    hub.on_client_message(msg_type, handler)

