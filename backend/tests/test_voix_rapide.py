"""Tests de la voie rapide dans ``SessionVocale`` (/ws/voix + SessionAgyChaude simulée)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend import settings_store
from backend.api import chat
from backend.api.chat import ChatRequest
from backend.brain import memories
from backend.llm.base import EngineError
from backend.voice import stream, stt, tts
from backend.ws_hub import hub


def _trames_pcm_20ms(amplitude: int, duree_ms: int) -> list[bytes]:
    """Découpe un signal PCM 16 kHz mono int16 LE d'amplitude constante en trames de 20 ms."""
    echantillons_par_trame = 320
    nb_trames = duree_ms // 20
    trame = np.full(echantillons_par_trame, amplitude, dtype="<i2").tobytes()
    return [trame for _ in range(nb_trames)]


def _vad_amplitude(fenetre: np.ndarray) -> float:
    """VAD simulé déterministe : voix (> 0.9) si crête > 0.15, sinon silence (0.05)."""
    return 0.92 if float(np.max(np.abs(fenetre))) > 0.15 else 0.05


def _recevoir_message(ws: Any) -> tuple[str, Any]:
    """Lit la prochaine trame JSON ou binaire sur le WebSocket ``TestClient``."""
    msg = ws.receive()
    if msg.get("text") is not None:
        return ("json", json.loads(msg["text"]))
    return ("bytes", msg.get("bytes", b""))


def test_voie_rapide_streaming_premiere_phrase_et_enregistrement_tour(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La 1re phrase est synthétisée avant la fin du flux agy ; le 1er message inclut profil/souvenirs et le tour est sauvé."""
    profil_dir = jarvis_root / "second-brain" / "00-Profil"
    profil_dir.mkdir(parents=True, exist_ok=True)
    (profil_dir / "profil-utilisateur.md").write_text("P" * 4000, encoding="utf-8")
    memories.ajouter_souvenir("preference", "Toujours tutoyer l'utilisateur.", source="test")

    settings_store.ecrire({"voix": {"rapide": True, "modele_rapide": "gemini-4-hydrogen-low"}})
    monkeypatch.setattr(stream, "_obtenir_modele_vad", lambda: _vad_amplitude)
    monkeypatch.setattr(stt, "precharger", lambda: None)
    monkeypatch.setattr(
        stt,
        "transcrire_tableau",
        lambda audio, rapide=False: "Quel" if rapide else "Quelle heure est-il ?",
    )

    phrases_synthetisees: list[str] = []

    async def _fake_synthetiser(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
        del voix
        phrases_synthetisees.append(texte)
        tts_dir = jarvis_root / "runtime" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        cible = tts_dir / f"rapide_{len(phrases_synthetisees)}.mp3"
        cible.write_bytes(f"AUDIO:{texte}".encode())
        return cible

    monkeypatch.setattr(tts, "synthetiser", _fake_synthetiser)

    debloquer_fin = asyncio.Event()
    prompts_recus: list[str] = []

    class _FakeAgyChaude:
        def __init__(self, *, modele: str = "gemini-4-hydrogen-low", **kwargs: Any) -> None:
            del kwargs
            self.modele = modele
            self.est_active = False
            self.tours_sur_processus = 0

        async def demarrer(self) -> None:
            self.est_active = True

        async def arreter(self) -> None:
            self.est_active = False

        async def envoyer(self, texte: str) -> AsyncIterator[str]:
            prompts_recus.append(texte)
            yield "Voici la première phrase complète maintenant. "
            await debloquer_fin.wait()
            yield "Et voici la deuxième phrase finale."
            self.tours_sur_processus += 1

    monkeypatch.setattr(stream, "SessionAgyChaude", _FakeAgyChaude)

    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}

        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        # Attend le 1er audio_debut + binaire AVANT de débloquer la suite du flux agy.
        recus_avant: list[tuple[str, Any]] = []
        for _ in range(15):
            item = _recevoir_message(ws)
            recus_avant.append(item)
            if item[0] == "bytes":
                break

        audios_avant = [val for kind, val in recus_avant if kind == "json" and val["type"] == "audio_debut"]
        assert len(audios_avant) == 1
        assert audios_avant[0]["seq"] == 0
        assert audios_avant[0]["phrase"] == "Voici la première phrase complète maintenant."
        assert not any(kind == "json" and val["type"] == "reponse_fin" for kind, val in recus_avant)

        debloquer_fin.set()

        recus_apres: list[tuple[str, Any]] = []
        for _ in range(15):
            item = _recevoir_message(ws)
            recus_apres.append(item)
            if item == ("json", {"type": "reponse_fin"}):
                break

        audios_apres = [val for kind, val in recus_apres if kind == "json" and val["type"] == "audio_debut"]
        assert len(audios_apres) == 1
        assert audios_apres[0]["seq"] == 1
        assert audios_apres[0]["phrase"] == "Et voici la deuxième phrase finale."
        assert recus_apres[-1] == ("json", {"type": "reponse_fin"})
        ws.send_json({"type": "fin"})

    assert len(prompts_recus) == 1
    premier_prompt = prompts_recus[0]
    assert "CONSIGNE VOCALE" in premier_prompt
    assert "Toujours tutoyer l'utilisateur." in premier_prompt
    assert "P" * 3000 in premier_prompt
    assert "P" * 3001 not in premier_prompt

    hist = client.get("/api/conversations/agent/jarvis/messages").json()
    msgs = hist["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["contenu"] == "Quelle heure est-il ?"
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["auteur"] == "jarvis"
    assert (
        msgs[1]["contenu"]
        == "Voici la première phrase complète maintenant. Et voici la deuxième phrase finale."
    )
    assert msgs[1]["meta"]["moteur"] == "agy"
    assert msgs[1]["meta"]["modele"] == "gemini-4-hydrogen-low"
    assert msgs[1]["meta"]["voie"] == "rapide"


def test_voie_rapide_agir_bascule_vers_pipeline_complet_sans_lire_marqueur(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une réponse ``[[AGIR]] ...`` n'est jamais lue à voix haute, dit « Je m'en occupe. » puis appelle demarrer_chat."""
    settings_store.ecrire({"voix": {"rapide": True}})
    monkeypatch.setattr(stream, "_obtenir_modele_vad", lambda: _vad_amplitude)
    monkeypatch.setattr(stt, "precharger", lambda: None)
    monkeypatch.setattr(
        stt,
        "transcrire_tableau",
        lambda audio, rapide=False: "Ajoute" if rapide else "Ajoute acheter du pain à mes tâches",
    )

    phrases_tts: list[str] = []

    async def _fake_synthetiser(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
        del voix
        phrases_tts.append(texte)
        tts_dir = jarvis_root / "runtime" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        cible = tts_dir / f"agir_{len(phrases_tts)}.mp3"
        cible.write_bytes(b"MP3")
        return cible

    monkeypatch.setattr(tts, "synthetiser", _fake_synthetiser)

    class _FakeAgyAgir:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.modele = "gemini-4-hydrogen-low"
            self.est_active = False
            self.tours_sur_processus = 0

        async def demarrer(self) -> None:
            self.est_active = True

        async def arreter(self) -> None:
            self.est_active = False

        async def envoyer(self, texte: str) -> AsyncIterator[str]:
            del texte
            yield "["
            yield "[AGIR]] Ajoute la tâche : "
            yield "acheter du pain."
            self.tours_sur_processus += 1

    monkeypatch.setattr(stream, "SessionAgyChaude", _FakeAgyAgir)

    consignes_chat: list[str] = []

    async def _fake_demarrer_chat(body: ChatRequest) -> str:
        consignes_chat.append(body.message)
        conv_id = f"{body.cible}:{body.id}"

        async def _emettre() -> None:
            await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": body.id})
            await hub.broadcast(
                "chat_delta",
                {
                    "conversation_id": conv_id,
                    "auteur": body.id,
                    "delta": "C'est noté, j'ai ajouté acheter du pain.",
                },
            )
            await hub.broadcast("chat_fin", {"conversation_id": conv_id, "auteur": body.id, "contenu": ""})

        asyncio.create_task(_emettre())
        return conv_id

    monkeypatch.setattr(chat, "demarrer_chat", _fake_demarrer_chat)

    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}

        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        recus: list[tuple[str, Any]] = []
        for _ in range(20):
            item = _recevoir_message(ws)
            recus.append(item)
            if item == ("json", {"type": "reponse_fin"}):
                break
        ws.send_json({"type": "fin"})

    assert consignes_chat == ["Ajoute la tâche : acheter du pain."]
    assert phrases_tts == [
        "Je m'en occupe.",
        "C'est noté, j'ai ajouté acheter du pain.",
    ]
    assert not any("AGIR" in p for p in phrases_tts)


def test_voie_rapide_repli_unique_si_agy_indisponible_et_erreur_si_tout_echoue(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repli automatique (1 seule fois/session) vers demarrer_chat si agy KO ; erreur explicite si rien ne marche."""
    settings_store.ecrire({"voix": {"rapide": True}})
    monkeypatch.setattr(stream, "_obtenir_modele_vad", lambda: _vad_amplitude)
    monkeypatch.setattr(stt, "precharger", lambda: None)
    monkeypatch.setattr(
        stt,
        "transcrire_tableau",
        lambda audio, rapide=False: "Test" if rapide else "Question test",
    )

    async def _fake_synthetiser(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
        del texte, voix
        tts_dir = jarvis_root / "runtime" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        cible = tts_dir / "repli.mp3"
        cible.write_bytes(b"MP3")
        return cible

    monkeypatch.setattr(tts, "synthetiser", _fake_synthetiser)

    tentatives_agy = {"n": 0}

    class _FakeAgyKO:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.modele = "gemini-4-hydrogen-low"
            self.est_active = False
            self.tours_sur_processus = 0

        async def demarrer(self) -> None:
            tentatives_agy["n"] += 1
            raise EngineError("CLI agy introuvable", transitoire=False)

        async def arreter(self) -> None:
            self.est_active = False

        async def envoyer(self, texte: str) -> AsyncIterator[str]:
            del texte
            if False:
                yield ""
            raise EngineError("CLI agy introuvable", transitoire=False)

    monkeypatch.setattr(stream, "SessionAgyChaude", _FakeAgyKO)

    tour_chat = {"n": 0}

    async def _fake_demarrer_chat(body: ChatRequest) -> str:
        tour_chat["n"] += 1
        conv_id = f"{body.cible}:{body.id}"
        if tour_chat["n"] <= 2:
            async def _ok() -> None:
                await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": body.id})
                await hub.broadcast(
                    "chat_delta",
                    {
                        "conversation_id": conv_id,
                        "auteur": body.id,
                        "delta": f"Réponse complète numéro {tour_chat['n']} prête.",
                    },
                )
                await hub.broadcast("chat_fin", {"conversation_id": conv_id, "auteur": body.id, "contenu": ""})

            asyncio.create_task(_ok())
            return conv_id

        raise EngineError("Aucun moteur disponible", transitoire=False, code="aucun_moteur")

    monkeypatch.setattr(chat, "demarrer_chat", _fake_demarrer_chat)

    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}

        # Tour 1 : agy KO -> bascule sur demarrer_chat
        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)
        for _ in range(20):
            if _recevoir_message(ws) == ("json", {"type": "reponse_fin"}):
                break

        # Tour 2 : voie rapide bloquée pour la session -> va directement au pipeline complet
        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)
        for _ in range(20):
            if _recevoir_message(ws) == ("json", {"type": "reponse_fin"}):
                break

        # Tour 3 : pipeline complet échoue aussi -> erreur explicite émise au client
        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        erreur_evt: dict[str, Any] | None = None
        for _ in range(15):
            kind, val = _recevoir_message(ws)
            if kind == "json" and val.get("type") == "erreur":
                erreur_evt = val
                break

        assert erreur_evt is not None
        assert erreur_evt["code"] == "aucun_moteur"
        assert "Aucun moteur" in erreur_evt["message"]
        ws.send_json({"type": "fin"})

    assert tentatives_agy["n"] == 1
    assert tour_chat["n"] == 3
