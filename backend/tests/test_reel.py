"""Test d'intégration réel (exclu par défaut, lancé avec ``pytest -m reel``)."""

from pathlib import Path

import pytest

from backend.llm import router
from backend.llm.base import LLMRequest


@pytest.mark.reel
async def test_reel_agy_hydrogen_pong(jarvis_root: Path) -> None:
    """Appelle réellement agy (Gemini 4 Hydrogen low) et vérifie la réponse PONG."""
    del jarvis_root
    res = await router.generer(
        LLMRequest(
            prompt="Réponds PONG",
            moteur="gemini",
            modele="hydrogen",
            effort="low",
            timeout_s=60,
        )
    )
    assert "PONG" in res.texte.upper()
