"""Tests unitaires et d'intégration du pipeline vocal temps réel (/ws/voix)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend import settings_store
from backend.api import chat
from backend.api.chat import ChatRequest
from backend.voice import stream, stt, tts
from backend.voice.stream import DecoupeurPhrases, DetecteurParole
from backend.ws_hub import hub


def _trames_pcm_20ms(amplitude: int, duree_ms: int) -> list[bytes]:
    """Découpe un signal PCM 16 kHz mono int16 LE d'amplitude constante en trames de 20 ms."""
    echantillons_par_trame = 320  # 20 ms à 16 kHz
    nb_trames = duree_ms // 20
    trame = np.full(echantillons_par_trame, amplitude, dtype="<i2").tobytes()
    return [trame for _ in range(nb_trames)]


def _vad_amplitude(fenetre: np.ndarray) -> float:
    """VAD simulé déterministe : voix (> 0.9) si amplitude crête > 0.15, sinon silence (0.05)."""
    return 0.92 if float(np.max(np.abs(fenetre))) > 0.15 else 0.05


def _recevoir_message(ws: Any) -> tuple[str, Any]:
    """Lit la prochaine trame WebSocket (JSON ou binaire) depuis ``TestClient``."""
    msg = ws.receive()
    if msg["type"] == "websocket.close":
        raise WebSocketDisconnect(code=msg.get("code", 1000), reason=msg.get("reason", ""))
    if msg.get("text") is not None:
        return ("json", json.loads(msg["text"]))
    return ("bytes", msg["bytes"])


async def test_hub_ecouteurs_sync_async_et_isolation_erreurs() -> None:
    """Vérifie ajouter_ecouteur/retirer_ecouteur (sync, async, erreur isolée)."""
    recus_sync: list[tuple[str, dict[str, Any]]] = []
    recus_async: list[tuple[str, dict[str, Any]]] = []

    def _ecouteur_sync(msg_type: str, data: dict[str, Any]) -> None:
        recus_sync.append((msg_type, data))

    async def _ecouteur_async(msg_type: str, data: dict[str, Any]) -> None:
        recus_async.append((msg_type, data))

    def _ecouteur_casse(msg_type: str, data: dict[str, Any]) -> None:
        del msg_type, data
        raise RuntimeError("Erreur simulée d'un écouteur")

    hub.ajouter_ecouteur(_ecouteur_casse)
    hub.ajouter_ecouteur(_ecouteur_sync)
    hub.ajouter_ecouteur(_ecouteur_async)

    try:
        await hub.broadcast("chat_delta", {"conversation_id": "agent:jarvis", "delta": "Salut"})
        await asyncio.sleep(0)

        assert recus_sync == [("chat_delta", {"conversation_id": "agent:jarvis", "delta": "Salut"})]
        assert recus_async == [("chat_delta", {"conversation_id": "agent:jarvis", "delta": "Salut"})]

        hub.retirer_ecouteur(_ecouteur_sync)
        hub.retirer_ecouteur(_ecouteur_async)
        hub.retirer_ecouteur(_ecouteur_casse)

        await hub.broadcast("chat_fin", {"conversation_id": "agent:jarvis"})
        await asyncio.sleep(0)
        assert len(recus_sync) == 1
        assert len(recus_async) == 1
    finally:
        hub.retirer_ecouteur(_ecouteur_sync)
        hub.retirer_ecouteur(_ecouteur_async)
        hub.retirer_ecouteur(_ecouteur_casse)


def test_stt_precharger_et_transcrire_tableau(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie precharger() et transcrire_tableau() en mode rapide (beam=1) et final (beam=5)."""
    del jarvis_root
    monkeypatch.setattr(stt, "cuda_disponible", lambda: False)
    appels: list[dict[str, Any]] = []

    class _FakeWhisper:
        def transcribe(
            self,
            audio: np.ndarray,
            *,
            language: str | None = None,
            beam_size: int = 5,
            vad_filter: bool = True,
            condition_on_previous_text: bool = True,
        ) -> tuple[list[Any], dict[str, Any]]:
            appels.append(
                {
                    "dtype": audio.dtype,
                    "ndim": audio.ndim,
                    "shape": audio.shape,
                    "premier": float(audio[0]),
                    "language": language,
                    "beam_size": beam_size,
                    "vad_filter": vad_filter,
                    "condition_on_previous_text": condition_on_previous_text,
                }
            )
            return [SimpleNamespace(text=" Bonjour "), SimpleNamespace(text="Jarvis ")], {}

    stt.set_modele_for_tests(_FakeWhisper())
    try:
        stt.precharger()
        assert stt.transcrire_tableau(np.zeros(0, dtype=np.float32), rapide=True) == ""
        assert appels == []

        txt_rapide = stt.transcrire_tableau(np.full(800, 0.25, dtype=np.float32), rapide=True)
        assert txt_rapide == "Bonjour Jarvis"
        assert appels[-1]["beam_size"] == 1
        assert appels[-1]["language"] == "fr"
        assert appels[-1]["vad_filter"] is False
        assert appels[-1]["condition_on_previous_text"] is False

        txt_final = stt.transcrire_tableau(np.array([16384, -16384], dtype=np.int16), rapide=False)
        assert txt_final == "Bonjour Jarvis"
        assert appels[-1]["beam_size"] == 5
        assert appels[-1]["dtype"] == np.float32
        assert appels[-1]["premier"] == pytest.approx(0.5, abs=1e-4)
    finally:
        stt.set_modele_for_tests(None)
        stt.liberer()


def test_decoupeur_phrases_abreviations_nombres_regroupement_et_code() -> None:
    """Vérifie le découpage pur : nombres, abréviations, regroupement < 25 car. et blocs code."""
    # 1. Nombres et abréviations (M., 1.5, ex., etc.) ne doivent pas provoquer de coupure.
    dec = DecoupeurPhrases()
    deltas = [
        "M. Dupont mesure 1.5 m, ex. des cadres, etc. et ",
        "il arrive bientôt. ",
        "Voici la seconde phrase complète pour vérifier.",
    ]
    emis: list[str] = []
    for delta in deltas:
        emis.extend(dec.alimenter(delta))
    emis.extend(dec.terminer())

    assert emis == [
        "M. Dupont mesure 1.5 m, ex. des cadres, etc. et il arrive bientôt.",
        "Voici la seconde phrase complète pour vérifier.",
    ]

    # 2. Regroupement des phrases de moins de 25 caractères avec la suivante.
    dec2 = DecoupeurPhrases()
    assert dec2.alimenter("Oui. ") == []
    assert dec2.alimenter("Très bien. ") == []
    out = dec2.alimenter("Voici la réponse complète attendue. ")
    assert out == ["Oui. Très bien. Voici la réponse complète attendue."]
    assert dec2.terminer() == []

    # Réponse courte unique tout de même émise à terminer().
    assert dec2.alimenter("Bonjour !") == []
    assert dec2.terminer() == ["Bonjour !"]

    # 3. Markdown nettoyé et blocs de code ignorés (y compris balise coupée entre deltas).
    # "Voici un exemple simple :" fait 25 car. (>= 25) et se termine par ":\n" :
    # il doit être émis immédiatement selon ARCHITECTURE-VOIX.md §2.
    dec3 = DecoupeurPhrases()
    flux = [
        "Voici **un exemple** simple :\n",
        "``",
        "`python\nprint('1. ne pas lire')\n``",
        "`\n",
        "Consulte [la doc](https://exemple.fr) pour finir.",
    ]
    phrases_md: list[str] = []
    for morceau in flux:
        phrases_md.extend(dec3.alimenter(morceau))
    phrases_md.extend(dec3.terminer())

    assert phrases_md == [
        "Voici un exemple simple :",
        "Consulte la doc pour finir.",
    ]
    texte_joint = " ".join(phrases_md)
    assert "print" not in texte_joint
    assert "bloc de code" not in texte_joint
    assert "**" not in texte_joint
    assert "https://exemple.fr" not in texte_joint


def test_detecteur_parole_debut_fin_preroll_et_coupe_30s() -> None:
    """Vérifie le VAD en flux : début >= 200 ms, pré-roll 300 ms, fin 700 ms et coupe 30 s."""
    det = DetecteurParole(vad=_vad_amplitude)

    # 512 ms (16 * 512 éch.) de bruit de fond faible (0.05 -> silence VAD) pour remplir le pré-roll
    # sans reliquat partiel avant les fenêtres de voix alignées sur 512 échantillons.
    preroll_signal = np.full(16 * 512, 0.05, dtype=np.float32)
    assert det.alimenter(preroll_signal) == []
    assert not det.parole_active

    # 160 ms de voix (< 200 ms) : pas encore de début.
    voix_courte = np.full(5 * 512, 0.5, dtype=np.float32)  # 160 ms
    assert det.alimenter(voix_courte) == []
    assert not det.parole_active

    # + 64 ms de voix (total 224 ms >= 200 ms) : déclenche "debut".
    voix_suite = np.full(2 * 512, 0.5, dtype=np.float32)
    evts_debut = det.alimenter(voix_suite)
    assert len(evts_debut) == 1
    assert evts_debut[0].type == "debut"
    assert det.parole_active

    # 512 ms de silence (< 700 ms) : toujours en parole.
    silence_partiel = np.zeros(16 * 512, dtype=np.float32)
    assert det.alimenter(silence_partiel) == []
    assert det.parole_active

    # + 256 ms de silence (total 768 ms >= 700 ms) : déclenche "fin" avec pré-roll 300 ms.
    silence_fin = np.zeros(8 * 512, dtype=np.float32)
    evts_fin = det.alimenter(silence_fin)
    assert len(evts_fin) == 1
    assert evts_fin[0].type == "fin"
    assert evts_fin[0].raison == "silence"
    assert evts_fin[0].audio is not None
    assert not det.parole_active

    audio_recu = evts_fin[0].audio
    # Les premiers 4800 échantillons (300 ms à 16 kHz) doivent contenir le signal de pré-roll (0.05).
    assert np.allclose(audio_recu[:4800], 0.05, atol=1e-5)
    # Suivis des 7 * 512 échantillons de voix (0.5).
    assert np.allclose(audio_recu[4800 : 4800 + 7 * 512], 0.5, atol=1e-5)

    # Test de coupe forcée à 30 s d'énoncé continu.
    det.reinitialiser()
    evts_30s: list[Any] = []
    bloc_1s = np.full(16000, 0.5, dtype=np.float32)
    for _ in range(31):
        evts_30s.extend(det.alimenter(bloc_1s))

    fins = [e for e in evts_30s if e.type == "fin"]
    assert any(e.raison == "duree_max" for e in fins)
    fin_max = next(e for e in fins if e.raison == "duree_max")
    assert fin_max.audio is not None
    assert fin_max.audio.size == 30 * 16000


def test_ws_voix_session_complete_ordre_et_evenements(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teste une conversation complète via /ws/voix : pret, partiel, final, TTS ordonné et reponse_fin."""
    settings_store.ecrire({"voix": {"rapide": False}})
    monkeypatch.setattr(stream, "_obtenir_modele_vad", lambda: _vad_amplitude)

    appels_stt: list[bool] = []
    monkeypatch.setattr(stt, "precharger", lambda: None)

    def _fake_transcrire_tableau(audio: np.ndarray, rapide: bool = False) -> str:
        del audio
        appels_stt.append(rapide)
        return "Bonjour Jar" if rapide else "Bonjour Jarvis"

    monkeypatch.setattr(stt, "transcrire_tableau", _fake_transcrire_tableau)

    async def _fake_synthetiser(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
        del voix
        tts_dir = jarvis_root / "runtime" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        idx = 0 if "Première" in texte else 1
        ext = "mp3" if idx == 0 else "wav"
        cible = tts_dir / f"phrase_{idx}.{ext}"
        cible.write_bytes(f"AUDIO-{idx}:{texte}".encode())
        return cible

    monkeypatch.setattr(tts, "synthetiser", _fake_synthetiser)

    async def _fake_demarrer_chat(body: ChatRequest) -> str:
        conv_id = f"{body.cible}:{body.id}"

        async def _emettre() -> None:
            await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": body.id})
            await hub.broadcast(
                "chat_delta",
                {
                    "conversation_id": conv_id,
                    "auteur": body.id,
                    "delta": "Première phrase suffisamment longue. ",
                },
            )
            await hub.broadcast(
                "chat_delta",
                {
                    "conversation_id": conv_id,
                    "auteur": body.id,
                    "delta": "Deuxième phrase tout aussi claire.",
                },
            )
            await hub.broadcast(
                "chat_fin",
                {"conversation_id": conv_id, "auteur": body.id, "contenu": ""},
            )

        asyncio.create_task(_emettre())
        return conv_id

    monkeypatch.setattr(chat, "demarrer_chat", _fake_demarrer_chat)

    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}
        ws.send_json({"type": "config", "agent": "jarvis", "voix": "fr-FR-DeniseNeural"})

        # Envoie 280 ms de voix puis attend parole_debut et partiel avant le silence.
        for trame in _trames_pcm_20ms(12000, 280):
            ws.send_bytes(trame)

        recus: list[tuple[str, Any]] = []
        for _ in range(10):
            item = _recevoir_message(ws)
            recus.append(item)
            types_courants = [val["type"] for kind, val in recus if kind == "json"]
            if "parole_debut" in types_courants and "partiel" in types_courants:
                break

        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        for _ in range(25):
            item = _recevoir_message(ws)
            recus.append(item)
            if item == ("json", {"type": "reponse_fin"}):
                break

        types_json = [val["type"] for kind, val in recus if kind == "json"]
        assert "parole_debut" in types_json
        assert "partiel" in types_json
        assert "final" in types_json
        assert "audio_debut" in types_json
        assert types_json[-1] == "reponse_fin"

        evt_final = next(val for kind, val in recus if kind == "json" and val["type"] == "final")
        assert evt_final["texte"] == "Bonjour Jarvis"

        # Vérifie que chaque audio_debut est immédiatement suivi de sa trame binaire dans l'ordre seq 0, 1.
        paires_audio: list[tuple[dict[str, Any], bytes]] = []
        for i, (kind, val) in enumerate(recus):
            if kind == "json" and val.get("type") == "audio_debut":
                assert i + 1 < len(recus)
                kind_next, bytes_next = recus[i + 1]
                assert kind_next == "bytes"
                paires_audio.append((val, bytes_next))

        assert len(paires_audio) == 2
        assert paires_audio[0][0]["seq"] == 0
        assert paires_audio[0][0]["mime"] == "audio/mpeg"
        assert paires_audio[0][1].startswith(b"AUDIO-0:Premi")
        assert paires_audio[1][0]["seq"] == 1
        assert paires_audio[1][0]["mime"] == "audio/wav"
        assert paires_audio[1][1].startswith(b"AUDIO-1:Deuxi")

        ws.send_json({"type": "fin"})


def test_ws_voix_barge_in_et_interrompre(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le barge-in pendant la lecture (stop_audio + file vidée) et la commande interrompre."""
    settings_store.ecrire({"voix": {"rapide": False}})
    monkeypatch.setattr(stream, "_obtenir_modele_vad", lambda: _vad_amplitude)
    monkeypatch.setattr(stt, "precharger", lambda: None)
    monkeypatch.setattr(stt, "transcrire_tableau", lambda audio, rapide=False: "Question" if not rapide else "Quest")

    syntheses: list[str] = []
    debloquer_phrase_1: asyncio.Event | None = None

    async def _fake_synthetiser(texte: str, voix: str = tts.VOIX_DEFAUT) -> Path:
        del voix
        syntheses.append(texte)
        tts_dir = jarvis_root / "runtime" / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)
        cible = tts_dir / f"barge_{len(syntheses)}.mp3"
        cible.write_bytes(b"MP3-DATA")
        if len(syntheses) >= 2 and debloquer_phrase_1 is not None:
            await debloquer_phrase_1.wait()
        return cible

    monkeypatch.setattr(tts, "synthetiser", _fake_synthetiser)

    async def _fake_demarrer_chat(body: ChatRequest) -> str:
        conv_id = f"{body.cible}:{body.id}"

        async def _emettre() -> None:
            await hub.broadcast("chat_debut", {"conversation_id": conv_id, "auteur": body.id})
            await hub.broadcast(
                "chat_delta",
                {
                    "conversation_id": conv_id,
                    "auteur": body.id,
                    "delta": (
                        "Première phrase de réponse suffisamment longue. "
                        "Deuxième phrase qui doit être annulée. "
                        "Troisième phrase qui ne doit jamais partir."
                    ),
                },
            )
            await hub.broadcast("chat_fin", {"conversation_id": conv_id, "auteur": body.id, "contenu": ""})

        asyncio.create_task(_emettre())
        return conv_id

    monkeypatch.setattr(chat, "demarrer_chat", _fake_demarrer_chat)

    # 1. Test barge-in par détection vocale (>= 300 ms) pendant la synthèse de la phrase 2.
    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}
        debloquer_phrase_1 = asyncio.Event()

        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        # Consomme jusqu'au premier audio_debut + sa trame binaire.
        premier_audio_recu = False
        for _ in range(20):
            kind, _val = _recevoir_message(ws)
            if kind == "bytes":
                premier_audio_recu = True
                break
        assert premier_audio_recu

        # Parle >= 300 ms (360 ms) pendant la réponse -> doit déclencher stop_audio et parole_debut.
        for trame in _trames_pcm_20ms(12000, 360):
            ws.send_bytes(trame)

        apres_barge: list[dict[str, Any]] = []
        for _ in range(20):
            kind, val = _recevoir_message(ws)
            if kind == "json":
                apres_barge.append(val)
                types = [m["type"] for m in apres_barge]
                if "stop_audio" in types and "parole_debut" in types:
                    break

        types_apres = [m["type"] for m in apres_barge]
        assert "stop_audio" in types_apres
        assert "parole_debut" in types_apres
        assert "audio_debut" not in types_apres
        assert len(syntheses) <= 2

    # 2. Test de la commande JSON {"type": "interrompre"}.
    syntheses.clear()
    with client.websocket_connect(f"/ws/voix?token={token}") as ws:
        assert ws.receive_json() == {"type": "pret"}
        debloquer_phrase_1 = asyncio.Event()

        for trame in _trames_pcm_20ms(12000, 260):
            ws.send_bytes(trame)
        for trame in _trames_pcm_20ms(0, 800):
            ws.send_bytes(trame)

        for _ in range(20):
            kind, _val = _recevoir_message(ws)
            if kind == "bytes":
                break

        ws.send_json({"type": "interrompre"})
        evts_stop: list[dict[str, Any]] = []
        for _ in range(20):
            kind, val = _recevoir_message(ws)
            if kind == "json":
                evts_stop.append(val)
                if val["type"] == "stop_audio":
                    break

        assert any(m["type"] == "stop_audio" for m in evts_stop)
        assert not any(m["type"] == "audio_debut" for m in evts_stop)
        assert len(syntheses) <= 2
        ws.send_json({"type": "fin"})


def test_ws_voix_jeton_invalide_refuse_et_remplacement_session(
    client: TestClient,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie le refus 4401 sur jeton invalide/absent et la fermeture 4000 de l'ancienne session."""
    settings_store.ecrire({"voix": {"rapide": False}})
    monkeypatch.setattr(stt, "precharger", lambda: None)

    with pytest.raises(WebSocketDisconnect) as exc_absent:
        with client.websocket_connect("/ws/voix"):
            pass
    assert exc_absent.value.code == 4401

    with pytest.raises(WebSocketDisconnect) as exc_invalide:
        with client.websocket_connect("/ws/voix?token=mauvais-jeton"):
            pass
    assert exc_invalide.value.code == 4401

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/ws/voix?token={token}",
            headers={"Origin": "https://attaquant.example"},
        ):
            pass

    with client.websocket_connect(f"/ws/voix?token={token}") as ws1:
        assert ws1.receive_json() == {"type": "pret"}

        with client.websocket_connect(f"/ws/voix?token={token}") as ws2:
            assert ws2.receive_json() == {"type": "pret"}

            with pytest.raises(WebSocketDisconnect) as exc_remplacee:
                ws1.receive_json()
            assert exc_remplacee.value.code == 4000

            ws2.send_json({"type": "fin"})


@pytest.mark.reel
async def test_reel_ws_voix_edge_tts_vers_whisper(
    client: TestClient,
    jarvis_root: Path,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Génère un vrai audio edge-tts, le découpe en trames de 20 ms et vérifie « Jarvis » dans final."""
    settings_store.ecrire({"voix": {"rapide": False}})
    import edge_tts
    from faster_whisper.audio import decode_audio

    chemin_mp3 = jarvis_root / "runtime" / "reel_phrase.mp3"
    chemin_mp3.parent.mkdir(parents=True, exist_ok=True)
    await edge_tts.Communicate("Bonjour Jarvis, comment vas-tu ?", tts.VOIX_DEFAUT).save(str(chemin_mp3))

    audio_f32 = decode_audio(str(chemin_mp3), sampling_rate=16000)
    pcm_int16 = np.clip(audio_f32 * 32767.0, -32768, 32767).astype("<i2")
    silence_debut = np.zeros(int(0.32 * 16000), dtype="<i2")
    silence_fin = np.zeros(int(1.0 * 16000), dtype="<i2")
    flux = np.concatenate([silence_debut, pcm_int16, silence_fin]).tobytes()

    async def _fake_demarrer_chat(body: ChatRequest) -> str:
        conv_id = f"{body.cible}:{body.id}"
        await hub.broadcast("chat_fin", {"conversation_id": conv_id, "auteur": body.id, "contenu": ""})
        return conv_id

    monkeypatch.setattr(chat, "demarrer_chat", _fake_demarrer_chat)

    try:
        with client.websocket_connect(f"/ws/voix?token={token}") as ws:
            assert ws.receive_json() == {"type": "pret"}

            taille_trame = 640  # 20 ms à 16 kHz mono int16
            for debut in range(0, len(flux), taille_trame):
                morceau = flux[debut : debut + taille_trame]
                if len(morceau) % 2 == 0 and morceau:
                    ws.send_bytes(morceau)

            final_texte = ""
            for _ in range(60):
                kind, val = _recevoir_message(ws)
                if kind == "json" and val.get("type") == "final":
                    final_texte = str(val.get("texte") or "")
                    break

            assert "jarvis" in final_texte.lower()
            ws.send_json({"type": "fin"})
    finally:
        stt.liberer()
