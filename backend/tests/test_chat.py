"""Tests d'intégration de l'API REST chat, santé, moteurs, paramètres et streaming WS."""

from fastapi.testclient import TestClient

from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult


class _MoteurChatSimule:
    """Moteur simulé émettant plusieurs deltas ordonnés pour les tests d'intégration chat."""

    nom = "agy"

    async def disponible(self) -> tuple[bool, str]:
        """Toujours disponible en test."""
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Streame une réponse fixe en trois fragments."""
        del req, session_id
        fragments = ["Bonjour ", "utilisateur", " !"]
        for frag in fragments:
            if on_delta is not None:
                await on_delta(frag)
        return LLMResult(
            texte="Bonjour utilisateur !",
            moteur="agy",
            modele=modele_id,
            session_id="sess-chat-1",
            input_tokens=42,
            output_tokens=7,
        )


def test_post_chat_streaming_ws_et_historique(
    client: TestClient,
    token: str,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/chat diffuse chat_debut/delta/fin sur WS et persiste les messages en SQLite."""
    quota.set_quotas_for_tests(
        {
            "buckets": {
                "gemini-5h": {"group": "gemini", "remaining": 0.85},
                "3p-5h": {"group": "3p", "remaining": 0.85},
                "gemini-argon-wtu-weekly": {"group": "argon", "remaining": 0.85},
            }
        }
    )
    engines: dict[str, Engine] = {"agy": _MoteurChatSimule(), "claude_cli": _MoteurChatSimule()}
    router.set_engines_for_tests(engines)

    evenements: list[dict[str, object]] = []
    with client.websocket_connect(f"/ws?token={token}&client=ui") as ws:
        resp = client.post(
            "/api/chat",
            headers=auth_headers,
            json={"cible": "agent", "id": "jarvis", "message": "Salut Jarvis"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"conversation_id": "agent:jarvis"}

        for _ in range(15):
            evt = ws.receive_json()
            evenements.append(evt)
            if evt["type"] == "etat_jarvis" and evt["data"].get("etat") == "repos":
                break

    evenements_chat = [e for e in evenements if e["type"] != "agent_etat"]
    types = [e["type"] for e in evenements_chat]
    assert types == [
        "etat_jarvis",
        "chat_debut",
        "chat_delta",
        "chat_delta",
        "chat_delta",
        "chat_fin",
        "etat_jarvis",
    ]
    assert evenements_chat[0]["data"] == {"etat": "reflexion"}
    assert evenements_chat[1]["data"] == {"conversation_id": "agent:jarvis", "auteur": "jarvis"}
    deltas = [e["data"]["delta"] for e in evenements_chat if e["type"] == "chat_delta"]  # type: ignore[index]
    assert deltas == ["Bonjour ", "utilisateur", " !"]

    fin = evenements_chat[5]["data"]
    assert isinstance(fin, dict)
    assert fin["contenu"] == "Bonjour utilisateur !"
    assert fin["moteur"] == "agy"
    assert fin["modele"] == "hydrogen"
    assert isinstance(fin["message_id"], int)

    hist = client.get("/api/conversations/agent/jarvis/messages?limite=100")
    assert hist.status_code == 200
    payload = hist.json()
    assert payload["conversation_id"] == "agent:jarvis"
    msgs = payload["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["contenu"] == "Salut Jarvis"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["auteur"] == "jarvis"
    assert msgs[1]["contenu"] == "Bonjour utilisateur !"
    assert msgs[1]["meta"]["moteur"] == "agy"
    assert msgs[1]["meta"]["modele"] == "hydrogen"


def test_sante_moteurs_quotas_et_parametres(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vérifie GET /api/sante, /api/moteurs, /api/quotas et GET/PUT /api/parametres."""
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.75}}})
    router.set_engines_for_tests(
        {"agy": _MoteurChatSimule(), "claude_cli": _MoteurChatSimule()}
    )

    sante = client.get("/api/sante")
    assert sante.status_code == 200
    s_json = sante.json()
    assert s_json["ok"] is True
    assert s_json["version"] == __import__("backend").__version__
    assert s_json["overlay_connecte"] is False
    assert s_json["moteurs"]["agy"] is True
    assert s_json["moteurs"]["claude_cli"]["ok"] is True

    moteurs = client.get("/api/moteurs")
    assert moteurs.status_code == 200
    m_json = moteurs.json()
    ids = [m["id"] for m in m_json["catalogue"]]
    assert "hydrogen" in ids
    assert "cli-sonnet" in ids

    quotas_resp = client.get("/api/quotas")
    assert quotas_resp.status_code == 200
    q_json = quotas_resp.json()
    assert q_json["buckets"]["gemini-5h"]["remaining"] == 0.75
    assert "claude_cli" in q_json

    p_init = client.get("/api/parametres")
    assert p_init.status_code == 200
    assert p_init.json()["voix"] == {"active": True, "nom": "fr-FR-DeniseNeural"}

    p_maj = client.put(
        "/api/parametres",
        headers=auth_headers,
        json={"effort": "high", "voix": {"active": False}},
    )
    assert p_maj.status_code == 200
    data_maj = p_maj.json()
    assert data_maj["effort"] == "high"
    # La fusion profonde conserve voix.nom intact.
    assert data_maj["voix"] == {"active": False, "nom": "fr-FR-DeniseNeural"}
