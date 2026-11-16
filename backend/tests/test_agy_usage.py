"""Tests de parsing de ``agy -p /usage --output-format json`` et cache quotas."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.llm import agy_usage, binaries, quota

EXEMPLE_AGY_USAGE = {
    "command": {
        "name": "usage",
        "data": {
            "groups": [
                {
                    "id": "gemini",
                    "buckets": [
                        {
                            "id": "gemini-5h",
                            "remaining_fraction": 0.82,
                            "window": "5h",
                            "reset_time": "2026-11-16T21:00:00Z",
                        },
                        {
                            "id": "gemini-weekly",
                            "remaining_fraction": 0.91,
                            "window": "7d",
                            "reset_time": "2026-11-23T00:00:00Z",
                        },
                    ],
                },
                {
                    "id": "3p",
                    "buckets": [
                        {
                            "id": "3p-5h",
                            "remaining_fraction": 0.65,
                            "window": "5h",
                            "reset_time": "2026-11-16T21:00:00Z",
                        }
                    ],
                },
                {
                    "id": "argon",
                    "buckets": [
                        {
                            "id": "gemini-argon-wtu-weekly",
                            "remaining_fraction": 0.40,
                            "window": "7d",
                            "reset_time": None,
                        }
                    ],
                },
            ]
        },
    }
}


def test_parser_usage_json_format_standard() -> None:
    """Parse correctement data.command.data.groups[].buckets[] vers le format historique."""
    res = agy_usage.parser_usage_json(json.dumps(EXEMPLE_AGY_USAGE))
    assert res is not None
    assert isinstance(res["updatedAt"], str)
    assert res["buckets"] == {
        "gemini-5h": {
            "group": "gemini",
            "window": "5h",
            "remaining": 0.82,
            "resetsAt": "2026-11-16T21:00:00Z",
        },
        "gemini-weekly": {
            "group": "gemini",
            "window": "7d",
            "remaining": 0.91,
            "resetsAt": "2026-11-23T00:00:00Z",
        },
        "3p-5h": {
            "group": "3p",
            "window": "5h",
            "remaining": 0.65,
            "resetsAt": "2026-11-16T21:00:00Z",
        },
        "gemini-argon-wtu-weekly": {
            "group": "argon",
            "window": "7d",
            "remaining": 0.40,
            "resetsAt": None,
        },
    }

    assert quota.restant("gemini", res) == 0.82
    assert quota.restant("3p", res) == 0.65
    assert quota.restant("argon", res) == 0.40


def test_parser_usage_json_invalide_retourne_none() -> None:
    """Retourne None sur JSON invalide ou structure sans groups."""
    assert agy_usage.parser_usage_json("") is None
    assert agy_usage.parser_usage_json("pas du json") is None
    assert agy_usage.parser_usage_json({"autre": 1}) is None


async def test_lire_usage_et_quota_cache_disque_et_nettoyage_env(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exécute agy /usage avec env nettoyé, écrit ROOT/runtime/agy-quota.json et utilise le cache."""
    monkeypatch.setenv("GEMINI_API_KEY", "secret-a-retirer")
    monkeypatch.setenv("GOOGLE_API_KEY", "secret-a-retirer")
    monkeypatch.setattr(binaries, "trouver", lambda nom: "C:/fake/agy.exe" if nom == "agy" else None)

    appels: list[tuple[list[str], dict[str, str]]] = []

    class _FakeProc:
        returncode = 0

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            del timeout
            return json.dumps(EXEMPLE_AGY_USAGE), ""

    def _fake_popen(cmd: list[str], **kwargs: Any) -> _FakeProc:
        appels.append((cmd, dict(kwargs.get("env") or {})))
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    snap = await quota.lire_quotas(forcer=True)
    assert len(appels) == 1
    cmd_lance, env_enfant = appels[0]
    assert cmd_lance == ["C:/fake/agy.exe", "-p", "/usage", "--output-format", "json"]
    assert "GEMINI_API_KEY" not in env_enfant
    assert "GOOGLE_API_KEY" not in env_enfant
    assert snap["buckets"]["gemini-5h"]["remaining"] == 0.82
    assert "claude_cli" in snap

    fichier_cache = jarvis_root / "runtime" / "agy-quota.json"
    assert fichier_cache.is_file()

    # Réinitialise le cache mémoire uniquement : lire_usage_sync doit relire le cache disque sans Popen.
    quota.set_quotas_for_tests(None)
    snap2 = await quota.lire_quotas()
    assert len(appels) == 1
    assert snap2["buckets"]["3p-5h"]["remaining"] == 0.65
