"""Initialisation idempotente des données utilisateur et suivi du premier lancement."""

import json
import logging
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_SENTINELLE = object()

ETAT_DEFAUT: dict[str, Any] = {
    "termine": False,
    "etape": "moteurs",
    "termine_le": None,
}


def _chemin_premier_lancement() -> Path:
    """Retourne le chemin vers ``ROOT/runtime/premier_lancement.json``."""
    return config.RUNTIME / "premier_lancement.json"


def _lire_etat_sans_verrou() -> dict[str, Any]:
    """Lit et normalise ``premier_lancement.json`` sans acquérir ``_lock``."""
    chemin = _chemin_premier_lancement()
    if not chemin.is_file():
        return dict(ETAT_DEFAUT)

    try:
        brut = json.loads(chemin.read_text(encoding="utf-8"))
        if not isinstance(brut, dict):
            raise ValueError("premier_lancement.json doit contenir un objet JSON.")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("État premier lancement illisible (%s), retour aux valeurs par défaut.", exc)
        return dict(ETAT_DEFAUT)

    termine_le = brut.get("termine_le")
    termine_le_propre = str(termine_le).strip() if isinstance(termine_le, str) and termine_le.strip() else None
    return {
        "termine": bool(brut.get("termine", False)),
        "etape": str(brut.get("etape") or ETAT_DEFAUT["etape"]),
        "termine_le": termine_le_propre,
    }


def ensure_data_dir() -> None:
    """Prépare ``ROOT`` et copie les modèles vierges de ``TEMPLATES_DIR`` sans jamais écraser.

    Crée ``runtime/`` et tous les sous-dossiers de données attendus, puis copie récursivement
    chaque fichier absent de ``TEMPLATES_DIR`` vers ``ROOT``. Idempotente : les fichiers déjà
    présents dans ``ROOT`` sont préservés intacts.

    Effets de bord:
        Crée les répertoires sous ``config.ROOT`` et copie les fichiers modèles manquants.
    """
    config.ensure_dirs()

    templates_dir = config.TEMPLATES_DIR
    root_dir = config.ROOT
    if not templates_dir.is_dir():
        return
    if templates_dir.resolve() == root_dir.resolve():
        return

    copies = 0
    for src in templates_dir.rglob("*"):
        rel = src.relative_to(templates_dir)
        dest = root_dir / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif src.is_file() and not dest.exists() and not dest.is_symlink():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copies += 1

    if copies > 0:
        logger.info("Bootstrap données utilisateur : %d fichier(s) modèle(s) copié(s).", copies)


def lire_etat_premier_lancement() -> dict[str, Any]:
    """Lit l'état d'avancement du premier lancement depuis ``ROOT/runtime/premier_lancement.json``.

    Returns:
        Dictionnaire ``{"termine": bool, "etape": str, "termine_le": str | None}``.
    """
    with _lock:
        return _lire_etat_sans_verrou()


def ecrire_etat_premier_lancement(
    termine: bool | None = None,
    etape: str | None = None,
    termine_le: Any = _SENTINELLE,
) -> dict[str, Any]:
    """Met à jour de façon atomique l'état du premier lancement dans ``premier_lancement.json``.

    Args:
        termine: Nouvel état booléen de complétion, ou ``None`` pour conserver l'actuel.
        etape: Identifiant de l'étape courante, ou ``None`` pour conserver l'actuelle.
        termine_le: Horodatage ISO explicite (ou ``None``) ; si omis, rempli automatiquement
            quand ``termine=True`` et effacé quand ``termine=False``.

    Returns:
        Dictionnaire complet mis à jour ``{"termine", "etape", "termine_le"}``.

    Effets de bord:
        Écrit ``ROOT/runtime/premier_lancement.json`` via un fichier temporaire atomique.
    """
    with _lock:
        etat = _lire_etat_sans_verrou()

        if termine is not None:
            etat["termine"] = bool(termine)
            if termine_le is _SENTINELLE:
                if etat["termine"] and not etat["termine_le"]:
                    etat["termine_le"] = datetime.now().isoformat(timespec="seconds")
                elif not etat["termine"]:
                    etat["termine_le"] = None

        if etape is not None:
            etat["etape"] = str(etape)

        if termine_le is not _SENTINELLE:
            etat["termine_le"] = (
                str(termine_le).strip()
                if isinstance(termine_le, str) and termine_le.strip()
                else None
            )

        chemin = _chemin_premier_lancement()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = chemin.with_name(f"{chemin.stem}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(etat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(chemin)
        finally:
            tmp.unlink(missing_ok=True)

        return etat
