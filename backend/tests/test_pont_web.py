"""Tests unitaires et API du pont web vocal simple (sans fenêtre ni navigateur réels)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import settings_store
from backend.voice import pont_web


def test_liste_blanche_services_et_urls_interdites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seules les deux URL fixes (ChatGPT et Gemini) sont acceptées par le pont web."""
    assert pont_web.resoudre_url_service("chatgpt") == ("chatgpt", "https://chatgpt.com/")
    assert pont_web.resoudre_url_service("GEMINI") == ("gemini", "https://gemini.google.com/app")

    appels_startfile: list[str] = []
    appels_popen: list[list[str]] = []
    monkeypatch.setattr(os, "startfile", appels_startfile.append, raising=False)
    monkeypatch.setattr(pont_web.subprocess, "Popen", lambda cmd, **_kw: appels_popen.append(list(cmd)))

    for invalide in ("claude", "perplexity", "https://evil.example/", "", "file:///c:/windows"):
        with pytest.raises(ValueError):
            pont_web.ouvrir_service_web(service=invalide, navigateur="defaut")

    with pytest.raises(ValueError):
        pont_web._ouvrir_url("https://exemple-non-autorise.com/", "defaut")

    with pytest.raises(ValueError):
        pont_web.ouvrir_service_web(service="chatgpt", navigateur="firefox")

    assert appels_startfile == []
    assert appels_popen == []


def test_trouver_fenetre_par_titre_simule_enum_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simule ``EnumWindows`` pour vérifier la détection insensible à la casse du titre."""
    fenetres = [
        (101, False, "ChatGPT caché"),
        (102, True, ""),
        (103, True, "Bloc-notes"),
        (204, True, "ChatGPT - Google Chrome"),
        (305, True, "Google Gemini - Microsoft Edge"),
    ]

    class _FakeUser32:
        def IsWindowVisible(self, hwnd: int) -> bool:
            return any(h == hwnd and vis for h, vis, _ in fenetres)

        def GetWindowTextLengthW(self, hwnd: int) -> int:
            for h, _vis, titre in fenetres:
                if h == hwnd:
                    return len(titre)
            return 0

        def GetWindowTextW(self, hwnd: int, buf: Any, max_len: int) -> int:
            del max_len
            for h, _vis, titre in fenetres:
                if h == hwnd:
                    buf.value = titre
                    return len(titre)
            return 0

        def EnumWindows(self, callback: Any, lparam: int) -> int:
            for h, _vis, _titre in fenetres:
                if not callback(h, lparam):
                    break
            return 1

    import ctypes

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", lambda *_args: (lambda fn: fn), raising=False)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=_FakeUser32()),
        raising=False,
    )

    assert pont_web.trouver_fenetre_par_titre(("chatgpt",)) == 204
    assert pont_web.trouver_fenetre_par_titre(("gemini",)) == 305
    assert pont_web.trouver_fenetre_par_titre(("inexistant",)) is None


def test_ouvrir_service_web_fenetre_trouvee_met_au_premier_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quand la fenêtre est déjà ouverte, la met au premier plan sans lancer de navigateur."""
    mises_avant: list[int] = []
    lancements: list[Any] = []

    monkeypatch.setattr(pont_web, "trouver_fenetre_par_titre", lambda mots: 999 if "gemini" in mots else None)
    monkeypatch.setattr(pont_web, "mettre_au_premier_plan", lambda hwnd: mises_avant.append(hwnd) or True)
    monkeypatch.setattr(pont_web.subprocess, "Popen", lambda *a, **kw: lancements.append((a, kw)))
    monkeypatch.setattr(os, "startfile", lambda url: lancements.append(url), raising=False)

    res = pont_web.ouvrir_service_web(service="gemini", navigateur="chrome")
    assert res == {"ok": True, "action": "premier_plan", "service": "gemini"}
    assert mises_avant == [999]
    assert lancements == []


def test_ouvrir_service_web_fenetre_absente_defaut_chrome_edge_et_repli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie la commande d'ouverture pour ``defaut``, ``chrome --app``, ``edge --app`` et le repli."""
    monkeypatch.setattr(pont_web, "trouver_fenetre_par_titre", lambda _mots: None)

    pf = tmp_path / "pf"
    pf86 = tmp_path / "pf86"
    local = tmp_path / "local"
    for d in (pf, pf86, local):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ProgramFiles", str(pf))
    monkeypatch.setenv("ProgramFiles(x86)", str(pf86))
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    appels_startfile: list[str] = []
    appels_popen: list[list[str]] = []
    monkeypatch.setattr(os, "startfile", appels_startfile.append, raising=False)
    monkeypatch.setattr(pont_web.subprocess, "Popen", lambda cmd, **_kw: appels_popen.append(list(cmd)))

    # 1. Navigateur par défaut -> os.startfile
    res_defaut = pont_web.ouvrir_service_web(service="chatgpt", navigateur="defaut")
    assert res_defaut == {"ok": True, "action": "ouverture", "service": "chatgpt"}
    assert appels_startfile == ["https://chatgpt.com/"]
    assert appels_popen == []

    # 2. Chrome introuvable -> repli os.startfile
    appels_startfile.clear()
    res_repli = pont_web.ouvrir_service_web(service="gemini", navigateur="chrome")
    assert res_repli == {"ok": True, "action": "ouverture", "service": "gemini"}
    assert appels_startfile == ["https://gemini.google.com/app"]
    assert appels_popen == []

    # 3. Chrome trouvé dans LOCALAPPDATA -> Popen --app
    appels_startfile.clear()
    chrome_exe = local / "Google" / "Chrome" / "Application" / "chrome.exe"
    chrome_exe.parent.mkdir(parents=True, exist_ok=True)
    chrome_exe.write_text("", encoding="utf-8")

    res_chrome = pont_web.ouvrir_service_web(service="chatgpt", navigateur="chrome")
    assert res_chrome == {"ok": True, "action": "ouverture", "service": "chatgpt"}
    assert appels_popen == [[str(chrome_exe), "--app=https://chatgpt.com/"]]
    assert appels_startfile == []

    # 4. Edge trouvé dans ProgramFiles(x86) -> Popen --app
    appels_popen.clear()
    edge_exe = pf86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge_exe.parent.mkdir(parents=True, exist_ok=True)
    edge_exe.write_text("", encoding="utf-8")

    res_edge = pont_web.ouvrir_service_web(service="gemini", navigateur="edge")
    assert res_edge == {"ok": True, "action": "ouverture", "service": "gemini"}
    assert appels_popen == [[str(edge_exe), "--app=https://gemini.google.com/app"]]
    assert appels_startfile == []


def test_api_voix_web_ouvrir(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie ``POST /api/voix/web/ouvrir`` (jeton, lecture réglages, surcharges et 422)."""
    del jarvis_root
    # Protégé par X-Jarvis-Token
    assert client.post("/api/voix/web/ouvrir").status_code == 403

    appels: list[tuple[str, str]] = []

    def _fake_ouvrir(*, service: str, navigateur: str) -> dict[str, Any]:
        appels.append((service, navigateur))
        return {"ok": True, "action": "ouverture", "service": service}

    monkeypatch.setattr(pont_web, "ouvrir_service_web", _fake_ouvrir)

    settings_store.ecrire(
        {"voix": {"mode": "web", "web_service": "gemini", "web_navigateur": "edge"}}
    )

    # Sans corps -> lit les réglages enregistrés
    rep_def = client.post("/api/voix/web/ouvrir", headers=auth_headers)
    assert rep_def.status_code == 200
    assert rep_def.json() == {"ok": True, "action": "ouverture", "service": "gemini"}
    assert appels[-1] == ("gemini", "edge")

    # Avec surcharges valides
    rep_ovr = client.post(
        "/api/voix/web/ouvrir",
        headers=auth_headers,
        json={"service": "chatgpt", "navigateur": "chrome"},
    )
    assert rep_ovr.status_code == 200
    assert rep_ovr.json() == {"ok": True, "action": "ouverture", "service": "chatgpt"}
    assert appels[-1] == ("chatgpt", "chrome")

    # Hors liste blanche -> 422
    assert (
        client.post(
            "/api/voix/web/ouvrir",
            headers=auth_headers,
            json={"service": "inconnu"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/voix/web/ouvrir",
            headers=auth_headers,
            json={"navigateur": "opera"},
        ).status_code
        == 422
    )
