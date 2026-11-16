"""Contrôles de sécurité locale : jeton, anti DNS-rebinding, origine et middleware ASGI."""

import json
import os
import secrets
import threading
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from backend import config

_token_lock = threading.Lock()

HOSTS_AUTORISES = {
    "127.0.0.1:5230",
    "localhost:5230",
}

HOSTS_TEST_AUTORISES = {
    "testserver",
    "testserver:80",
}

ORIGINES_AUTORISEES = {
    "http://127.0.0.1:5230",
    "http://localhost:5230",
    "http://127.0.0.1:5231",
    "http://localhost:5231",
}


def obtenir_jeton() -> str:
    """Lit le jeton d'authentification dans ``RUNTIME/token.txt`` ou le crée au 1er lancement.

    Returns:
        Jeton URL-safe de 32 octets encodés.

    Effets de bord:
        Crée ``RUNTIME/token.txt`` s'il est absent ou vide.
    """
    with _token_lock:
        chemin = config.TOKEN_PATH
        if chemin.exists():
            contenu = chemin.read_text(encoding="utf-8").strip()
            if contenu:
                return contenu

        chemin.parent.mkdir(parents=True, exist_ok=True)
        jeton = secrets.token_urlsafe(32)
        chemin.write_text(jeton, encoding="utf-8")
        return jeton


def verifier_jeton(jeton: str | None) -> bool:
    """Vérifie en temps constant si le jeton fourni correspond au jeton serveur.

    Args:
        jeton: Valeur reçue dans ``X-Jarvis-Token`` ou paramètre de requête WS.

    Returns:
        ``True`` si le jeton est non vide et égal au jeton local.
    """
    if not jeton:
        return False
    attendu = obtenir_jeton()
    return secrets.compare_digest(jeton, attendu)


def host_est_valide(host: str | None) -> bool:
    """Valide l'en-tête ``Host`` contre les hôtes locaux autorisés (anti DNS-rebinding).

    En mode test (``JARVIS_TEST=1``), ``testserver`` utilisé par ``TestClient`` est aussi accepté.

    Args:
        host: Valeur brute de l'en-tête ``Host``.

    Returns:
        ``True`` si l'hôte est explicitement autorisé.
    """
    if not host:
        return False
    normalise = host.strip().lower()
    if normalise in HOSTS_AUTORISES:
        return True
    if os.environ.get("JARVIS_TEST") == "1" and normalise in HOSTS_TEST_AUTORISES:
        return True
    return False


def origine_est_valide(origin: str | None) -> bool:
    """Vérifie l'en-tête ``Origin`` lorsqu'il est présent.

    Args:
        origin: Valeur brute de l'en-tête ``Origin``.

    Returns:
        ``True`` si l'origine appartient à la liste blanche locale (ports 5230 et 5231).
    """
    if not origin:
        return False
    return origin.strip().lower() in ORIGINES_AUTORISEES


def est_get_sensible(path: str) -> bool:
    """Indique si une route ``GET`` exige la présentation du jeton ``X-Jarvis-Token``.

    Args:
        path: Chemin HTTP normalisé de la requête.

    Returns:
        ``True`` pour les captures d'écran (``/api/ecran/*``) et notes (``/api/cerveau/note``).
    """
    return (
        path == "/api/ecran"
        or path.startswith("/api/ecran/")
        or path == "/api/cerveau/note"
        or path.startswith("/api/cerveau/note/")
    )


def _extraire_entetes(scope: Scope) -> dict[bytes, str]:
    """Extrait les en-têtes ASGI en dictionnaire clé minuscule -> valeur décodée."""
    resultat: dict[bytes, str] = {}
    for cle, valeur in scope.get("headers", ()):
        resultat[cle.lower()] = valeur.decode("latin-1").strip()
    return resultat


async def _envoyer_403(send: Send, detail: str, head_only: bool = False) -> None:
    """Envoie une réponse HTTP 403 JSON au format standard ``{"detail": ...}``."""
    corps = b"" if head_only else json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(corps)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": corps, "more_body": False})


class SecuriteMiddleware:
    """Middleware ASGI appliquant la vérification de ``Host``, ``Origin`` et ``X-Jarvis-Token``.

    Opère directement au niveau ASGI afin de protéger à la fois les requêtes HTTP et WebSocket.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialise le middleware autour de l'application ASGI cible.

        Args:
            app: Application ASGI suivante dans la chaîne.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Filtre les connexions HTTP et WebSocket selon les règles de sécurité locales.

        Args:
            scope: Dictionnaire de contexte ASGI.
            receive: Canal de réception ASGI.
            send: Canal d'envoi ASGI.
        """
        scope_type = scope.get("type")
        if scope_type not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        entetes = _extraire_entetes(scope)
        host = entetes.get(b"host", "")
        origin = entetes.get(b"origin", "")

        if scope_type == "websocket":
            if not host_est_valide(host):
                await send({"type": "websocket.close", "code": 1008, "reason": "Hôte non autorisé"})
                return
            if origin and not origine_est_valide(origin):
                await send({"type": "websocket.close", "code": 1008, "reason": "Origine non autorisée"})
                return
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        head_only = method == "HEAD"
        if not host_est_valide(host):
            await _envoyer_403(send, "Hôte non autorisé", head_only=head_only)
            return

        path = scope.get("path", "")
        est_api = path == "/api" or path.startswith("/api/")
        if est_api:
            exige_jeton = method not in ("GET", "HEAD", "OPTIONS") or est_get_sensible(path)
            if exige_jeton:
                if origin and not origine_est_valide(origin):
                    await _envoyer_403(send, "Origine non autorisée", head_only=head_only)
                    return
                jeton = entetes.get(b"x-jarvis-token", "")
                if not jeton and method in ("GET", "HEAD") and est_get_sensible(path):
                    qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                    valeurs_token = qs.get("token")
                    if valeurs_token:
                        jeton = valeurs_token[0]
                if not verifier_jeton(jeton):
                    await _envoyer_403(send, "Jeton X-Jarvis-Token manquant ou invalide", head_only=head_only)
                    return

        await self.app(scope, receive, send)
