"""Tests de sécurité HTTP et WebSocket : Host, jeton, Origin et injection token frontend."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api import chat


async def _traiteur_noop(
    conv_id: str,
    cible: str,
    id: str,
    message: str,
    moteur: str,
    modele: str | None,
    effort: str,
) -> None:
    """Traiteur factice évitant tout appel moteur réel dans les tests de sécurité."""
    del conv_id, cible, id, message, moteur, modele, effort


def test_host_invalide_retourne_403(client: TestClient) -> None:
    """Un en-tête Host non local doit être bloqué avec un statut 403."""
    reponse = client.get("/api/sante", headers={"Host": "evil.example.com"})
    assert reponse.status_code == 403


def test_post_sans_jeton_retourne_403(client: TestClient) -> None:
    """Une requête POST sur /api/* sans X-Jarvis-Token doit être refusée (403)."""
    reponse = client.post(
        "/api/chat",
        json={"cible": "agent", "id": "jarvis", "message": "Bonjour"},
    )
    assert reponse.status_code == 403


def test_post_mauvais_jeton_retourne_403(client: TestClient) -> None:
    """Une requête POST avec un jeton incorrect doit être refusée (403)."""
    reponse = client.post(
        "/api/chat",
        headers={"X-Jarvis-Token": "jeton-invalide"},
        json={"cible": "agent", "id": "jarvis", "message": "Bonjour"},
    )
    assert reponse.status_code == 403


def test_post_avec_jeton_ok(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Une requête POST avec le jeton valide doit réussir."""
    chat.set_traiteur(_traiteur_noop)
    reponse = client.post(
        "/api/chat",
        headers=auth_headers,
        json={"cible": "agent", "id": "jarvis", "message": "Bonjour"},
    )
    assert reponse.status_code == 200
    assert reponse.json() == {"conversation_id": "agent:jarvis"}


def test_get_sensible_exige_jeton(client: TestClient, auth_headers: dict[str, str], token: str) -> None:
    """Les GET sensibles (/api/cerveau/note, /api/ecran/*) exigent le jeton."""
    assert client.get("/api/cerveau/note").status_code == 403
    assert client.get("/api/ecran/capture").status_code == 403
    assert client.get("/api/ecran/image/inexistant.png").status_code == 403
    assert client.get("/api/ecran/image/inexistant.png?token=mauvais").status_code == 403

    # Avec jeton valide (en-tête ou ?token= sur GET sensible), le middleware laisse passer.
    assert client.get("/api/cerveau/note", headers=auth_headers).status_code == 404
    assert client.get("/api/ecran/capture", headers=auth_headers).status_code in (404, 405)
    assert client.get("/api/ecran/image/inexistant.png", headers=auth_headers).status_code == 404
    assert client.get(f"/api/ecran/image/inexistant.png?token={token}").status_code == 404


def test_origin_etranger_retourne_403(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Une origine tierce est refusée (403) même avec un jeton valide."""
    chat.set_traiteur(_traiteur_noop)
    reponse = client.post(
        "/api/chat",
        headers={**auth_headers, "Origin": "https://attacker.example"},
        json={"cible": "agent", "id": "jarvis", "message": "Bonjour"},
    )
    assert reponse.status_code == 403


def test_origin_vite_autorise_ok(client: TestClient, auth_headers: dict[str, str]) -> None:
    """L'origine locale du serveur de dev Vite (5231) est acceptée."""
    reponse = client.put(
        "/api/parametres",
        headers={**auth_headers, "Origin": "http://localhost:5231"},
        json={"verbosite": "concis"},
    )
    assert reponse.status_code == 200
    assert reponse.json()["verbosite"] == "concis"


def test_ws_sans_jeton_refuse(client: TestClient) -> None:
    """Une connexion WebSocket sans jeton valide doit être refusée."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=faux"):
            pass


def test_ws_origin_etranger_refuse(client: TestClient, token: str) -> None:
    """Une connexion WebSocket depuis une origine étrangère est refusée."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws?token={token}&client=ui",
            headers={"Origin": "https://evil.example"},
        ):
            pass


def test_ws_avec_jeton_ping_pong(client: TestClient, token: str) -> None:
    """Un client WS authentifié peut échanger ping -> pong."""
    with client.websocket_connect(f"/ws?token={token}&client=ui") as ws:
        ws.send_json({"type": "ping"})
        reponse = ws.receive_json()
        assert reponse["type"] == "pong"
        assert reponse["data"] == {}
        assert "ts" in reponse


def test_frontend_non_compile_et_injection_jeton(
    client: TestClient,
    jarvis_root: Path,
    token: str,
) -> None:
    """Sans dist/ renvoie la page de secours ; avec dist/ injecte le jeton dans index.html."""
    sans_build = client.get("/")
    assert sans_build.status_code == 200
    assert "Frontend non compilé" in sans_build.text

    dist = jarvis_root / "frontend" / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        '<html><head><meta name="jarvis-token" content="__JARVIS_TOKEN__"></head><body>OK</body></html>',
        encoding="utf-8",
    )

    avec_build = client.get("/")
    assert avec_build.status_code == 200
    assert token in avec_build.text
    assert "__JARVIS_TOKEN__" not in avec_build.text

    fallback_spa = client.get("/cerveau/graphe")
    assert fallback_spa.status_code == 200
    assert token in fallback_spa.text
