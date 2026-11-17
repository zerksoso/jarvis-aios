"""Tests de la voie rapide LLM local et du moteur TTS Kyutai (tout est simulé, aucun GPU)."""

from __future__ import annotations

import json

import httpx
import pytest

from backend import settings_store
from backend.llm.base import EngineError
from backend.llm.llm_local_session import SessionLLMLocale
from backend.voice import kyutai, tts


def _sse(*morceaux: str) -> bytes:
    lignes = [
        "data: " + json.dumps({"choices": [{"delta": {"content": m}}]}) for m in morceaux
    ]
    return ("\n\n".join([*lignes, "data: [DONE]"]) + "\n\n").encode()


def _transport(corps_recus: list[dict]) -> httpx.MockTransport:
    def handler(requete: httpx.Request) -> httpx.Response:
        if requete.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "text-embedding-x"}, {"id": "qwen3-4b"}]})
        corps_recus.append(json.loads(requete.content))
        return httpx.Response(200, content=_sse("Bon", "jour."), headers={"content-type": "text/event-stream"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_session_llm_locale_stream_et_historique() -> None:
    recus: list[dict] = []
    session = SessionLLMLocale(transport=_transport(recus))
    await session.demarrer()
    assert session.est_active
    assert session.modele == "qwen3-4b"  # l'embedding est ignoré

    premier = "CONSIGNE VOCALE :\nSois bref.\n\nDEMANDE UTILISATEUR :\nSalut"
    texte = "".join([d async for d in session.envoyer(premier)])
    assert texte == "Bonjour."
    assert session.tours_sur_processus == 1
    assert recus[0]["messages"][0] == {"role": "system", "content": "CONSIGNE VOCALE :\nSois bref."}
    assert recus[0]["messages"][-1] == {"role": "user", "content": "Salut"}

    _ = [d async for d in session.envoyer("Et demain ?")]
    roles = [m["role"] for m in recus[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


@pytest.mark.asyncio
async def test_session_llm_locale_serveur_absent() -> None:
    def refus(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refusé")

    session = SessionLLMLocale(transport=httpx.MockTransport(refus))
    with pytest.raises(EngineError):
        await session.demarrer()
    assert not session.est_active


@pytest.mark.asyncio
async def test_session_llm_locale_modele_inconnu() -> None:
    session = SessionLLMLocale(modele="absent", transport=_transport([]))
    with pytest.raises(EngineError):
        await session.demarrer()


def test_reglages_llm_rapide_validation() -> None:
    defauts = settings_store._defauts_dynamiques()["voix"]
    brut = {"llm_rapide": "nimporte", "llm_local_url": "ftp://x", "llm_local_modele": 3}
    valide = settings_store._normaliser_bloc_voix(brut, defauts)
    assert valide["llm_rapide"] == "agy"
    assert valide["llm_local_url"] == "http://127.0.0.1:1234/v1"
    assert valide["llm_local_modele"] == ""
    ok = settings_store._normaliser_bloc_voix({"llm_rapide": "LOCAL", "moteur_tts": "kyutai"}, defauts)
    assert ok["llm_rapide"] == "local"
    assert ok["moteur_tts"] == "kyutai"


@pytest.mark.asyncio
async def test_tts_kyutai_pret_utilise_le_serveur_local(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    appels: list[str] = []

    async def pret_oui() -> bool:
        return True

    def handler(requete: httpx.Request) -> httpx.Response:
        appels.append(str(requete.url))
        return httpx.Response(200, content=b"RIFFfakewav", headers={"content-type": "audio/wav"})

    monkeypatch.setattr(kyutai, "assurer_demarre", pret_oui)
    monkeypatch.setattr(tts.config, "RUNTIME", tmp_path)
    chemin = await tts.synthetiser(
        "Bonjour tout le monde.", "defaut", moteur_tts="kyutai", transport=httpx.MockTransport(handler)
    )
    assert chemin.suffix == ".wav"
    assert appels == ["http://127.0.0.1:8880/v1/audio/speech"]


@pytest.mark.asyncio
async def test_tts_kyutai_pas_pret_replie_sur_edge(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    async def pret_non() -> bool:
        return False

    async def faux_edge(self, chemin: str) -> None:  # noqa: ANN001
        from pathlib import Path

        Path(chemin).write_bytes(b"mp3")

    monkeypatch.setattr(kyutai, "assurer_demarre", pret_non)
    monkeypatch.setattr(tts.config, "RUNTIME", tmp_path)
    monkeypatch.setattr(tts.edge_tts.Communicate, "save", faux_edge)
    chemin = await tts.synthetiser("Bonjour tout le monde.", "defaut", moteur_tts="kyutai")
    assert chemin.suffix == ".mp3"


@pytest.mark.asyncio
async def test_kyutai_pret_hors_ligne() -> None:
    def refus(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("non")

    assert not await kyutai.pret(transport=httpx.MockTransport(refus))
