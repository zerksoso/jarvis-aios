"""Configuration de la journalisation rotative vers fichier et console."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend import config

_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_HANDLERS_ACTIFS: list[logging.Handler] = []


def shutdown_logging() -> None:
    """Ferme et retire les handlers installés par Jarvis sur le logger racine.

    Essentiel sous Windows pour libérer les verrous de fichiers avant suppression de dossiers temporaires.

    Effets de bord:
        Ferme et détache les handlers suivis sur ``logging.getLogger()``.
    """
    racine = logging.getLogger()
    for handler in list(_HANDLERS_ACTIFS):
        racine.removeHandler(handler)
        try:
            handler.close()
        except OSError:
            pass
    _HANDLERS_ACTIFS.clear()


def setup_logging(log_dir: Path | None = None) -> logging.Logger:
    """Configure les sorties console et fichier rotatif (5 × 2 Mo) pour Jarvis.

    Idempotent : remplace proprement les handlers Jarvis existants si le chemin change.

    Args:
        log_dir: Dossier cible optionnel ; par défaut ``config.LOG_DIR``.

    Returns:
        Le logger nommé ``"jarvis"``.

    Effets de bord:
        Crée le dossier de logs et ouvre ``jarvis.log`` en mode append rotatif.
    """
    dossier = log_dir or config.LOG_DIR
    dossier.mkdir(parents=True, exist_ok=True)
    fichier_log = dossier / "jarvis.log"

    shutdown_logging()

    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    formateur = logging.Formatter(_FORMAT)

    handler_fichier = RotatingFileHandler(
        fichier_log,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler_fichier.setFormatter(formateur)

    handler_console = logging.StreamHandler()
    handler_console.setFormatter(formateur)

    racine.addHandler(handler_fichier)
    racine.addHandler(handler_console)
    _HANDLERS_ACTIFS.extend((handler_fichier, handler_console))
    return logging.getLogger("jarvis")
