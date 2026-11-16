"""Tests unitaires du moteur Codex CLI, du routage chatgpt et du code aucun_moteur."""

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.llm import binaries, codex_engine, quota, router
from backend.llm.base import DeltaCallback, Engine, EngineError, LLMRequest, LLMResult
from backend.llm.codex_engine import CodexEngine, parse_ligne


def test_parse_ligne_jsonl_codex() -> None:
    """Parse correctement les événements JSONL Codex (thread, delta, item.completed, usage)."""
    flux = [
        "",
        "Texte hors JSON",
        '{"type":"thread.started","thread_id":"thr-123"}',
        '{"type":"item.agent_message.delta","delta":"Réflexion "}',
        '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"Brouillon"}}',
        '{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"Réponse finale Codex"}}',
        '{"type":"turn.completed","usage":{"input_tokens":120,"output_tokens":35}}',
    ]
    evts = [evt for ligne in flux if (evt := parse_ligne(ligne)) is not None]
    assert evts == [
        {"type": "init", "session_id": "thr-123"},
        {"type": "delta", "delta": "Réflexion "},
        {"type": "agent_message", "text": "Brouillon"},
        {"type": "agent_message", "text": "Réponse finale Codex"},
        {"type": "usage", "input_tokens": 120, "output_tokens": 35},
    ]


async def test_codex_disponible_verifie_binaire_et_login_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """disponible() exige codex installé ET `codex login status` avec code retour 0."""
    moteur = CodexEngine()

    monkeypatch.setattr(binaries, "trouver", lambda _nom: None)
    ok_abs, raison_abs = await moteur.disponible()
    assert ok_abs is False
    assert "non installé" in raison_abs.lower()

    monkeypatch.setattr(binaries, "trouver", lambda nom: "C:/fake/codex.exe" if nom == "codex" else None)

    class _FakeLoginProc:
        def __init__(self, rc: int) -> None:
            self.returncode = rc

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            del timeout
            return ("Logged in" if self.returncode == 0 else "", "Not logged in" if self.returncode else "")

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: _FakeLoginProc(1))
    ok_deconn, raison_deconn = await moteur.disponible()
    assert ok_deconn is False
    assert "non connecté" in raison_deconn.lower()

    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_kw: _FakeLoginProc(0))
    ok_conn, raison_conn = await moteur.disponible()
    assert ok_conn is True
    assert raison_conn == "OK"


async def test_codex_generer_retourne_dernier_message_agent(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exécute `codex exec --json` simulé et extrait le dernier message agent."""
    del jarvis_root
    monkeypatch.setattr(binaries, "trouver", lambda nom: "C:/fake/codex.exe" if nom == "codex" else None)

    jsonl_sortie = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thr-99"}',
            '{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":"Étape intermédiaire"}}',
            '{"type":"item.completed","item":{"id":"m2","type":"agent_message","text":"Réponse finale vérifiée"}}',
            '{"type":"turn.completed","usage":{"input_tokens":80,"output_tokens":19}}',
        ]
    ) + "\n"

    commandes_vues: list[list[str]] = []

    class _FakeLoginProc:
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            del timeout
            return "Logged in", ""

    class _FakeExecProc:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = io.StringIO(jsonl_sortie)
            self.stderr = io.StringIO("")

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

        def kill(self) -> None:
            pass

    def _fake_popen(cmd: list[str], **_kwargs: Any) -> Any:
        commandes_vues.append(cmd)
        if cmd[1:3] == ["login", "status"]:
            return _FakeLoginProc()
        return _FakeExecProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    deltas: list[str] = []

    async def _on_delta(d: str) -> None:
        deltas.append(d)

    res = await codex_engine.CodexEngine().generer(
        LLMRequest(prompt="Dis bonjour", systeme="Sois concis", moteur="chatgpt"),
        modele_id="codex",
        session_id=None,
        on_delta=_on_delta,
    )

    assert ["C:/fake/codex.exe", "login", "status"] in commandes_vues
    assert any(c[:3] == ["C:/fake/codex.exe", "exec", "--json"] for c in commandes_vues)
    assert res.texte == "Réponse finale vérifiée"
    assert res.moteur == "codex"
    assert res.input_tokens == 80
    assert res.output_tokens == 19
    assert deltas == ["Réponse finale vérifiée"]


class _MoteurFactice:
    """Moteur simulé minimal pour tester le routage chatgpt et aucun_moteur."""

    def __init__(self, nom: str, ok: bool, texte: str = "ok") -> None:
        self.nom = nom
        self._ok = ok
        self._texte = texte

    async def disponible(self) -> tuple[bool, str]:
        return (True, "OK") if self._ok else (False, f"{self.nom} indisponible")

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, session_id, on_delta
        return LLMResult(texte=self._texte, moteur=self.nom, modele=modele_id)


async def test_router_chatgpt_prefere_codex_sinon_gpt_oss(jarvis_root: Path) -> None:
    """Le moteur chatgpt utilise Codex si disponible, sinon GPT-OSS via agy."""
    del jarvis_root
    quota.set_quotas_for_tests(
        {"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.9}, "3p-5h": {"group": "3p", "remaining": 0.9}}}
    )

    engines_codex_ok: dict[str, Engine] = {
        "codex": _MoteurFactice("codex", True, "Depuis Codex"),
        "agy": _MoteurFactice("agy", True, "Depuis GPT-OSS"),
    }
    res_codex = await router.generer(LLMRequest(prompt="Salut", moteur="chatgpt"), engines=engines_codex_ok)
    assert res_codex.moteur == "codex"
    assert res_codex.modele == "codex"
    assert res_codex.texte == "Depuis Codex"

    engines_codex_ko: dict[str, Engine] = {
        "codex": _MoteurFactice("codex", False),
        "agy": _MoteurFactice("agy", True, "Depuis GPT-OSS"),
    }
    res_repli = await router.generer(LLMRequest(prompt="Salut", moteur="chatgpt"), engines=engines_codex_ko)
    assert res_repli.moteur == "agy"
    assert res_repli.modele == "gpt-oss"
    assert res_repli.texte == "Depuis GPT-OSS"


async def test_router_aucun_moteur_leve_code(jarvis_root: Path) -> None:
    """Si aucun moteur n'est utilisable, router.generer lève EngineError(code='aucun_moteur')."""
    del jarvis_root
    quota.set_quotas_for_tests({})
    aucun: dict[str, Engine] = {
        "agy": _MoteurFactice("agy", False),
        "claude_cli": _MoteurFactice("claude_cli", False),
        "codex": _MoteurFactice("codex", False),
    }

    with pytest.raises(EngineError) as exc_info:
        await router.generer(LLMRequest(prompt="Test"), engines=aucun)
    assert exc_info.value.code == "aucun_moteur"


def test_ws_chat_erreur_remonte_code_aucun_moteur(
    client: TestClient,
    token: str,
    auth_headers: dict[str, str],
) -> None:
    """chat_erreur WS inclut le champ code='aucun_moteur' quand aucun moteur n'est disponible."""
    quota.set_quotas_for_tests({})
    aucun: dict[str, Engine] = {
        "agy": _MoteurFactice("agy", False),
        "claude_cli": _MoteurFactice("claude_cli", False),
        "codex": _MoteurFactice("codex", False),
    }
    router.set_engines_for_tests(aucun)
    evenements: list[dict[str, Any]] = []
    with client.websocket_connect(f"/ws?token={token}&client=ui") as ws:
        resp = client.post(
            "/api/chat",
            headers=auth_headers,
            json={"cible": "agent", "id": "jarvis", "message": "Bonjour"},
        )
        assert resp.status_code == 200
        for _ in range(12):
            evt = ws.receive_json()
            evenements.append(evt)
            if evt["type"] == "etat_jarvis" and evt["data"].get("etat") == "repos":
                break

    erreurs = [e for e in evenements if e["type"] == "chat_erreur"]
    assert len(erreurs) == 1
    assert erreurs[0]["data"]["code"] == "aucun_moteur"
