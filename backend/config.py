"""Configuration centralisée des chemins et paramètres réseau de Jarvis."""

import os
from pathlib import Path

HOST = "127.0.0.1"
PORT = 5230

VERSION_DEFAUT = "0.0.0-dev"
APP_DIR = Path(__file__).resolve().parents[1]


def lire_version(app_dir: Path | None = None) -> str:
    """Lit la version applicative depuis ``APP_DIR/VERSION`` (défaut ``0.0.0-dev``).

    Args:
        app_dir: Dossier applicatif à inspecter ; si ``None``, utilise ``config.APP_DIR``.

    Returns:
        Chaîne de version nettoyée, ou ``"0.0.0-dev"`` si absente ou vide.
    """
    base = app_dir if app_dir is not None else APP_DIR
    fichier = base / "VERSION"
    if fichier.is_file():
        try:
            contenu = fichier.read_text(encoding="utf-8").strip()
        except OSError:
            return VERSION_DEFAUT
        if contenu:
            return contenu
    return VERSION_DEFAUT


def resoudre_root(app_dir: Path | None = None) -> Path:
    """Résout le dossier des données utilisateur ``ROOT`` selon la priorité de distribution.

    Priorité :
    1. Variable d'environnement ``JARVIS_ROOT`` si définie et non vide.
    2. ``%USERPROFILE%\\JarvisAIOS`` (``Path.home() / "JarvisAIOS"``) si ``APP_DIR/installed.flag`` existe.
    3. ``APP_DIR`` (mode développement local).

    Args:
        app_dir: Dossier applicatif de référence ; par défaut ``config.APP_DIR``.

    Returns:
        Chemin ``Path`` du dossier racine des données utilisateur.
    """
    env_root = (os.environ.get("JARVIS_ROOT") or "").strip()
    if env_root:
        return Path(env_root)

    base_app = app_dir if app_dir is not None else APP_DIR
    if (base_app / "installed.flag").exists():
        return Path.home() / "JarvisAIOS"

    return base_app


ROOT = resoudre_root(APP_DIR)
RUNTIME = ROOT / "runtime"
DB_PATH = RUNTIME / "jarvis.db"
TOKEN_PATH = RUNTIME / "token.txt"
SETTINGS_PATH = RUNTIME / "parametres.json"
BRAIN_DIR = ROOT / "second-brain"
SKILLS_DIR = ROOT / "aios/skills"
ROUTINES_DIR = ROOT / "aios/routines"
SCRIPTS_DIR = ROOT / "aios/scripts"
AGENTS_DIR = ROOT / "agents"
CANAUX_DIR = ROOT / "canaux"
AGY_SANDBOX = RUNTIME / "agy-sandbox"
CLAUDE_SANDBOX = RUNTIME / "claude-sandbox"
LOG_DIR = RUNTIME / "logs"

FRONT_DIST = APP_DIR / "frontend/dist"
TEMPLATES_DIR = APP_DIR / "templates"
VERSION = lire_version(APP_DIR)


def refresh_paths(
    nouveau_root: Path | None = None,
    app_dir: Path | None = None,
) -> Path:
    """Recalcule tous les chemins dérivés de ``ROOT`` et de ``APP_DIR``.

    Utile principalement pour les tests isolés qui modifient ``JARVIS_ROOT`` ou ``APP_DIR``
    après import.

    Args:
        nouveau_root: Racine explicite des données ; si ``None``, applique ``resoudre_root()``.
        app_dir: Dossier applicatif explicite ; si ``None``, conserve ``APP_DIR`` courant.

    Returns:
        Le chemin racine ``ROOT`` actif après mise à jour.

    Effets de bord:
        Met à jour les constantes de module ``APP_DIR``, ``ROOT``, ``RUNTIME``, ``DB_PATH``,
        ``FRONT_DIST``, ``TEMPLATES_DIR``, ``VERSION``, etc.
    """
    global APP_DIR, ROOT, RUNTIME, DB_PATH, TOKEN_PATH, SETTINGS_PATH
    global BRAIN_DIR, SKILLS_DIR, ROUTINES_DIR, SCRIPTS_DIR
    global AGENTS_DIR, CANAUX_DIR, AGY_SANDBOX, CLAUDE_SANDBOX, LOG_DIR
    global FRONT_DIST, TEMPLATES_DIR, VERSION

    if app_dir is not None:
        APP_DIR = Path(app_dir)

    if nouveau_root is not None:
        ROOT = Path(nouveau_root)
    else:
        ROOT = resoudre_root(APP_DIR)

    RUNTIME = ROOT / "runtime"
    DB_PATH = RUNTIME / "jarvis.db"
    TOKEN_PATH = RUNTIME / "token.txt"
    SETTINGS_PATH = RUNTIME / "parametres.json"
    BRAIN_DIR = ROOT / "second-brain"
    SKILLS_DIR = ROOT / "aios/skills"
    ROUTINES_DIR = ROOT / "aios/routines"
    SCRIPTS_DIR = ROOT / "aios/scripts"
    AGENTS_DIR = ROOT / "agents"
    CANAUX_DIR = ROOT / "canaux"
    AGY_SANDBOX = RUNTIME / "agy-sandbox"
    CLAUDE_SANDBOX = RUNTIME / "claude-sandbox"
    LOG_DIR = RUNTIME / "logs"

    FRONT_DIST = APP_DIR / "frontend/dist"
    TEMPLATES_DIR = APP_DIR / "templates"
    VERSION = lire_version(APP_DIR)
    return ROOT


def ensure_dirs() -> None:
    """Crée l'ensemble des répertoires de travail requis par Jarvis.

    Ne crée pas ``FRONT_DIST`` afin que le serveur détecte l'absence d'un build frontend.

    Effets de bord:
        Crée les répertoires sur le système de fichiers s'ils n'existent pas encore.
    """
    dossiers = (
        RUNTIME,
        BRAIN_DIR,
        SKILLS_DIR,
        ROUTINES_DIR,
        SCRIPTS_DIR,
        AGENTS_DIR,
        CANAUX_DIR,
        AGY_SANDBOX,
        AGY_SANDBOX / "img",
        CLAUDE_SANDBOX,
        LOG_DIR,
    )
    for dossier in dossiers:
        dossier.mkdir(parents=True, exist_ok=True)
