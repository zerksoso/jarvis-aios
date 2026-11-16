"""Métadonnées de version applicative, empreinte runtime et dépôt GitHub des mises à jour."""

import os
import re
from pathlib import Path

from backend import config, settings_store

DEPOT_GITHUB = "zerksoso/jarvis-aios"
VERSION = config.VERSION

_DEPOT_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def version_actuelle() -> str:
    """Retourne la version applicative courante lue dynamiquement depuis ``config.VERSION``.

    Returns:
        Chaîne de version (ex. ``"1.2.3"`` ou ``"0.0.0-dev"``).
    """
    return config.VERSION


def runtime_version_actuelle(app_dir: Path | None = None) -> str | None:
    """Lit l'empreinte ``RUNTIME_VERSION`` depuis ``APP_DIR/RUNTIME_VERSION`` si présente.

    Args:
        app_dir: Dossier applicatif cible ; par défaut ``config.APP_DIR``.

    Returns:
        Chaîne d'empreinte nettoyée ou ``None`` si le fichier est absent ou vide.
    """
    base = app_dir if app_dir is not None else config.APP_DIR
    fichier = base / "RUNTIME_VERSION"
    if not fichier.is_file():
        return None
    try:
        contenu = fichier.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return contenu or None


def depot_github() -> str:
    """Résout le dépôt GitHub ``owner/repo`` utilisé pour la vérification des releases.

    Priorité :
    1. Variable d'environnement ``JARVIS_DEPOT_GITHUB``.
    2. Paramètre ``parametres.json -> maj.depot``.
    3. Constante de module ``DEPOT_GITHUB``.

    Returns:
        Identifiant GitHub validé au format ``owner/repo``.

    Raises:
        ValueError: Si la valeur configurée ne respecte pas le format ``owner/repo``.
    """
    env_depot = (os.environ.get("JARVIS_DEPOT_GITHUB") or "").strip()
    if env_depot:
        if not _DEPOT_RE.match(env_depot):
            raise ValueError(f"Dépôt GitHub invalide (JARVIS_DEPOT_GITHUB) : {env_depot!r}")
        return env_depot

    params = settings_store.lire()
    maj_conf = params.get("maj")
    if isinstance(maj_conf, dict):
        param_depot = str(maj_conf.get("depot") or "").strip()
        if param_depot:
            if not _DEPOT_RE.match(param_depot):
                raise ValueError(f"Dépôt GitHub invalide (parametres.json) : {param_depot!r}")
            return param_depot

    if not _DEPOT_RE.match(DEPOT_GITHUB):
        raise ValueError(f"Dépôt GitHub par défaut invalide : {DEPOT_GITHUB!r}")
    return DEPOT_GITHUB
