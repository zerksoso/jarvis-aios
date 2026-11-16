"""Point d'entrée du serveur FastAPI Jarvis, routage dynamique, WebSocket et frontend."""

import asyncio
import importlib
import json
import logging
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from backend import __version__, bootstrap, config, db, security, settings_store
from backend.api import chat, moteurs, sante
from backend.lifecycle import executer_hooks, on_shutdown_hooks, on_startup_hooks
from backend.llm import quota
from backend.logging_setup import setup_logging, shutdown_logging
from backend.security import SecuriteMiddleware
from backend.update import service as maj_service
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

EXTRA_ROUTERS: list[str] = [
    "cerveau",
    "memoires",
    "skills",
    "routines",
    "taches",
    "agents",
    "canaux",
    "approbations",
    "jarvis",
    "voix",
    "cartes",
    "stats",
    "parametres",
    "setup",
    "maj",
]

_HTML_NON_COMPILE = (
    "<!doctype html><html lang='fr'><head><meta charset='utf-8'>"
    "<title>Jarvis AIOS</title></head><body>"
    "<h1>Frontend non compilé</h1>"
    "<p>Aucun build trouvé dans <code>frontend/dist</code>.</p>"
    "</body></html>"
)


def register_routers(app: FastAPI) -> None:
    """Monte les routeurs API principaux et découvre dynamiquement les lots optionnels.

    Args:
        app: Instance ``FastAPI`` cible.

    Effets de bord:
        Importe les modules ``backend.api.<domaine>`` présents et inclut leur ``router``
        sous le préfixe ``/api``.
    """
    app.include_router(sante.router, prefix="/api")
    app.include_router(moteurs.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")

    for domaine in EXTRA_ROUTERS:
        nom_module = f"backend.api.{domaine}"
        try:
            module = importlib.import_module(nom_module)
        except ModuleNotFoundError as exc:
            if exc.name == nom_module:
                logger.debug("Routeur optionnel absent : %s", nom_module)
                continue
            logger.warning("Dépendance manquante dans le routeur %s : %s", nom_module, exc)
            continue
        except ImportError as exc:
            logger.warning("Échec d'import du routeur %s : %s", nom_module, exc)
            continue

        routeur = getattr(module, "router", None)
        if routeur is not None:
            app.include_router(routeur, prefix="/api")


async def _boucle_quotas_periodique(intervalle_s: float = 600.0) -> None:
    """Rafraîchit et diffuse les quotas sur le hub WebSocket toutes les 10 minutes."""
    while True:
        await asyncio.sleep(intervalle_s)
        try:
            donnees = await quota.lire_quotas(forcer=True)
            if donnees:
                await hub.broadcast("quotas", donnees)
        except Exception as exc:
            logger.warning("Échec du rafraîchissement périodique des quotas : %s", exc)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Cycle de vie FastAPI : dossiers, bootstrap données, logs, base SQLite, jeton, hooks et quotas."""
    del app
    bootstrap.ensure_data_dir()
    setup_logging()
    db.init_db()
    settings_store.lire()
    security.obtenir_jeton()
    hub.set_loop(asyncio.get_running_loop())
    hub.register_handler("chat", chat.handle_ws_chat)

    await executer_hooks(on_startup_hooks)
    tache_quotas = asyncio.create_task(_boucle_quotas_periodique())
    tache_maj = asyncio.create_task(maj_service.boucle_verification_periodique())

    try:
        yield
    finally:
        tache_maj.cancel()
        with suppress(asyncio.CancelledError):
            await tache_maj
        tache_quotas.cancel()
        with suppress(asyncio.CancelledError):
            await tache_quotas
        await executer_hooks(on_shutdown_hooks)
        hub.set_loop(None)
        shutdown_logging()


def _servir_index_html(index_file: Path) -> HTMLResponse:
    """Lit ``index.html`` en y injectant le jeton à la place de ``__JARVIS_TOKEN__``."""
    contenu = index_file.read_text(encoding="utf-8")
    contenu = contenu.replace("__JARVIS_TOKEN__", security.obtenir_jeton())
    return HTMLResponse(content=contenu)


def create_app() -> FastAPI:
    """Construit et configure l'application FastAPI complète de Jarvis.

    Returns:
        Application ``FastAPI`` configurée avec sécurité, API, WebSocket et frontend.
    """
    app = FastAPI(title="Jarvis AIOS", version=__version__, lifespan=_lifespan)
    app.add_middleware(SecuriteMiddleware)
    register_routers(app)

    @app.websocket("/ws")
    async def websocket_endpoint(
        websocket: WebSocket,
        token: str | None = None,
        client: str = "ui",
    ) -> None:
        """Point d'entrée WebSocket sécurisé par jeton et origine."""
        origin = websocket.headers.get("origin")
        if origin and not security.origine_est_valide(origin):
            await websocket.close(code=1008, reason="Origine non autorisée")
            return
        if not security.verifier_jeton(token):
            await websocket.close(code=1008, reason="Jeton invalide ou manquant")
            return

        await hub.connect(websocket, client=client)
        try:
            while True:
                try:
                    raw = await websocket.receive_json()
                except (KeyError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(raw, dict):
                    await hub.handle_client_message(websocket, raw)
        except WebSocketDisconnect:
            pass
        finally:
            await hub.disconnect(websocket)

    @app.get("/", include_in_schema=False)
    @app.get("/{chemin:path}", include_in_schema=False)
    async def servir_frontend(chemin: str = "") -> Response:
        """Sert les fichiers statiques de ``frontend/dist`` avec fallback SPA sur ``index.html``."""
        if chemin == "api" or chemin.startswith("api/"):
            raise HTTPException(status_code=404, detail="Route API introuvable")

        dist_dir = config.FRONT_DIST
        index_file = dist_dir / "index.html"
        if not dist_dir.exists() or not index_file.is_file():
            return HTMLResponse(content=_HTML_NON_COMPILE)

        if chemin:
            dist_resolu = dist_dir.resolve()
            cible = (dist_dir / chemin).resolve()
            if cible.is_relative_to(dist_resolu) and cible.is_file():
                if cible == index_file.resolve():
                    return _servir_index_html(index_file)
                return FileResponse(cible)

        return _servir_index_html(index_file)

    return app


def _verifier_port_libre(host: str, port: int) -> None:
    """Vérifie que ``(host, port)`` est libre avant de démarrer Uvicorn."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError as exc:
            print(
                f"Erreur : le port {port} sur {host} est déjà utilisé ({exc}). "
                "Arrêtez l'instance existante avant de relancer Jarvis.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc


def main() -> None:
    """Démarre le serveur Uvicorn Jarvis sur ``127.0.0.1:5230``.

    Effets de bord:
        Vérifie la disponibilité du port puis bloque sur la boucle Uvicorn.
    """
    _verifier_port_libre(config.HOST, config.PORT)
    app = create_app()
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
