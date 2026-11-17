"""Gestion du serveur TTS local Kyutai (voix naturelle FR/EN, GPU, hors ligne).

Le serveur (``scripts/tts_kyutai_server.py``) tourne dans un venv dédié (torch CUDA + moshi),
installé hors de l'application par ``scripts/installer_voix_locale.ps1`` (défaut ``G:/ai-cli/kyutai-tts``,
surchargeable par la variable ``JARVIS_KYUTAI_DIR``). Jarvis le démarre à la demande ; tant qu'il n'est pas
prêt, la synthèse retombe sur ``edge-tts``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from backend import config

logger = logging.getLogger(__name__)

URL_KYUTAI = "http://127.0.0.1:8880"
DOSSIER_DEFAUT = Path("G:/ai-cli/kyutai-tts")
DELAI_DEMARRAGE_S = 180.0

_verrou_demarrage = asyncio.Lock()
_dernier_lancement = 0.0


def dossier_kyutai() -> Path:
    """Dossier du venv Kyutai (``JARVIS_KYUTAI_DIR`` ou ``G:/ai-cli/kyutai-tts``)."""
    brut = os.environ.get("JARVIS_KYUTAI_DIR", "").strip()
    return Path(brut) if brut else DOSSIER_DEFAUT


def _python_kyutai() -> Path:
    return dossier_kyutai() / "Scripts" / "python.exe"


def installe() -> bool:
    """Vrai si le venv Kyutai et le script serveur sont présents."""
    return _python_kyutai().is_file() and (config.APP_DIR / "scripts" / "tts_kyutai_server.py").is_file()


async def pret(url: str = URL_KYUTAI, *, transport: httpx.AsyncBaseTransport | None = None) -> bool:
    """Vrai si le serveur répond sur ``/health``."""
    try:
        async with httpx.AsyncClient(timeout=1.5, transport=transport) as client:
            rep = await client.get(f"{url}/health")
            return rep.status_code == 200
    except Exception:
        return False


def _lancer_processus() -> None:
    """Lance le serveur sans fenêtre, détaché, journal dans ``RUNTIME/kyutai.log``."""
    env = dict(os.environ)
    env["NO_TORCH_COMPILE"] = "1"
    env.setdefault("HF_HOME", str(dossier_kyutai().parent / "hf-cache"))
    for cle in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        env.pop(cle, None)
    journal = config.RUNTIME / "kyutai.log"
    journal.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with journal.open("ab") as sortie:
        subprocess.Popen(  # noqa: S603 - binaire local contrôlé
            [str(_python_kyutai()), str(config.APP_DIR / "scripts" / "tts_kyutai_server.py")],
            stdout=sortie,
            stderr=sortie,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=flags,
        )


async def assurer_demarre() -> bool:
    """Démarre le serveur si besoin (une seule tentative toutes les 60 s) sans attendre qu'il soit prêt.

    Returns:
        ``True`` si le serveur répond déjà ; ``False`` sinon (démarrage lancé ou impossible).
    """
    global _dernier_lancement
    if await pret():
        return True
    if not installe():
        return False
    async with _verrou_demarrage:
        if await pret():
            return True
        if time.monotonic() - _dernier_lancement < 60.0:
            return False
        _dernier_lancement = time.monotonic()
        try:
            await asyncio.to_thread(_lancer_processus)
            logger.info("Serveur TTS Kyutai lancé (chargement du modèle en cours).")
        except OSError as exc:
            logger.warning("Impossible de lancer le serveur TTS Kyutai : %s", exc)
    return False
