"""Tests de comparaison semver, vérification GitHub Releases et politique /api/maj."""

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.update import checker, service
from backend.update.downloader import ErreurTelechargement
from backend.ws_hub import hub


def test_semver_comparaisons_et_preversions() -> None:
    """Vérifie la précédence SemVer 2.0.0 sans dépendance externe."""
    assert checker.est_plus_recente("1.2.10", "1.2.9") is True
    assert checker.est_plus_recente("1.2.9", "1.2.10") is False
    assert checker.est_plus_recente("v2.0.0", "1.99.99") is True
    assert checker.comparer_semver("v1.2.10", "1.2.10+build.5") == 0

    # Préversions
    assert checker.est_plus_recente("1.0.0", "1.0.0-rc.1") is True
    assert checker.est_plus_recente("1.0.0-rc.1", "1.0.0") is False
    assert checker.est_plus_recente("1.0.0-beta", "1.0.0-alpha.1") is True
    assert checker.est_plus_recente("1.0.0-alpha.1", "1.0.0-alpha") is True
    assert checker.est_plus_recente("1.0.0-alpha.beta", "1.0.0-alpha.1") is True
    assert checker.est_plus_recente("0.1.0", "0.0.0-dev") is True

    with pytest.raises(ValueError):
        checker.parser_semver("pas-une-version")


def _creer_transport_release(
    version: str,
    runtime_version: str,
    host_asset: str = "github.com",
) -> httpx.MockTransport:
    """Construit un ``httpx.MockTransport`` simulant GitHub Releases + manifest.json."""
    manifest = {
        "version": version,
        "runtime_version": runtime_version,
        "notes": f"Notes {version}",
        "app_zip": {
            "name": f"JarvisAIOS-app-{version}.zip",
            "sha256": "a" * 64,
            "size": 1024,
        },
        "setup": {
            "name": f"JarvisAIOS-Setup-{version}.exe",
            "sha256": "b" * 64,
            "size": 2048,
        },
    }
    release = {
        "tag_name": f"v{version}",
        "body": "Notes GitHub",
        "assets": [
            {
                "name": "manifest.json",
                "browser_download_url": f"https://{host_asset}/OWNER/jarvis-aios/releases/download/v{version}/manifest.json",
            },
            {
                "name": f"JarvisAIOS-app-{version}.zip",
                "browser_download_url": f"https://{host_asset}/OWNER/jarvis-aios/releases/download/v{version}/JarvisAIOS-app-{version}.zip",
            },
            {
                "name": f"JarvisAIOS-Setup-{version}.exe",
                "browser_download_url": f"https://{host_asset}/OWNER/jarvis-aios/releases/download/v{version}/JarvisAIOS-Setup-{version}.exe",
            },
        ],
    }

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=release)
        if request.url.path.endswith("/manifest.json"):
            return httpx.Response(200, json=manifest)
        return httpx.Response(404)

    return httpx.MockTransport(_handler)


async def test_checker_release_legere_et_complete(jarvis_root: Path) -> None:
    """Détecte disponible=True/False et legere=True/False selon RUNTIME_VERSION."""
    (jarvis_root / "VERSION").write_text("1.2.9\n", encoding="utf-8")
    (jarvis_root / "RUNTIME_VERSION").write_text("rt-abc\n", encoding="utf-8")
    config.refresh_paths(nouveau_root=jarvis_root, app_dir=jarvis_root)

    info_legere = await checker.verifier_release_github(
        transport=_creer_transport_release("1.2.10", "rt-abc")
    )
    assert info_legere.disponible is True
    assert info_legere.legere is True
    assert info_legere.version == "1.2.10"
    assert info_legere.notes == "Notes 1.2.10"

    info_complete = await checker.verifier_release_github(
        transport=_creer_transport_release("1.2.10", "rt-xyz")
    )
    assert info_complete.disponible is True
    assert info_complete.legere is False

    info_identique = await checker.verifier_release_github(
        transport=_creer_transport_release("1.2.9", "rt-abc")
    )
    assert info_identique.disponible is False


async def test_checker_refuse_hote_non_autorise(jarvis_root: Path) -> None:
    """Refuse tout asset ou manifest hébergé hors des hôtes GitHub autorisés."""
    del jarvis_root
    transport = _creer_transport_release("2.0.0", "rt-1", host_asset="evil.example")
    with pytest.raises(ErreurTelechargement):
        await checker.verifier_release_github(transport=transport)


async def test_service_mode_jamais_aucune_requete_reseau(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """En mode 'jamais', verifier(forcer=False) n'émet aucune requête réseau."""
    r = client.post("/api/maj/mode", headers=auth_headers, json={"mode": "jamais"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "mode": "jamais"}

    def _interdit(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Requête réseau interdite en mode jamais : {request.url}")

    etat = await service.verifier(forcer=False, transport=httpx.MockTransport(_interdit))
    assert etat["mode"] == "jamais"
    assert etat["disponible"] is False
    assert etat["verifie_le"] is None


async def test_service_mode_demander_ne_lance_pas_installation_auto(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En mode 'demander', diffuse maj_disponible en WS mais n'installe jamais sans appel explicite."""
    (jarvis_root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (jarvis_root / "RUNTIME_VERSION").write_text("rt-1\n", encoding="utf-8")
    (jarvis_root / "installed.flag").write_text("1\n", encoding="utf-8")
    config.refresh_paths(nouveau_root=jarvis_root, app_dir=jarvis_root)

    diffusions: list[tuple[str, dict[str, Any]]] = []

    async def _fake_broadcast(msg_type: str, data: dict[str, Any] | None = None, **_kwargs: Any) -> None:
        diffusions.append((msg_type, data or {}))

    monkeypatch.setattr(hub, "broadcast", _fake_broadcast)

    installs: list[str] = []

    async def _fake_install(**_kwargs: Any) -> dict[str, Any]:
        installs.append("called")
        return {"ok": True}

    monkeypatch.setattr(service, "demarrer_installation", _fake_install)

    service.changer_mode("demander")
    etat = await service.verifier(
        forcer=False,
        transport=_creer_transport_release("1.1.0", "rt-1"),
    )
    assert etat["disponible"] is True
    assert etat["derniere"] == {"version": "1.1.0", "notes": "Notes 1.1.0", "legere": True}
    assert installs == []
    assert diffusions == [
        ("maj_disponible", {"version": "1.1.0", "notes": "Notes 1.1.0", "legere": True})
    ]


def test_api_maj_etat_verifier_et_mode(
    client: TestClient,
    auth_headers: dict[str, str],
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie GET /api/maj/etat, POST /api/maj/verifier et POST /api/maj/mode."""
    (jarvis_root / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    (jarvis_root / "RUNTIME_VERSION").write_text("rt-1\n", encoding="utf-8")
    config.refresh_paths(nouveau_root=jarvis_root, app_dir=jarvis_root)

    r_etat = client.get("/api/maj/etat")
    assert r_etat.status_code == 200
    assert r_etat.json()["version"] == "1.0.0"
    assert r_etat.json()["disponible"] is False
    assert r_etat.json()["mode"] == "demander"

    transport = _creer_transport_release("1.2.0", "rt-1")
    orig_verifier = checker.verifier_release_github

    async def _verifier_mock(depot: str | None = None, **_kwargs: Any) -> checker.InfoRelease:
        return await orig_verifier(depot=depot, transport=transport)

    monkeypatch.setattr(checker, "verifier_release_github", _verifier_mock)

    r_verif = client.post("/api/maj/verifier", headers=auth_headers)
    assert r_verif.status_code == 200
    corps = r_verif.json()
    assert corps["disponible"] is True
    assert corps["derniere"]["version"] == "1.2.0"
    assert corps["derniere"]["legere"] is True
    assert isinstance(corps["verifie_le"], str)

    assert client.post("/api/maj/mode", json={"mode": "auto"}).status_code == 403
    r_mode = client.post("/api/maj/mode", headers=auth_headers, json={"mode": "auto"})
    assert r_mode.status_code == 200
    assert json.loads((jarvis_root / "runtime" / "parametres.json").read_text(encoding="utf-8"))["maj"]["mode"] == "auto"


async def test_boucle_verification_ne_crashe_pas_si_github_injoignable(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La boucle périodique capture toute erreur réseau GitHub sans planter le serveur."""
    del jarvis_root
    appels = 0

    async def _echec_reseau(**_kwargs: Any) -> dict[str, Any]:
        nonlocal appels
        appels += 1
        raise ErreurTelechargement("GitHub injoignable")

    monkeypatch.setattr(service, "verifier", _echec_reseau)

    tache = asyncio.create_task(
        service.boucle_verification_periodique(delai_initial_s=0.0, intervalle_s=0.05)
    )
    await asyncio.sleep(0.03)
    tache.cancel()
    with suppress(asyncio.CancelledError):
        await tache
    assert appels >= 1
