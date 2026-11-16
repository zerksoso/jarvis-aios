"""Tests du routeur LLM avec moteurs et quotas simulés."""

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from backend import db
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, EngineError, LLMRequest, LLMResult


class MoteurSimule:
    """Moteur simulé programmable pour tester les scénarios de routage."""

    def __init__(
        self,
        nom: str,
        handler: Callable[
            [LLMRequest, str, str | None, DeltaCallback],
            LLMResult | Awaitable[LLMResult],
        ]
        | None = None,
        dispo: tuple[bool, str] = (True, "OK"),
    ) -> None:
        """Initialise le moteur simulé avec un handler et une disponibilité donnés."""
        self.nom = nom
        self._handler = handler
        self._dispo = dispo
        self.appels: list[tuple[str, str | None]] = []

    async def disponible(self) -> tuple[bool, str]:
        """Retourne l'état de disponibilité simulé."""
        return self._dispo

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Exécute le handler simulé et enregistre la trace d'appel."""
        self.appels.append((modele_id, session_id))
        if self._handler is None:
            return LLMResult(texte="ok", moteur=self.nom, modele=modele_id, session_id="sess-default")

        res = self._handler(req, modele_id, session_id, on_delta)
        if inspect.isawaitable(res):
            return await res
        return res


def _quotas_pleins() -> dict[str, object]:
    """Construit un jeu de quotas confortables (> 10 %) pour tous les groupes."""
    return {
        "buckets": {
            "gemini-5h": {"group": "gemini", "remaining": 0.90},
            "gemini-weekly": {"group": "gemini", "remaining": 0.90},
            "3p-5h": {"group": "3p", "remaining": 0.90},
            "3p-weekly": {"group": "3p", "remaining": 0.90},
            "gemini-argon-wtu-weekly": {"group": "argon", "remaining": 0.90},
        }
    }


async def test_succes_direct(jarvis_root: Path) -> None:
    """Le premier maillon (hydrogen) réussit directement sans bascule et trace usage_llm."""
    del jarvis_root
    quota.set_quotas_for_tests(_quotas_pleins())

    async def _ok(
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, session_id, on_delta
        return LLMResult(
            texte="Réponse directe",
            moteur="agy",
            modele=modele_id,
            session_id="s-1",
            input_tokens=100,
            output_tokens=20,
        )

    engines: dict[str, Engine] = {
        "agy": MoteurSimule("agy", _ok),
        "claude_cli": MoteurSimule("claude_cli"),
    }
    res = await router.generer(LLMRequest(prompt="Salut"), engines=engines)
    assert res.texte == "Réponse directe"
    assert res.modele == "hydrogen"
    assert res.moteur == "agy"
    assert res.bascules == []

    lignes = db.query("SELECT moteur, modele, ok, input_tokens, output_tokens FROM usage_llm")
    assert len(lignes) == 1
    assert lignes[0] == {
        "moteur": "agy",
        "modele": "hydrogen",
        "ok": 1,
        "input_tokens": 100,
        "output_tokens": 20,
    }


async def test_bascule_sur_quota_bas(jarvis_root: Path) -> None:
    """Saute hydrogen et flash si le groupe gemini < 10 % et bascule vers claude-sonnet."""
    del jarvis_root
    quotas = _quotas_pleins()
    quotas["buckets"]["gemini-5h"] = {"group": "gemini", "remaining": 0.05}  # type: ignore[index]
    quota.set_quotas_for_tests(quotas)

    agy = MoteurSimule("agy")
    engines: dict[str, Engine] = {
        "agy": agy,
        "claude_cli": MoteurSimule("claude_cli"),
    }

    res = await router.generer(LLMRequest(prompt="Test quota"), engines=engines)
    assert res.modele == "claude-sonnet"
    assert agy.appels == [("claude-sonnet", None)]
    assert any("hydrogen: quota < 10 %" in b for b in res.bascules)
    assert any("flash: quota < 10 %" in b for b in res.bascules)


async def test_relance_sur_erreur_transitoire_puis_succes(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relance le même maillon après une erreur transitoire et réussit au 2e essai."""
    del jarvis_root
    monkeypatch.setattr(router, "DELAIS_RELANCE", (0.0, 0.0))
    quota.set_quotas_for_tests(_quotas_pleins())

    compteur = {"n": 0}

    async def _transitoire_puis_ok(
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, session_id, on_delta
        compteur["n"] += 1
        if compteur["n"] == 1:
            raise EngineError("Eligibility check failed: unexpected EOF", transitoire=True)
        return LLMResult(texte="Succès après relance", moteur="agy", modele=modele_id)

    agy = MoteurSimule("agy", _transitoire_puis_ok)
    engines: dict[str, Engine] = {"agy": agy, "claude_cli": MoteurSimule("claude_cli")}

    res = await router.generer(LLMRequest(prompt="Relance"), engines=engines)
    assert res.texte == "Succès après relance"
    assert res.modele == "hydrogen"
    assert res.bascules == []
    assert len(agy.appels) == 2

    lignes = db.query("SELECT ok, erreur FROM usage_llm ORDER BY id ASC")
    assert len(lignes) == 2
    assert lignes[0]["ok"] == 0
    assert lignes[1]["ok"] == 1


async def test_bascule_sur_erreur_non_transitoire(jarvis_root: Path) -> None:
    """Une erreur non transitoire fait immédiatement passer au maillon suivant."""
    del jarvis_root
    quota.set_quotas_for_tests(_quotas_pleins())

    async def _echoue_sur_hydrogen(
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, session_id, on_delta
        if modele_id == "hydrogen":
            raise EngineError("Erreur modèle fatale", transitoire=False)
        return LLMResult(texte="Repli flash", moteur="agy", modele=modele_id)

    agy = MoteurSimule("agy", _echoue_sur_hydrogen)
    engines: dict[str, Engine] = {"agy": agy, "claude_cli": MoteurSimule("claude_cli")}

    res = await router.generer(LLMRequest(prompt="Test repli"), engines=engines)
    assert res.modele == "flash"
    assert res.texte == "Repli flash"
    assert agy.appels == [("hydrogen", None), ("flash", None)]
    assert res.bascules == ["hydrogen: Erreur modèle fatale"]


async def test_argon_saute_sous_10_pourcent_meme_choisi(jarvis_root: Path) -> None:
    """Argon est sauté sous 10 % de quota même lorsqu'il est explicitement demandé."""
    del jarvis_root
    quotas = _quotas_pleins()
    quotas["buckets"]["gemini-argon-wtu-weekly"] = {"group": "argon", "remaining": 0.04}  # type: ignore[index]
    quota.set_quotas_for_tests(quotas)

    agy = MoteurSimule("agy")
    engines: dict[str, Engine] = {"agy": agy, "claude_cli": MoteurSimule("claude_cli")}

    res = await router.generer(
        LLMRequest(prompt="Utilise argon", moteur="gemini", modele="argon"),
        engines=engines,
    )
    assert res.modele == "hydrogen"
    assert agy.appels == [("hydrogen", None)]
    assert res.bascules == ["argon: quota < 10 %"]


async def test_continuite_session_enregistree_et_reutilisee(jarvis_root: Path) -> None:
    """Enregistre session_id au 1er tour et le réutilise au 2e, avec repli sans session si inconnu."""
    del jarvis_root
    quota.set_quotas_for_tests(_quotas_pleins())

    tours = {"n": 0}

    async def _sessions(
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, on_delta
        tours["n"] += 1
        if tours["n"] == 1:
            assert session_id is None
            return LLMResult(texte="Tour 1", moteur="agy", modele=modele_id, session_id="sess-001")
        if tours["n"] == 2:
            assert session_id == "sess-001"
            return LLMResult(texte="Tour 2", moteur="agy", modele=modele_id, session_id="sess-001")
        if tours["n"] == 3:
            # Simule une session expirée/inconnue puis reprise sans session au tour 4.
            assert session_id == "sess-001"
            raise EngineError("Session inconnue", transitoire=False)
        assert session_id is None
        return LLMResult(texte="Tour 3 récupéré", moteur="agy", modele=modele_id, session_id="sess-002")

    agy = MoteurSimule("agy", _sessions)
    engines: dict[str, Engine] = {"agy": agy, "claude_cli": MoteurSimule("claude_cli")}

    r1 = await router.generer(LLMRequest(prompt="Un", session_cle="agent:jarvis"), engines=engines)
    assert r1.session_id == "sess-001"

    r2 = await router.generer(LLMRequest(prompt="Deux", session_cle="agent:jarvis"), engines=engines)
    assert r2.texte == "Tour 2"

    r3 = await router.generer(LLMRequest(prompt="Trois", session_cle="agent:jarvis"), engines=engines)
    assert r3.texte == "Tour 3 récupéré"
    assert r3.session_id == "sess-002"

    ligne = db.query_one(
        "SELECT session_id FROM sessions_moteur WHERE cle = ? AND modele = ?",
        ("agent:jarvis", "hydrogen"),
    )
    assert ligne == {"session_id": "sess-002"}


async def test_deltas_transmis_dans_ordre(jarvis_root: Path) -> None:
    """Vérifie que chaque delta streamé par le moteur est transmis dans l'ordre exact."""
    del jarvis_root
    quota.set_quotas_for_tests(_quotas_pleins())
    morceaux = ["Bon", "jour", " ", "Jarvis", "!"]

    async def _stream(
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del req, session_id
        for fragment in morceaux:
            if on_delta is not None:
                await on_delta(fragment)
        return LLMResult(texte="".join(morceaux), moteur="agy", modele=modele_id)

    engines: dict[str, Engine] = {"agy": MoteurSimule("agy", _stream)}
    recus: list[str] = []

    async def _collecte(delta: str) -> None:
        recus.append(delta)

    res = await router.generer(LLMRequest(prompt="Stream"), on_delta=_collecte, engines=engines)
    assert recus == morceaux
    assert res.texte == "Bonjour Jarvis!"
