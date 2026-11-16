"""Tests de l'installeur de mise à jour (zip-slip, Authenticode, mode dev et /api/maj/installer)."""

import hashlib
import subprocess
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.update import checker, downloader, installer
from backend.update.downloader import ErreurInstallation


@pytest.mark.parametrize(
    "nom_malveillant",
    [
        "../outside.txt",
        "backend/../../outside.txt",
        "/abs/outside.txt",
        "C:\\windows\\evil.txt",
    ],
)
def test_zip_slip_chemins_malveillants_refuses(tmp_path: Path, nom_malveillant: str) -> None:
    """Refuse toute entrée zip tentant d'écrire hors du dossier _maj_staging."""
    zip_path = tmp_path / "malveillant.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(nom_malveillant, "boom")

    staging = tmp_path / "_maj_staging"
    with pytest.raises(ErreurInstallation):
        installer.extraire_zip_securise(zip_path, staging)
    assert not staging.exists()
    assert not (tmp_path / "outside.txt").exists()


def test_zip_slip_lien_symbolique_refuse(tmp_path: Path) -> None:
    """Refuse les liens symboliques POSIX embarqués dans une archive zip."""
    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("lien")
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")

    staging = tmp_path / "_maj_staging"
    with pytest.raises(ErreurInstallation, match="Lien symbolique"):
        installer.extraire_zip_securise(zip_path, staging)
    assert not staging.exists()


def test_maj_legere_extrait_staging_lance_jarvis_exe_et_arrete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extrait l'archive dans _maj_staging, lance Jarvis.exe --appliquer-maj et arrête le serveur."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "Jarvis.exe").write_bytes(b"MZ")

    zip_path = tmp_path / "app.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("VERSION", "1.2.10")
        zf.writestr("backend/main.py", "# maj\n")

    appels_popen: list[tuple[list[str], dict[str, Any]]] = []
    arrets: list[bool] = []

    def _fake_popen(cmd: list[str], **kwargs: Any) -> object:
        appels_popen.append((cmd, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    staging = installer.appliquer_maj_legere(
        zip_path,
        app_dir=app_dir,
        arreter_serveur=lambda: arrets.append(True),
    )
    assert staging == app_dir / "_maj_staging"
    assert (staging / "VERSION").read_text(encoding="utf-8") == "1.2.10"
    assert (staging / "backend" / "main.py").read_text(encoding="utf-8") == "# maj\n"
    assert appels_popen[0][0] == [str(app_dir / "Jarvis.exe"), "--appliquer-maj"]
    assert arrets == [True]


def test_maj_complete_authenticode_et_options_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Accepte NotSigned/Valid, refuse une signature invalide et lance le Setup détaché."""
    setup_exe = tmp_path / "JarvisAIOS-Setup-2.0.0.exe"
    setup_exe.write_bytes(b"MZ")

    statut_simule = {"val": "HashMismatch"}
    commandes: list[list[str]] = []

    class _FakePsProc:
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            del timeout
            return f"{statut_simule['val']}\n", ""

    def _fake_popen(cmd: list[str], **kwargs: Any) -> Any:
        del kwargs
        commandes.append(cmd)
        if cmd[0] == "powershell":
            return _FakePsProc()
        return object()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    with pytest.raises(ErreurInstallation, match="Signature Authenticode invalide"):
        installer.appliquer_maj_complete(setup_exe)

    commandes.clear()
    statut_simule["val"] = "NotSigned"
    installer.appliquer_maj_complete(setup_exe)
    assert commandes[0][0] == "powershell"
    assert commandes[1] == [
        str(setup_exe),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/RESTARTAPPLICATIONS",
    ]

    commandes.clear()
    statut_simule["val"] = "Valid"
    installer.appliquer_maj_complete(setup_exe)
    assert commandes[1][0] == str(setup_exe)


def test_api_maj_installer_refuse_en_mode_developpement(
    client: TestClient,
    auth_headers: dict[str, str],
    jarvis_root: Path,
) -> None:
    """Refuse POST /api/maj/installer avec 409 lorsque APP_DIR/installed.flag est absent."""
    assert not (jarvis_root / "installed.flag").exists()
    resp = client.post("/api/maj/installer", headers=auth_headers)
    assert resp.status_code == 409
    assert "installed.flag" in resp.json()["detail"]


def test_api_maj_installer_et_progression_avec_installed_flag(
    client: TestClient,
    auth_headers: dict[str, str],
    jarvis_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le flux complet verifier -> installer -> progression en mode installé simulé."""
    (jarvis_root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (jarvis_root / "RUNTIME_VERSION").write_text("rt-1\n", encoding="utf-8")
    (jarvis_root / "installed.flag").write_text("1\n", encoding="utf-8")
    (jarvis_root / "Jarvis.exe").write_bytes(b"MZ")
    config.refresh_paths(nouveau_root=jarvis_root, app_dir=jarvis_root)

    zip_buffer = tmp_path / "payload.zip"
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("VERSION", "1.1.0")
    zip_bytes = zip_buffer.read_bytes()
    zip_sha = hashlib.sha256(zip_bytes).hexdigest()

    manifest = {
        "version": "1.1.0",
        "runtime_version": "rt-1",
        "notes": "Correctif",
        "app_zip": {"name": "JarvisAIOS-app-1.1.0.zip", "sha256": zip_sha, "size": len(zip_bytes)},
        "setup": {"name": "JarvisAIOS-Setup-1.1.0.exe", "sha256": "b" * 64, "size": 100},
    }
    release = {
        "tag_name": "v1.1.0",
        "assets": [
            {
                "name": "manifest.json",
                "browser_download_url": "https://github.com/OWNER/jarvis-aios/releases/download/v1.1.0/manifest.json",
            },
            {
                "name": "JarvisAIOS-app-1.1.0.zip",
                "browser_download_url": "https://github.com/OWNER/jarvis-aios/releases/download/v1.1.0/JarvisAIOS-app-1.1.0.zip",
            },
            {
                "name": "JarvisAIOS-Setup-1.1.0.exe",
                "browser_download_url": "https://github.com/OWNER/jarvis-aios/releases/download/v1.1.0/JarvisAIOS-Setup-1.1.0.exe",
            },
        ],
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=release)
        if request.url.path.endswith("/manifest.json"):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith(".zip"):
            return httpx.Response(200, headers={"Content-Length": str(len(zip_bytes))}, content=zip_bytes)
        return httpx.Response(404)

    transport = httpx.MockTransport(_handler)
    orig_verifier = checker.verifier_release_github

    async def _verifier_mock(depot: str | None = None, **_kwargs: Any) -> checker.InfoRelease:
        return await orig_verifier(depot=depot, transport=transport)

    monkeypatch.setattr(checker, "verifier_release_github", _verifier_mock)

    orig_telecharger = downloader.telecharger_fichier

    async def _dl_mock(**kwargs: Any) -> Path:
        kwargs["transport"] = transport
        return await orig_telecharger(**kwargs)

    monkeypatch.setattr(downloader, "telecharger_fichier", _dl_mock)
    monkeypatch.setattr(installer, "arreter_serveur_propre", lambda: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: object())

    assert client.post("/api/maj/verifier", headers=auth_headers).status_code == 200
    r_inst = client.post("/api/maj/installer", headers=auth_headers)
    assert r_inst.status_code == 200
    assert r_inst.json()["ok"] is True

    prog = {}
    for _ in range(30):
        prog = client.get("/api/maj/progression").json()
        if prog["pourcent"] == 100 or prog["erreur"]:
            break
        time.sleep(0.05)

    assert prog["erreur"] is None
    assert prog["pourcent"] == 100
    assert (jarvis_root / "_maj_staging" / "VERSION").read_text(encoding="utf-8") == "1.1.0"
