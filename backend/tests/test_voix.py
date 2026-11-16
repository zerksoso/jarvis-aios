"""Tests unitaires et d'intégration du module voix (STT, TTS et API /api/voix/*)."""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.voice import stt, tts


def _creer_wav_test(chemin: Path) -> None:
    """Écrit un mini fichier WAV valide de 0,1 s pour les tests sans micro."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(chemin), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)


def test_nettoyage_markdown_pour_tts() -> None:
    """Vérifie le retrait du Markdown, le remplacement des blocs de code et la troncature 1 200 car."""
    brut = (
        "# Titre principal\n\n"
        "Voici **un texte important** avec `du code` et [un lien](https://exemple.fr).\n\n"
        "```python\nprint('secret')\n```\n\n"
        "- Point un\n"
        "- Point deux\n"
    )
    propre = tts.nettoyer_markdown_pour_tts(brut)
    assert "**" not in propre
    assert "#" not in propre
    assert "`" not in propre
    assert "https://exemple.fr" not in propre
    assert "(bloc de code)" in propre
    assert "Titre principal" in propre
    assert "un texte important" in propre
    assert "un lien" in propre

    long_texte = "Bonjour Jarvis. " * 200
    tronque = tts.nettoyer_markdown_pour_tts(long_texte)
    assert len(tronque) <= 1200


async def test_tts_cache_edge_tts_simule_et_repli_pyttsx3(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie la mise en cache MP3 avec edge-tts simulé et le repli WAV pyttsx3."""
    del jarvis_root
    appels_edge: list[tuple[str, str]] = []

    class _FakeCommunicateOk:
        def __init__(self, text: str, voice: str) -> None:
            self.text = text
            self.voice = voice

        async def save(self, audio_fname: str) -> None:
            appels_edge.append((self.text, self.voice))
            Path(audio_fname).write_bytes(b"ID3-FAKE-MP3-DATA")

    monkeypatch.setattr(tts.edge_tts, "Communicate", _FakeCommunicateOk)

    chemin1 = await tts.synthetiser("**Bonjour** #Jarvis", voix="fr-FR-DeniseNeural")
    chemin2 = await tts.synthetiser("Bonjour Jarvis", voix="fr-FR-DeniseNeural")

    assert chemin1 == chemin2
    assert chemin1.suffix == ".mp3"
    assert chemin1.read_bytes() == b"ID3-FAKE-MP3-DATA"
    # Les deux textes deviennent identiques après nettoyage Markdown : un seul appel réseau.
    assert len(appels_edge) == 1

    # Simulation d'échec réseau edge-tts -> bascule sur pyttsx3 WAV.
    class _FakeCommunicateFail:
        def __init__(self, text: str, voice: str) -> None:
            del text, voice

        async def save(self, audio_fname: str) -> None:
            del audio_fname
            raise RuntimeError("Réseau coupé")

    class _FakePyttsx3Engine:
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

    monkeypatch.setattr(tts.edge_tts, "Communicate", _FakeCommunicateFail)
    monkeypatch.setattr(tts.pyttsx3, "init", lambda: _FakePyttsx3Engine())

    chemin_wav = await tts.synthetiser("Mode hors ligne actif", voix="fr-FR-DeniseNeural")
    assert chemin_wav.suffix == ".wav"
    assert chemin_wav.is_file()
    assert chemin_wav.stat().st_size > 0


def test_stt_chargement_paresseux_repli_cpu_et_liberation(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le repli CUDA -> CPU int8, le singleton verrouillé et liberer()."""
    stt.set_modele_for_tests(None)
    stt.liberer()
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)

    appels_init: list[tuple[str, str, str]] = []
    appels_transcribe: list[tuple[str, str | None, bool]] = []

    class _FakeWhisperModel:
        def __init__(self, size: str, device: str, compute_type: str) -> None:
            appels_init.append((size, device, compute_type))
            if device == "cuda":
                raise RuntimeError("CUDA indisponible")

        def transcribe(
            self,
            audio: str,
            language: str | None = None,
            vad_filter: bool = False,
        ) -> tuple[list[Any], dict[str, Any]]:
            appels_transcribe.append((audio, language, vad_filter))
            return [SimpleNamespace(text=" Bonjour "), SimpleNamespace(text="Jarvis ")], {}

    import faster_whisper

    monkeypatch.setattr(faster_whisper, "WhisperModel", _FakeWhisperModel)

    wav = jarvis_root / "runtime" / "voice" / "test.wav"
    _creer_wav_test(wav)

    try:
        t1 = stt.transcrire(wav)
        t2 = stt.transcrire(wav)
        assert t1 == "Bonjour Jarvis"
        assert t2 == "Bonjour Jarvis"
        assert appels_init == [
            ("small", "cuda", "float16"),
            ("small", "cpu", "int8"),
        ]
        assert len(appels_transcribe) == 2
        assert appels_transcribe[0][1] == "fr"
        assert appels_transcribe[0][2] is True

        stt.liberer()
        stt.transcrire(wav)
        # Après liberer(), le modèle est bien rechargé au prochain appel.
        assert len(appels_init) == 4
    finally:
        stt.liberer()
        stt.set_modele_for_tests(None)


def test_api_voix_transcrire_ecouter_parler_et_audio(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teste tous les endpoints REST /api/voix/* ainsi que les diffusions WebSocket associées."""

    class _FakeModelApi:
        def transcribe(
            self,
            audio: str,
            language: str | None = None,
            vad_filter: bool = False,
        ) -> tuple[list[Any], dict[str, Any]]:
            del audio, language, vad_filter
            return [SimpleNamespace(text="Ouvre mes tâches du jour")], {}

    stt.set_modele_for_tests(_FakeModelApi())
    try:
        # 1. POST /api/voix/transcrire (multipart sans python-multipart requis)
        wav_upload = jarvis_root / "runtime" / "upload_src.wav"
        _creer_wav_test(wav_upload)
        resp_tr = client.post(
            "/api/voix/transcrire",
            headers=auth_headers,
            files={"audio": ("dictation.wav", wav_upload.read_bytes(), "audio/wav")},
        )
        assert resp_tr.status_code == 200
        assert resp_tr.json() == {"texte": "Ouvre mes tâches du jour"}

        resp_tr_vide = client.post(
            "/api/voix/transcrire",
            headers=auth_headers,
            files={"autre": ("x.wav", b"123", "audio/wav")},
        )
        assert resp_tr_vide.status_code == 400

        # 2. POST /api/voix/ecouter -> broadcast etat_jarvis ecoute puis reflexion
        wav_micro = jarvis_root / "runtime" / "voice" / "micro_sim.wav"
        _creer_wav_test(wav_micro)
        monkeypatch.setattr(stt, "enregistrer_micro", lambda: wav_micro)

        evenements_ecoute: list[dict[str, Any]] = []
        with client.websocket_connect(f"/ws?token={token}&client=overlay") as ws:
            # Consomme l'événement overlay_statut initial.
            ws.receive_json()
            resp_ec = client.post("/api/voix/ecouter", headers=auth_headers)
            assert resp_ec.status_code == 200
            assert resp_ec.json() == {"texte": "Ouvre mes tâches du jour"}

            for _ in range(5):
                evt = ws.receive_json()
                evenements_ecoute.append(evt)
                if evt["type"] == "etat_jarvis" and evt["data"].get("etat") == "repos":
                    break

        etats = [e["data"]["etat"] for e in evenements_ecoute if e["type"] == "etat_jarvis"]
        assert etats == ["ecoute", "reflexion", "repos"]

        # 3. POST /api/voix/ecouter erreur micro -> 503
        def _micro_hs() -> Path:
            raise stt.MicroIndisponibleError("Micro indisponible : aucun périphérique d'entrée")

        monkeypatch.setattr(stt, "enregistrer_micro", _micro_hs)
        resp_hs = client.post("/api/voix/ecouter", headers=auth_headers)
        assert resp_hs.status_code == 503
        assert "Micro indisponible" in resp_hs.json()["detail"]

        # 4. POST /api/voix/parler + GET /api/voix/audio/{nom} + sécurité sans jeton
        assert client.post("/api/voix/parler", json={"texte": "Salut"}).status_code == 403

        audio_cache = jarvis_root / "runtime" / "tts" / "test_parler.mp3"
        audio_cache.parent.mkdir(parents=True, exist_ok=True)
        audio_cache.write_bytes(b"MP3-TEST-BYTES")

        async def _fake_synth(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
            del texte, voix
            return audio_cache

        monkeypatch.setattr(tts, "synthetiser", _fake_synth)

        with client.websocket_connect(f"/ws?token={token}&client=overlay") as ws:
            ws.receive_json()
            resp_parler = client.post(
                "/api/voix/parler",
                headers=auth_headers,
                json={"texte": "Voici votre programme."},
            )
            assert resp_parler.status_code == 200
            url = resp_parler.json()["url"]
            assert url == "/api/voix/audio/test_parler.mp3"

            evt_voix = ws.receive_json()
            assert evt_voix["type"] == "voix_parler"
            assert evt_voix["data"] == {
                "url": "/api/voix/audio/test_parler.mp3",
                "texte": "Voici votre programme.",
            }

        resp_audio = client.get(url)
        assert resp_audio.status_code == 200
        assert resp_audio.content == b"MP3-TEST-BYTES"
        assert client.get("/api/voix/audio/inconnu.mp3").status_code == 404
    finally:
        stt.set_modele_for_tests(None)
        stt.liberer()
