"""Tests unitaires de découverte des binaires CLI via PATH et dossiers Windows."""

import shutil
from pathlib import Path

import pytest

from backend.llm import agy_engine, binaries, claude_engine


def test_trouver_priorise_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retourne immédiatement le chemin trouvé via shutil.which si présent."""
    monkeypatch.setattr(
        shutil,
        "which",
        lambda nom: "C:/fake-path/agy.exe" if nom == "agy" else None,
    )
    assert binaries.trouver("agy") == "C:/fake-path/agy.exe"


def test_trouver_dans_dossiers_connus_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Détecte agy, claude, codex et node dans les emplacements Windows standards sans PATH."""
    monkeypatch.setattr(shutil, "which", lambda _nom: None)

    local_app = tmp_path / "LocalAppData"
    app_data = tmp_path / "AppData"
    user_home = tmp_path / "UserHome"
    prog_files = tmp_path / "ProgramFiles"

    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setenv("APPDATA", str(app_data))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.setenv("ProgramFiles", str(prog_files))

    agy_exe = local_app / "Programs" / "Antigravity" / "bin" / "agy.cmd"
    agy_exe.parent.mkdir(parents=True, exist_ok=True)
    agy_exe.write_text("@echo off\n", encoding="utf-8")

    claude_cmd = app_data / "npm" / "claude.cmd"
    claude_cmd.parent.mkdir(parents=True, exist_ok=True)
    claude_cmd.write_text("@echo off\n", encoding="utf-8")

    codex_exe = user_home / ".local" / "bin" / "codex.exe"
    codex_exe.parent.mkdir(parents=True, exist_ok=True)
    codex_exe.write_text("binary", encoding="utf-8")

    node_exe = prog_files / "nodejs" / "node.exe"
    node_exe.parent.mkdir(parents=True, exist_ok=True)
    node_exe.write_text("binary", encoding="utf-8")

    assert binaries.trouver("agy") == str(agy_exe)
    assert binaries.trouver("claude") == str(claude_cmd)
    assert binaries.trouver("codex") == str(codex_exe)
    assert binaries.trouver("node") == str(node_exe)
    assert binaries.trouver("inexistant") is None
    assert binaries.trouver("") is None


async def test_moteurs_agy_et_claude_utilisent_binaries_trouver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AgyEngine et ClaudeEngine s'appuient sur binaries.trouver() pour disponible()."""
    appels: list[str] = []

    def _fake_trouver(nom: str) -> str | None:
        appels.append(nom)
        return None

    monkeypatch.setattr(binaries, "trouver", _fake_trouver)

    ok_agy, _ = await agy_engine.AgyEngine().disponible()
    ok_claude, _ = await claude_engine.ClaudeEngine().disponible()

    assert ok_agy is False
    assert ok_claude is False
    assert appels == ["agy", "claude"]
