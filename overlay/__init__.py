"""Application compagnon bureau Windows (HUD transparent PySide6) pour Jarvis AIOS."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"


def obtenir_racine() -> Path:
    """Retourne le dossier racine du projet Jarvis (``JARVIS_ROOT`` ou parent du package).

    Returns:
        Chemin absolu vers la racine du projet contenant ``runtime/``.
    """
    from backend import (
        config,  # même résolution que le serveur : JARVIS_ROOT, sinon ~/JarvisAIOS si installé, sinon dossier du code
    )

    return config.resoudre_root(Path(__file__).resolve().parents[1]).resolve()
