"""Tests de l'assistant console de configuration vocale ``setup_voice.py``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import setup_voice
from backend import config, settings_store
from backend.voice import stt


def test_setup_voice_non_interactif_ecrit_bloc_voix_et_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le mode --non-interactif écrit le bloc voix complet et runtime/voix_configuree.flag dans JARVIS_ROOT."""
    ancien_app_dir = config.APP_DIR
    monkeypatch.setenv("JARVIS_ROOT", str(tmp_path))
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)

    # Pré-existant dans parametres.json à préserver.
    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    params_path = runtime / "parametres.json"
    params_path.write_text(
        json.dumps({"accent": "#f59e0b", "verbosite": "concis"}, indent=2),
        encoding="utf-8",
    )

    try:
        code = setup_voice.main(["--non-interactif"])
        assert code == 0

        flag_path = runtime / "voix_configuree.flag"
        assert flag_path.is_file()
        assert params_path.is_file()

        donnees = json.loads(params_path.read_text(encoding="utf-8"))
        assert donnees["accent"] == "#f59e0b"
        assert donnees["verbosite"] == "concis"
        assert donnees["voix"] == {
            "active": True,
            "nom": "fr-FR-HenriNeural",
            "moteur_tts": "edge",
            "tts_url": None,
            "stt_modele": "small",
            "stt_beam_final": 5,
            "declenchement": "push",
            "micro": None,
            "rapide": True,
            "modele_rapide": "gemini-4-hydrogen-low",
            "llm_rapide": "agy",
            "llm_local_url": "http://127.0.0.1:1234/v1",
            "llm_local_modele": "",
            "mode": "local",
            "web_service": "chatgpt",
            "web_navigateur": "defaut",
        }
        assert settings_store.lire()["voix"]["nom"] == "fr-FR-HenriNeural"
    finally:
        config.refresh_paths(app_dir=ancien_app_dir)


def test_setup_voice_interactif_reponses_simulees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le parcours interactif local en français avec choix personnalisés sans préchargement réseau."""
    ancien_app_dir = config.APP_DIR
    monkeypatch.setenv("JARVIS_ROOT", str(tmp_path))
    monkeypatch.setattr(stt, "cuda_disponible", lambda: True)

    reponses = iter(
        [
            "2",  # Pipeline local
            "2",  # medium
            "4",  # Serveur HTTP local
            "http://127.0.0.1:8899/",
            "2",  # Conversation continue
            "n",  # Ne pas précharger
        ]
    )

    try:
        bloc = setup_voice.executer_assistant(
            non_interactif=False,
            dossier_donnees=tmp_path,
            input_fn=lambda _prompt: next(reponses),
        )
        assert bloc["mode"] == "local"
        assert bloc["stt_modele"] == "medium"
        assert bloc["stt_beam_final"] == 5
        assert bloc["moteur_tts"] == "http"
        assert bloc["tts_url"] == "http://127.0.0.1:8899"
        assert bloc["declenchement"] == "continu"
        assert (tmp_path / "runtime" / "voix_configuree.flag").is_file()
        assert (tmp_path / "runtime" / "parametres.json").is_file()
    finally:
        config.refresh_paths(app_dir=ancien_app_dir)


def test_setup_voice_interactif_pont_web(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le choix interactif 1 (Pont web) avec service et navigateur."""
    ancien_app_dir = config.APP_DIR
    monkeypatch.setenv("JARVIS_ROOT", str(tmp_path))
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)

    reponses = iter(
        [
            "1",  # Pont web
            "2",  # Gemini
            "2",  # Chrome
        ]
    )

    try:
        bloc = setup_voice.executer_assistant(
            non_interactif=False,
            dossier_donnees=tmp_path,
            input_fn=lambda _prompt: next(reponses),
        )
        assert bloc["mode"] == "web"
        assert bloc["web_service"] == "gemini"
        assert bloc["web_navigateur"] == "chrome"
        assert (tmp_path / "runtime" / "voix_configuree.flag").is_file()
        assert (tmp_path / "runtime" / "parametres.json").is_file()
    finally:
        config.refresh_paths(app_dir=ancien_app_dir)
