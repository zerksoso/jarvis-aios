"""Fixtures pytest isolant ``JARVIS_ROOT`` dans un dossier temporaire par test."""

import atexit
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Protège le vrai workspace dès l'import des modules backend pendant la collecte pytest.
_BOOT_ROOT = Path(tempfile.mkdtemp(prefix="jarvis-pytest-boot-"))
os.environ.setdefault("JARVIS_TEST", "1")
os.environ.setdefault("JARVIS_ROOT", str(_BOOT_ROOT))
atexit.register(lambda: shutil.rmtree(_BOOT_ROOT, ignore_errors=True))

from backend import config, db, security  # noqa: E402
from backend.agents import agent_runtime  # noqa: E402
from backend.api import chat, setup, voix_ws  # noqa: E402
from backend.jarvis_engine import approvals, teacher  # noqa: E402
from backend.llm import agy_engine, claude_engine, quota, router  # noqa: E402
from backend.logging_setup import shutdown_logging  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.update import service as maj_service  # noqa: E402
from backend.ws_hub import hub  # noqa: E402


@pytest.fixture
def jarvis_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fournit une racine ``JARVIS_ROOT`` et un ``APP_DIR`` isolés et réinitialise les singletons."""
    ancien_app_dir = config.APP_DIR
    ancien_root = config.ROOT
    monkeypatch.setenv("JARVIS_TEST", "1")
    monkeypatch.setenv("JARVIS_ROOT", str(tmp_path))
    config.refresh_paths(tmp_path, app_dir=tmp_path)
    config.ensure_dirs()
    db.init_db()

    quota.set_quotas_for_tests(None)
    router.set_engines_for_tests(None)
    agy_engine.reset_etat_pour_tests()
    claude_engine.reset_etat_pour_tests()
    chat.set_traiteur(None)
    agent_runtime.reset_etat_pour_tests()
    approvals.reset_etat_pour_tests()
    teacher.reset_etat_pour_tests()
    setup.reset_etat_pour_tests()
    voix_ws.reset_etat_pour_tests()
    maj_service.reset_etat_pour_tests()
    hub.reset()

    try:
        yield tmp_path
    finally:
        quota.set_quotas_for_tests(None)
        router.set_engines_for_tests(None)
        agy_engine.reset_etat_pour_tests()
        claude_engine.reset_etat_pour_tests()
        chat.set_traiteur(None)
        agent_runtime.reset_etat_pour_tests()
        approvals.reset_etat_pour_tests()
        teacher.reset_etat_pour_tests()
        setup.reset_etat_pour_tests()
        voix_ws.reset_etat_pour_tests()
        maj_service.reset_etat_pour_tests()
        hub.reset()
        shutdown_logging()
        config.refresh_paths(nouveau_root=ancien_root, app_dir=ancien_app_dir)


@pytest.fixture
def app(jarvis_root: Path) -> FastAPI:
    """Construit une instance FastAPI fraîche liée à ``jarvis_root``."""
    del jarvis_root
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Retourne un ``TestClient`` exécutant le lifespan de l'application."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def token(jarvis_root: Path, client: TestClient) -> str:
    """Lit le jeton généré dans ``RUNTIME/token.txt`` après démarrage du serveur."""
    del client
    chemin = jarvis_root / "runtime" / "token.txt"
    if not chemin.exists():
        return security.obtenir_jeton()
    return chemin.read_text(encoding="utf-8").strip()


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    """Construit les en-têtes d'authentification ``X-Jarvis-Token``."""
    return {"X-Jarvis-Token": token}
