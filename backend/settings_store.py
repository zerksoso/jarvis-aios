"""Gestion persistante des paramètres utilisateur dans ``RUNTIME/parametres.json``."""

import copy
import json
import logging
import threading
from typing import Any

from backend import config

logger = logging.getLogger(__name__)

DEFAUTS: dict[str, Any] = {
    "moteur": "auto",
    "modele": "hydrogen",
    "effort": "medium",
    "verbosite": "normal",
    "accent": "#22d3ee",
    "voix": {
        "active": True,
        "nom": "fr-FR-DeniseNeural",
    },
    "raccourci": "ctrl+space",
    "memoire_auto": True,
    "maj": {
        "mode": "demander",
        "depot": "",
    },
}

_lock = threading.Lock()


def _fusion_profonde(base: dict[str, Any], maj: dict[str, Any]) -> dict[str, Any]:
    """Fusionne récursivement ``maj`` dans une copie profonde de ``base``.

    Args:
        base: Dictionnaire de référence.
        maj: Modifications partielles à appliquer.

    Returns:
        Nouveau dictionnaire résultant de la fusion profonde.
    """
    resultat = copy.deepcopy(base)
    for cle, valeur in maj.items():
        if isinstance(valeur, dict) and isinstance(resultat.get(cle), dict):
            resultat[cle] = _fusion_profonde(resultat[cle], valeur)
        else:
            resultat[cle] = copy.deepcopy(valeur)
    return resultat


def lire() -> dict[str, Any]:
    """Lit les paramètres utilisateur, en créant le fichier par défaut s'il est absent.

    Complète automatiquement toute clé manquante à partir de ``DEFAUTS``.

    Returns:
        Dictionnaire complet des paramètres courants.

    Effets de bord:
        Crée ``RUNTIME/parametres.json`` au premier appel s'il n'existe pas.
    """
    with _lock:
        chemin = config.SETTINGS_PATH
        if not chemin.exists():
            chemin.parent.mkdir(parents=True, exist_ok=True)
            donnees = copy.deepcopy(DEFAUTS)
            chemin.write_text(json.dumps(donnees, indent=2, ensure_ascii=False), encoding="utf-8")
            return donnees

        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
            if not isinstance(brut, dict):
                raise ValueError("Le fichier de paramètres doit contenir un objet JSON.")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Paramètres illisibles (%s), retour aux valeurs par défaut.", exc)
            return copy.deepcopy(DEFAUTS)

        return _fusion_profonde(DEFAUTS, brut)


def ecrire(partiel: dict[str, Any]) -> dict[str, Any]:
    """Met à jour les paramètres par fusion profonde et persiste le résultat sur disque.

    Args:
        partiel: Dictionnaire partiel des clés à modifier.

    Returns:
        Dictionnaire complet des paramètres après mise à jour.

    Effets de bord:
        Lit et réécrit ``RUNTIME/parametres.json`` sous verrou thread.
    """
    with _lock:
        chemin = config.SETTINGS_PATH
        chemin.parent.mkdir(parents=True, exist_ok=True)

        courant = copy.deepcopy(DEFAUTS)
        if chemin.exists():
            try:
                brut = json.loads(chemin.read_text(encoding="utf-8"))
                if isinstance(brut, dict):
                    courant = _fusion_profonde(DEFAUTS, brut)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Paramètres existants corrompus (%s), réinitialisation avant fusion.", exc)

        mis_a_jour = _fusion_profonde(courant, partiel)
        chemin.write_text(json.dumps(mis_a_jour, indent=2, ensure_ascii=False), encoding="utf-8")
        return mis_a_jour
