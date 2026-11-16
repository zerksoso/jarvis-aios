"""Client WebSocket et HTTP asynchrone de l'overlay vers le backend Jarvis."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from PySide6.QtCore import QObject, Signal
from websockets.asyncio.client import connect as ws_connect

from overlay import obtenir_racine

logger = logging.getLogger(__name__)

BASE_HTTP_URL = "http://127.0.0.1:5230"
BASE_WS_URL = "ws://127.0.0.1:5230/ws"
RECONNEXION_DELAI_S = 3.0


def lire_jeton(token_path: Path | None = None) -> str:
    """Relit le jeton d'authentification local depuis ``runtime/token.txt``.

    Args:
        token_path: Chemin optionnel vers le fichier jeton.

    Returns:
        Jeton nettoyé ou chaîne vide si le fichier est absent ou illisible.
    """
    chemin = token_path or (obtenir_racine() / "runtime" / "token.txt")
    try:
        if chemin.is_file():
            return chemin.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("Lecture du jeton impossible (%s).", exc)
    return ""


class JarvisClient(QObject):
    """Pont thread-safe WebSocket + HTTP entre le backend FastAPI et les widgets PySide6.

    Exécute la connexion WebSocket dans un thread asyncio dédié et les requêtes ``httpx``
    dans des threads d'arrière-plan, en renvoyant tous les résultats sur le thread Qt
    principal via des signaux ``Signal``.
    """

    connecte_change = Signal(bool)
    message_recu = Signal(dict)
    _http_termine = Signal(object, object, object, object)

    def __init__(
        self,
        token_path: Path | None = None,
        base_http_url: str = BASE_HTTP_URL,
        base_ws_url: str = BASE_WS_URL,
        parent: QObject | None = None,
    ) -> None:
        """Initialise le client sans démarrer encore les threads réseau."""
        super().__init__(parent)
        self._token_path = token_path or (obtenir_racine() / "runtime" / "token.txt")
        self._base_http_url = base_http_url.rstrip("/")
        self._base_ws_url = base_ws_url.rstrip("/")

        self._stop_event = threading.Event()
        self._thread_ws: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: asyncio.Queue[str] | None = None
        self._stop_async: asyncio.Event | None = None
        self._ws_courant: Any | None = None
        self._connecte = False

        self._http_termine.connect(self._dispatcher_http)

    @property
    def est_connecte(self) -> bool:
        """Indique si la liaison WebSocket est actuellement active."""
        return self._connecte

    def demarrer(self) -> None:
        """Démarre le thread asyncio de connexion WebSocket et reconnexion automatique."""
        if self._thread_ws is not None and self._thread_ws.is_alive():
            return
        self._stop_event.clear()
        self._thread_ws = threading.Thread(
            target=self._entree_thread_ws,
            name="jarvis-overlay-ws",
            daemon=True,
        )
        self._thread_ws.start()

    def arreter(self) -> None:
        """Demande l'arrêt propre de la boucle WebSocket et attend brièvement la fin du thread."""
        self._stop_event.set()
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._signaler_arret_async)
        if self._thread_ws is not None and self._thread_ws.is_alive():
            self._thread_ws.join(timeout=2.0)

    def _signaler_arret_async(self) -> None:
        """Réveille la boucle asyncio pour fermer la socket et sortir immédiatement."""
        if self._stop_async is not None:
            self._stop_async.set()
        if self._ws_courant is not None:
            asyncio.create_task(self._fermer_socket_silencieux(self._ws_courant))

    @staticmethod
    async def _fermer_socket_silencieux(ws: Any) -> None:
        """Ferme proprement une connexion WebSocket sans propager d'erreur."""
        with contextlib.suppress(Exception):
            await ws.close()

    def envoyer_ws(self, msg_type: str, data: dict[str, Any] | None = None) -> bool:
        """Envoie un événement JSON au serveur via la boucle WebSocket de manière thread-safe.

        Args:
            msg_type: Type de message client → serveur (ex. ``"carte_epingler"``).
            data: Dictionnaire de données associé.

        Returns:
            ``True`` si le message a été mis en file d'envoi, ``False`` si non connecté.
        """
        loop = self._loop
        outbox = self._outbox
        if not self._connecte or loop is None or loop.is_closed() or outbox is None:
            return False

        trame = json.dumps(
            {"type": msg_type, "data": data if data is not None else {}},
            ensure_ascii=False,
        )
        try:
            loop.call_soon_threadsafe(outbox.put_nowait, trame)
            return True
        except RuntimeError:
            return False

    def _entree_thread_ws(self) -> None:
        """Point d'entrée du thread exécutant la boucle asyncio WebSocket."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._boucle_ws())
        finally:
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None
            self._outbox = None
            self._stop_async = None
            self._ws_courant = None

    async def _attendre_reconnexion(self, stop_async: asyncio.Event) -> None:
        """Attend ``RECONNEXION_DELAI_S`` secondes sauf demande d'arrêt anticipé."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_async.wait(), timeout=RECONNEXION_DELAI_S)

    def _notifier_connexion(self, etat: bool) -> None:
        """Met à jour l'état de connexion et émet le signal Qt uniquement sur changement."""
        if self._connecte != etat:
            self._connecte = etat
            self.connecte_change.emit(etat)

    async def _boucle_ws(self) -> None:
        """Maintient la connexion WebSocket avec reconnexion toutes les 3 s."""
        self._stop_async = asyncio.Event()
        if self._stop_event.is_set():
            self._stop_async.set()

        while not self._stop_event.is_set():
            jeton = lire_jeton(self._token_path)
            if not jeton:
                self._notifier_connexion(False)
                await self._attendre_reconnexion(self._stop_async)
                continue

            url = f"{self._base_ws_url}?token={quote(jeton)}&client=overlay"
            outbox: asyncio.Queue[str] = asyncio.Queue()
            self._outbox = outbox

            try:
                async with ws_connect(
                    url,
                    open_timeout=5.0,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                ) as ws:
                    self._ws_courant = ws
                    await ws.send(json.dumps({"type": "overlay_hello", "data": {}}))
                    self._notifier_connexion(True)
                    logger.info("Overlay connecté au backend WebSocket.")

                    lecteur = asyncio.create_task(self._lire_messages(ws))
                    ecrivain = asyncio.create_task(self._ecrire_messages(ws, outbox))
                    arret_task = asyncio.create_task(self._stop_async.wait())

                    _, en_cours = await asyncio.wait(
                        {lecteur, ecrivain, arret_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for tache in en_cours:
                        tache.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await tache
            except Exception as exc:
                logger.debug("Connexion WebSocket interrompue ou impossible (%s).", exc)
            finally:
                self._ws_courant = None
                self._outbox = None
                self._notifier_connexion(False)

            if not self._stop_event.is_set():
                await self._attendre_reconnexion(self._stop_async)

    async def _lire_messages(self, ws: Any) -> None:
        """Lit les trames JSON entrantes et les transmet au thread Qt via ``message_recu``."""
        async for brut in ws:
            if not isinstance(brut, str):
                continue
            try:
                env = json.loads(brut)
            except json.JSONDecodeError:
                continue
            if isinstance(env, dict):
                self.message_recu.emit(env)

    @staticmethod
    async def _ecrire_messages(ws: Any, outbox: asyncio.Queue[str]) -> None:
        """Vide la file d'envoi sortante sur le WebSocket actif."""
        while True:
            trame = await outbox.get()
            await ws.send(trame)

    def requete_http_async(
        self,
        methode: str,
        chemin: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 15.0,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Exécute un appel HTTP REST ``httpx`` dans un thread sans bloquer l'interface.

        Ajoute automatiquement l'en-tête ``X-Jarvis-Token`` relu depuis ``runtime/token.txt``.

        Args:
            methode: Verbe HTTP (``GET``, ``POST``, ``PUT``, etc.).
            chemin: Chemin relatif (ex. ``"/api/voix/ecouter"``).
            payload: Corps JSON optionnel.
            timeout: Délai maximal d'attente en secondes.
            on_success: Callback exécuté sur le thread Qt avec le JSON décodé.
            on_error: Callback exécuté sur le thread Qt en cas d'erreur réseau/HTTP.
        """
        def _worker() -> None:
            entetes: dict[str, str] = {}
            jeton = lire_jeton(self._token_path)
            if jeton:
                entetes["X-Jarvis-Token"] = jeton

            url = chemin if chemin.startswith("http") else f"{self._base_http_url}{chemin}"
            try:
                with httpx.Client(timeout=timeout) as http:
                    resp = http.request(methode.upper(), url, json=payload, headers=entetes)
                if resp.status_code >= 400:
                    detail = f"Erreur HTTP {resp.status_code}"
                    with contextlib.suppress(Exception):
                        data_err = resp.json()
                        if isinstance(data_err, dict) and data_err.get("detail"):
                            detail = str(data_err["detail"])
                    raise RuntimeError(detail)
                donnees = resp.json() if resp.content else None
                self._http_termine.emit(on_success, on_error, donnees, None)
            except Exception as exc:
                self._http_termine.emit(on_success, on_error, None, exc)

        thread = threading.Thread(target=_worker, name="jarvis-overlay-http", daemon=True)
        thread.start()

    def get_async(
        self,
        chemin: str,
        timeout: float = 10.0,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Exécute un ``GET`` asynchrone non bloquant vers le backend."""
        self.requete_http_async(
            "GET",
            chemin,
            payload=None,
            timeout=timeout,
            on_success=on_success,
            on_error=on_error,
        )

    def post_async(
        self,
        chemin: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Exécute un ``POST`` asynchrone non bloquant vers le backend."""
        self.requete_http_async(
            "POST",
            chemin,
            payload=payload,
            timeout=timeout,
            on_success=on_success,
            on_error=on_error,
        )

    @staticmethod
    def _dispatcher_http(
        on_success: Callable[[Any], None] | None,
        on_error: Callable[[Exception], None] | None,
        resultat: Any,
        erreur: Exception | None,
    ) -> None:
        """Relaye le résultat HTTP sur le thread Qt principal."""
        if erreur is not None:
            if on_error is not None:
                on_error(erreur)
        elif on_success is not None:
            on_success(resultat)
