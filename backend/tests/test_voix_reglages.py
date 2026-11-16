"""Tests des réglages ``voix``, routes ``/api/voix/{micros,etat,tester}``, STT dynamique et TTS HTTP."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import settings_store
from backend.voice import stt, tts


def _creer_wav_test(chemin: Path) -> None:
    """Écrit un mini fichier WAV valide pour les tests TTS sans audio réel."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(chemin), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)


def test_parametres_voix_defauts_et_validation(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie les défauts GPU/CPU, la conservation des clés existantes et la validation PUT."""
    del jarvis_root
    # Le lifespan de la fixture client a pu écrire parametres.json avant le monkeypatch CUDA.
    settings_store.config.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)
    voix_cpu = client.get("/api/parametres").json()["voix"]
    assert voix_cpu == {
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
        "mode": "local",
        "web_service": "chatgpt",
        "web_navigateur": "defaut",
    }

    # Si le fichier n'existe pas encore et CUDA est présent -> large-v3-turbo + beam 1.
    settings_path = settings_store.config.SETTINGS_PATH
    settings_path.unlink(missing_ok=True)
    monkeypatch.setattr(stt, "cuda_disponible", lambda: True)
    voix_gpu = client.get("/api/parametres").json()["voix"]
    assert voix_gpu["stt_modele"] == "large-v3-turbo"
    assert voix_gpu["stt_beam_final"] == 1
    assert voix_gpu["nom"] == "fr-FR-HenriNeural"
    assert voix_gpu["mode"] == "local"
    assert voix_gpu["web_service"] == "chatgpt"
    assert voix_gpu["web_navigateur"] == "defaut"

    # Mises à jour valides, y compris réinitialisation explicite de tts_url/micro à null.
    rep_ok = client.put(
        "/api/parametres",
        headers=auth_headers,
        json={
            "voix": {
                "moteur_tts": "http",
                "tts_url": "http://127.0.0.1:8880/",
                "stt_modele": "medium",
                "stt_beam_final": 3,
                "declenchement": "continu",
                "micro": "Casque USB",
                "rapide": False,
                "mode": "web",
                "web_service": "gemini",
                "web_navigateur": "edge",
            }
        },
    )
    assert rep_ok.status_code == 200
    v = rep_ok.json()["voix"]
    assert v["moteur_tts"] == "http"
    assert v["tts_url"] == "http://127.0.0.1:8880"
    assert v["stt_modele"] == "medium"
    assert v["stt_beam_final"] == 3
    assert v["declenchement"] == "continu"
    assert v["micro"] == "Casque USB"
    assert v["rapide"] is False
    assert v["active"] is True
    assert v["nom"] == "fr-FR-HenriNeural"
    assert v["mode"] == "web"
    assert v["web_service"] == "gemini"
    assert v["web_navigateur"] == "edge"

    rep_clear = client.put(
        "/api/parametres",
        headers=auth_headers,
        json={"voix": {"tts_url": None, "micro": None}},
    )
    assert rep_clear.status_code == 200
    assert rep_clear.json()["voix"]["tts_url"] is None
    assert rep_clear.json()["voix"]["micro"] is None

    # Validations 422
    invalides = [
        {"voix": {"moteur_tts": "inconnu"}},
        {"voix": {"stt_modele": "geant"}},
        {"voix": {"stt_beam_final": 0}},
        {"voix": {"declenchement": "telepathie"}},
        {"voix": {"tts_url": "ftp://serveur-invalide"}},
        {"voix": {"nom": "   "}},
        {"voix": {"mode": "hybride"}},
        {"voix": {"web_service": "perplexity"}},
        {"voix": {"web_navigateur": "firefox"}},
    ]
    for payload in invalides:
        assert client.put("/api/parametres", headers=auth_headers, json=payload).status_code == 422


def test_stt_changement_modele_a_chaud_et_beam_final(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le changement de voix.stt_modele recharge le modèle au prochain appel et applique stt_beam_final."""
    del jarvis_root
    stt.set_modele_for_tests(None)
    stt.liberer()
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)

    inits: list[str] = []
    beams: list[int] = []

    class _FakeWhisper:
        def __init__(self, size: str, device: str, compute_type: str) -> None:
            del device, compute_type
            inits.append(size)

        def transcribe(
            self,
            audio: np.ndarray,
            *,
            language: str | None = None,
            beam_size: int = 5,
            vad_filter: bool = True,
            condition_on_previous_text: bool = True,
        ) -> tuple[list[Any], dict[str, Any]]:
            del audio, language, vad_filter, condition_on_previous_text
            beams.append(beam_size)
            return [SimpleNamespace(text="Test")], {}

    import faster_whisper

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisper)
    try:
        settings_store.ecrire({"voix": {"stt_modele": "small", "stt_beam_final": 4}})
        assert stt.transcrire_tableau(np.ones(800, dtype=np.float32), rapide=False) == "Test"
        assert inits == ["small"]
        assert beams == [4]

        # Même modèle -> pas de rechargement
        assert stt.transcrire_tableau(np.ones(800, dtype=np.float32), rapide=False) == "Test"
        assert inits == ["small"]

        # Changement à chaud vers medium + beam 2 -> rechargement automatique
        settings_store.ecrire({"voix": {"stt_modele": "medium", "stt_beam_final": 2}})
        assert stt.transcrire_tableau(np.ones(800, dtype=np.float32), rapide=False) == "Test"
        assert inits == ["small", "medium"]
        assert beams == [4, 4, 2]
    finally:
        stt.liberer()


def test_api_voix_micros_etat_et_tester(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teste GET /api/voix/micros, GET /api/voix/etat et POST /api/voix/tester."""
    stt.set_modele_for_tests(None)
    stt.liberer()
    # Le lifespan de la fixture client a pu écrire parametres.json avant le monkeypatch CUDA.
    settings_store.config.SETTINGS_PATH.unlink(missing_ok=True)
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)

    fake_devices = [
        {"name": "Casque Bureau", "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Haut-parleurs", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "Micro Studio", "max_input_channels": 1, "max_output_channels": 0},
    ]
    monkeypatch.setattr(stt.sd, "query_devices", lambda: fake_devices)
    monkeypatch.setattr(stt.sd, "default", SimpleNamespace(device=(2, 1)))

    rep_micros = client.get("/api/voix/micros")
    assert rep_micros.status_code == 200
    assert rep_micros.json() == [
        {"index": 0, "nom": "Casque Bureau", "defaut": False},
        {"index": 2, "nom": "Micro Studio", "defaut": True},
    ]

    rep_etat = client.get("/api/voix/etat")
    assert rep_etat.status_code == 200
    assert rep_etat.json() == {
        "stt_modele": "small",
        "stt_peripherique": "cpu",
        "stt_charge": False,
        "tts_moteur": "edge",
        "tts_http_joignable": None,
    }

    # POST /api/voix/tester protégé par jeton et renvoie un flux audio
    assert client.post("/api/voix/tester", json={}).status_code == 403

    audio_test = jarvis_root / "runtime" / "tts" / "test_voix_api.mp3"
    audio_test.parent.mkdir(parents=True, exist_ok=True)
    audio_test.write_bytes(b"ID3-TEST-VOIX")

    appels_synth: list[tuple[str, str | None]] = []

    async def _fake_synth(texte: str, voix: str | None = None, **kwargs: Any) -> Path:
        del kwargs
        appels_synth.append((texte, voix))
        return audio_test

    monkeypatch.setattr(tts, "synthetiser", _fake_synth)

    rep_test = client.post("/api/voix/tester", headers=auth_headers, json={})
    assert rep_test.status_code == 200
    assert rep_test.headers["content-type"].startswith("audio/mpeg")
    assert rep_test.content == b"ID3-TEST-VOIX"
    assert len(appels_synth) == 1
    assert appels_synth[0][1] == "fr-FR-HenriNeural"


async def test_tts_http_mock_transport_repli_et_moteur_windows(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie moteur_tts='http' (MockTransport), repli vers edge-tts/pyttsx3 et moteur_tts='windows'."""
    del jarvis_root
    requetes_http: list[dict[str, Any]] = []

    def _handler_ok(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:8880/v1/audio/speech"
        requetes_http.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, content=b"HTTP-MP3-AUDIO", headers={"content-type": "audio/mpeg"})

    settings_store.ecrire(
        {
            "voix": {
                "nom": "fr-FR-HenriNeural",
                "moteur_tts": "http",
                "tts_url": "http://127.0.0.1:8880",
            }
        }
    )

    tts.set_http_transport_for_tests(httpx.MockTransport(_handler_ok))
    try:
        chemin_http = await tts.synthetiser("Bonjour HTTP")
        assert chemin_http.suffix == ".mp3"
        assert chemin_http.read_bytes() == b"HTTP-MP3-AUDIO"
        assert requetes_http == [
            {
                "model": "tts-1",
                "input": "Bonjour HTTP",
                "voice": "fr-FR-HenriNeural",
                "response_format": "mp3",
            }
        ]

        # Repli HTTP -> edge-tts si serveur injoignable
        def _handler_ko(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connexion refusée", request=request)

        tts.set_http_transport_for_tests(httpx.MockTransport(_handler_ko))

        appels_edge: list[tuple[str, str]] = []

        class _FakeEdgeOk:
            def __init__(self, text: str, voice: str) -> None:
                self.text = text
                self.voice = voice

            async def save(self, audio_fname: str) -> None:
                appels_edge.append((self.text, self.voice))
                Path(audio_fname).write_bytes(b"EDGE-FALLBACK-MP3")

        monkeypatch.setattr(tts.edge_tts, "Communicate", _FakeEdgeOk)

        chemin_repli_edge = await tts.synthetiser("Repli edge")
        assert chemin_repli_edge.read_bytes() == b"EDGE-FALLBACK-MP3"
        assert appels_edge == [("Repli edge", "fr-FR-HenriNeural")]

        # Repli complet HTTP -> edge KO -> pyttsx3 WAV
        class _FakeEdgeKo:
            def __init__(self, text: str, voice: str) -> None:
                del text, voice

            async def save(self, audio_fname: str) -> None:
                del audio_fname
                raise RuntimeError("Edge coupé")

        class _FakePyttsx3:
            def getProperty(self, name: str) -> list[Any]:
                del name
                return []

            def setProperty(self, name: str, value: Any) -> None:
                del name, value

            def save_to_file(self, texte: str, filename: str) -> None:
                del texte
                _creer_wav_test(Path(filename))

            def runAndWait(self) -> None:
                return None

        monkeypatch.setattr(tts.edge_tts, "Communicate", _FakeEdgeKo)
        monkeypatch.setattr(tts.pyttsx3, "init", lambda: _FakePyttsx3())

        chemin_repli_win = await tts.synthetiser("Repli windows final")
        assert chemin_repli_win.suffix == ".wav"
        assert chemin_repli_win.stat().st_size > 0

        # Moteur windows direct (n'appelle jamais edge-tts ni HTTP)
        settings_store.ecrire({"voix": {"moteur_tts": "windows"}})
        chemin_direct_win = await tts.synthetiser("Direct windows")
        assert chemin_direct_win.suffix == ".wav"
        assert chemin_direct_win.stat().st_size > 0
    finally:
        tts.set_http_transport_for_tests(None)
