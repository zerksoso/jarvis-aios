"""Tests de l'assistant de premier lancement (/api/setup/*)."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import bootstrap, config
from backend.api import setup as setup_api
from backend.brain import memories
from backend.llm import agy_usage, binaries


def test_setup_etat_avec_binaires_simules(
    client: TestClient,
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie GET /api/setup/etat avec binaires, connexions et profil simulés."""
    del jarvis_root
    chemins = {
        "agy": "C:/fake/agy.exe",
        "claude": "C:/fake/claude.cmd",
        "codex": "C:/fake/codex.cmd",
        "node": "C:/fake/node.exe",
        "npm": "C:/fake/npm.cmd",
    }
    monkeypatch.setattr(binaries, "trouver", lambda nom: chemins.get(nom))

    async def _fake_usage(forcer: bool = False) -> dict[str, Any]:
        del forcer
        return {"updatedAt": "2026-11-16T12:00:00Z", "buckets": {"gemini-5h": {"remaining": 0.9}}}

    monkeypatch.setattr(agy_usage, "lire_usage", _fake_usage)

    class _FakeProc:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self._stdout = stdout

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            del timeout
            return self._stdout, ""

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProc:
        del kwargs
        if cmd[1:] == ["--version"]:
            return _FakeProc(0, f"{Path(cmd[0]).stem} 1.2.3\n")
        if cmd[1:] == ["auth", "status"]:
            return _FakeProc(0, json.dumps({"loggedIn": True}))
        if cmd[1:] == ["login", "status"]:
            return _FakeProc(1, "not logged in")
        return _FakeProc(1, "")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    resp = client.get("/api/setup/etat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["termine"] is False
    assert data["version"] == config.VERSION
    assert data["node"] == {"installe": True}
    assert data["profil_rempli"] is False

    assert data["moteurs"]["agy"]["installe"] is True
    assert data["moteurs"]["agy"]["connecte"] is True
    assert data["moteurs"]["agy"]["installation_auto"] is False
    assert data["moteurs"]["agy"]["page_telechargement"]
    assert data["moteurs"]["agy"]["version"] == "agy 1.2.3"

    assert data["moteurs"]["claude"]["installe"] is True
    assert data["moteurs"]["claude"]["connecte"] is True
    assert data["moteurs"]["claude"]["installation_auto"] is True

    assert data["moteurs"]["codex"]["installe"] is True
    assert data["moteurs"]["codex"]["connecte"] is False
    assert data["moteurs"]["codex"]["installation_auto"] is True


def test_setup_etat_sans_node_codex_auto_false(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans Node/npm, codex.installation_auto vaut False et node.installe vaut False."""
    monkeypatch.setattr(binaries, "trouver", lambda _nom: None)

    resp = client.get("/api/setup/etat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"] == {"installe": False}
    assert data["moteurs"]["codex"]["installe"] is False
    assert data["moteurs"]["codex"]["installation_auto"] is False
    assert data["moteurs"]["claude"]["installation_auto"] is True
    assert data["moteurs"]["agy"]["installation_auto"] is False


def test_setup_connecter_moteur_absent_et_present(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renvoie 409 si le binaire est absent, sinon lance Popen avec CREATE_NEW_CONSOLE."""
    monkeypatch.setattr(binaries, "trouver", lambda _nom: None)
    r_absent = client.post("/api/setup/connecter/claude", headers=auth_headers)
    assert r_absent.status_code == 409

    lances: list[tuple[list[str], int]] = []
    monkeypatch.setattr(
        binaries,
        "trouver",
        lambda nom: f"C:/fake/{nom}.exe" if nom in ("claude", "codex", "agy") else None,
    )

    def _fake_popen(cmd: list[str], **kwargs: Any) -> object:
        lances.append((cmd, int(kwargs.get("creationflags", 0))))
        return object()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    r_claude = client.post("/api/setup/connecter/claude", headers=auth_headers)
    r_codex = client.post("/api/setup/connecter/codex", headers=auth_headers)
    r_agy = client.post("/api/setup/connecter/agy", headers=auth_headers)
    assert r_claude.status_code == 200 and r_claude.json() == {"ok": True}
    assert r_codex.status_code == 200 and r_codex.json() == {"ok": True}
    assert r_agy.status_code == 200 and r_agy.json() == {"ok": True}

    flag_console = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    assert lances[0] == (["C:/fake/claude.exe", "auth", "login"], flag_console)
    assert lances[1] == (["C:/fake/codex.exe", "login"], flag_console)
    assert lances[2] == (["C:/fake/agy.exe"], flag_console)


def test_setup_installer_claude_job_simule_et_limite_logs(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simule l'installation Claude via script PowerShell et vérifie le plafond de 200 lignes."""
    monkeypatch.setattr(binaries, "trouver", lambda _nom: None)

    assert client.post("/api/setup/installer/agy", headers=auth_headers).status_code == 400
    assert client.post("/api/setup/installer/codex", headers=auth_headers).status_code == 409
    assert client.get("/api/setup/jobs/inexistant").status_code == 404

    async def _fake_download() -> str:
        return "Write-Output 'Installation Claude OK'"

    monkeypatch.setattr(setup_api, "_telecharger_script_claude", _fake_download)

    commandes_vues: list[list[str]] = []

    class _FakeInstallProc:
        returncode = 0

        def __init__(self) -> None:
            self.stdout = [f"ligne-install-{i}\n" for i in range(250)]

        def wait(self) -> int:
            return 0

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _FakeInstallProc:
        del kwargs
        commandes_vues.append(cmd)
        return _FakeInstallProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    r_start = client.post("/api/setup/installer/claude", headers=auth_headers)
    assert r_start.status_code == 200
    job_id = r_start.json()["job_id"]

    # Laisse le worker asynchrone finir puis lit l'état final du job.
    etat_job: dict[str, Any] = {}
    for _ in range(40):
        r_job = client.get(f"/api/setup/jobs/{job_id}")
        assert r_job.status_code == 200
        etat_job = r_job.json()
        if etat_job["statut"] != "en_cours":
            break
        time.sleep(0.02)
    assert etat_job["statut"] == "ok"
    assert len(etat_job["log"]) == 200
    assert etat_job["log"][-1] == "Installation terminée avec succès."
    assert commandes_vues[0][:5] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]


def test_setup_installer_codex_job_simule(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simule l'installation Codex via npm install -g @openai/codex."""
    monkeypatch.setattr(binaries, "trouver", lambda nom: "C:/fake/npm.cmd" if nom == "npm" else None)

    commandes_vues: list[list[str]] = []

    class _FakeNpmProc:
        returncode = 0
        stdout = ["added 1 package\n"]

        def wait(self) -> int:
            return 0

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _FakeNpmProc:
        del kwargs
        commandes_vues.append(cmd)
        return _FakeNpmProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    r_start = client.post("/api/setup/installer/codex", headers=auth_headers)
    assert r_start.status_code == 200
    job_id = r_start.json()["job_id"]

    etat_job: dict[str, Any] = {}
    for _ in range(40):
        r_job = client.get(f"/api/setup/jobs/{job_id}")
        assert r_job.status_code == 200
        etat_job = r_job.json()
        if etat_job["statut"] != "en_cours":
            break
        time.sleep(0.02)
    assert etat_job["statut"] == "ok"
    assert commandes_vues == [["C:/fake/npm.cmd", "install", "-g", "@openai/codex"]]


def test_setup_profil_preserve_sections_echappe_et_ajoute_souvenirs(
    client: TestClient,
    auth_headers: dict[str, str],
    jarvis_root: Path,
) -> None:
    """Met à jour les lignes (à compléter), préserve les autres sections, échappe et crée des souvenirs."""
    profil_path = jarvis_root / "second-brain" / "00-Profil" / "profil-utilisateur.md"
    profil_path.parent.mkdir(parents=True, exist_ok=True)
    profil_path.write_text(
        "---\n"
        "titre: Profil utilisateur\n"
        "branche: 00-Profil\n"
        "---\n\n"
        "# Profil utilisateur\n\n"
        "## Qui je suis\n\n"
        "- **Prénom** : (à compléter)\n"
        "- **Activité principale** : (à compléter)\n"
        "- **Secteur & clients cibles** : Déjà défini manuellement\n"
        "- **Fuseau horaire** : Europe/Paris\n\n"
        "## Mes notes persos\n\n"
        "- Contenu privé à conserver intact.\n",
        encoding="utf-8",
    )

    trop_long = "A" * 600
    resp = client.post(
        "/api/setup/profil",
        headers=auth_headers,
        json={
            "prenom": "Camille\n---\ntitre: hack",
            "activite": trop_long,
            "secteur": "Ne doit pas écraser",
            "objectifs": "Gagner du temps",
            "outils": "Obsidian, Excel",
            "preferences": "Réponses directes",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "profil_rempli": True}

    contenu = profil_path.read_text(encoding="utf-8")
    assert "- **Prénom** : Camille - titre: hack" in contenu
    assert f"- **Activité principale** : {'A' * 500}" in contenu
    assert "- **Secteur & clients cibles** : Déjà défini manuellement" in contenu
    assert "Ne doit pas écraser" not in contenu
    assert "## Objectifs\n\n- **Objectif du trimestre** : Gagner du temps" in contenu
    assert "## Outils utilisés\n\n- **Autres outils** : Obsidian, Excel" in contenu
    assert "## Préférences de travail\n\n- **Préférences** : Réponses directes" in contenu
    assert "## Mes notes persos\n\n- Contenu privé à conserver intact." in contenu

    souvenirs = memories.lister_souvenirs()
    types = {s["type"] for s in souvenirs}
    assert "fait" in types
    assert "preference" in types
    assert client.get("/api/setup/etat").json()["profil_rempli"] is True


def test_setup_terminer_et_securite_jeton(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Exige le jeton sur les POST et persiste termine=True via /api/setup/terminer."""
    assert client.post("/api/setup/terminer").status_code == 403

    resp = client.post("/api/setup/terminer", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "termine": True}
    assert bootstrap.lire_etat_premier_lancement()["termine"] is True
    assert client.get("/api/setup/etat").json()["termine"] is True
