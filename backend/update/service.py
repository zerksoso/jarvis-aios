"""Service d'orchestration des mises à jour : état mémoire, boucle 6 h et installation."""

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from backend import config, settings_store
from backend import version as version_mod
from backend.update import checker, downloader, installer
from backend.update.checker import InfoRelease
from backend.update.downloader import ErreurMiseAJour
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

MODES_AUTORISES: tuple[str, ...] = ("demander", "auto", "jamais")
DELAI_DEMARRAGE_S: float = 30.0
INTERVALLE_VERIF_S: float = 6 * 3600.0

_lock = threading.Lock()
_etat_service: dict[str, Any] = {
    "derniere": None,
    "release_info": None,
    "disponible": False,
    "verifie_le": None,
    "installation_en_cours": False,
    "job_id": None,
    "progression": {
        "etape": "En attente",
        "pourcent": 0,
        "erreur": None,
    },
}
_tache_install: asyncio.Task[Any] | None = None


def reset_etat_pour_tests() -> None:
    """Réinitialise l'état en mémoire du service de mise à jour (tests unitaires)."""
    global _tache_install
    if _tache_install is not None and not _tache_install.done():
        _tache_install.cancel()
    _tache_install = None
    with _lock:
        _etat_service["derniere"] = None
        _etat_service["release_info"] = None
        _etat_service["disponible"] = False
        _etat_service["verifie_le"] = None
        _etat_service["installation_en_cours"] = False
        _etat_service["job_id"] = None
        _etat_service["progression"] = {
            "etape": "En attente",
            "pourcent": 0,
            "erreur": None,
        }


def lire_mode() -> str:
    """Lit la politique de mise à jour courante depuis ``parametres.json`` (défaut ``demander``)."""
    params = settings_store.lire()
    maj_conf = params.get("maj")
    if isinstance(maj_conf, dict):
        mode = str(maj_conf.get("mode") or "demander").strip().lower()
        if mode in MODES_AUTORISES:
            return mode
    return "demander"


def changer_mode(mode: str) -> str:
    """Valide et persiste le mode de mise à jour (``demander``, ``auto``, ``jamais``)."""
    propre = (mode or "").strip().lower()
    if propre not in MODES_AUTORISES:
        raise ValueError(f"Mode de mise à jour invalide : {mode!r}")
    settings_store.ecrire({"maj": {"mode": propre}})
    return propre


def est_mode_installe(app_dir: Path | None = None) -> bool:
    """Indique si l'application tourne depuis une installation réelle (``APP_DIR/installed.flag``)."""
    base = app_dir if app_dir is not None else config.APP_DIR
    return (base / "installed.flag").is_file()


def etat_courant() -> dict[str, Any]:
    """Retourne la vue sérialisable pour ``GET /api/maj/etat``."""
    with _lock:
        derniere = dict(_etat_service["derniere"]) if _etat_service["derniere"] else None
        disponible = bool(_etat_service["disponible"])
        verifie_le = _etat_service["verifie_le"]

    return {
        "version": version_mod.version_actuelle(),
        "derniere": derniere,
        "disponible": disponible,
        "verifie_le": verifie_le,
        "mode": lire_mode(),
    }


def progression_courante() -> dict[str, Any]:
    """Retourne l'état courant de progression pour ``GET /api/maj/progression``."""
    with _lock:
        return dict(_etat_service["progression"])


def _maj_progression(etape: str, pourcent: int, erreur: str | None = None) -> None:
    """Met à jour l'état de progression de façon atomique."""
    with _lock:
        _etat_service["progression"] = {
            "etape": etape,
            "pourcent": max(0, min(100, int(pourcent))),
            "erreur": erreur,
        }


async def verifier(
    forcer: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Vérifie la disponibilité d'une nouvelle version sur GitHub Releases.

    En mode ``jamais`` (et ``forcer=False``), aucune requête réseau n'est émise.
    En cas de nouvelle version détectée, diffuse l'événement WS ``maj_disponible``.
    En mode ``auto`` (avec ``installed.flag`` et ``forcer=False``), lance l'installation.

    Args:
        forcer: Force la requête réseau même lorsque ``maj.mode == "jamais"``.
        transport: Transport ``httpx`` optionnel pour tests sans réseau.

    Returns:
        Dictionnaire d'état mis à jour (format ``GET /api/maj/etat``).
    """
    mode = lire_mode()
    if not forcer and mode == "jamais":
        return etat_courant()

    info: InfoRelease = await checker.verifier_release_github(transport=transport)
    horodatage = datetime.now().isoformat(timespec="seconds")

    derniere_payload = (
        {
            "version": info.version,
            "notes": info.notes,
            "legere": info.legere,
        }
        if info.disponible
        else None
    )

    with _lock:
        _etat_service["release_info"] = info if info.disponible else None
        _etat_service["derniere"] = derniere_payload
        _etat_service["disponible"] = info.disponible
        _etat_service["verifie_le"] = horodatage

    if info.disponible:
        await hub.broadcast(
            "maj_disponible",
            {
                "version": info.version,
                "notes": info.notes,
                "legere": info.legere,
            },
        )
        if not forcer and mode == "auto" and est_mode_installe():
            with _lock:
                en_cours = bool(_etat_service["installation_en_cours"])
            if not en_cours:
                try:
                    await demarrer_installation(transport=transport)
                except ErreurMiseAJour as exc:
                    logger.warning("Installation automatique impossible : %s", exc)

    return etat_courant()


async def _executer_installation(
    info: InfoRelease,
    transport: httpx.AsyncBaseTransport | None = None,
    arreter_serveur: Callable[[], None] | None = None,
) -> None:
    """Télécharge, vérifie puis applique la mise à jour légère ou complète en tâche de fond."""
    try:
        asset = info.app_zip if info.legere else info.setup
        dossier_maj = config.RUNTIME / "updates"
        dossier_maj.mkdir(parents=True, exist_ok=True)
        cible = dossier_maj / asset.name

        _maj_progression("Téléchargement de la mise à jour…", 5)

        def _on_progress(recus: int, total: int | None) -> None:
            if total and total > 0:
                ratio = min(1.0, recus / total)
                pct = 5 + int(ratio * 75)
                _maj_progression("Téléchargement de la mise à jour…", pct)

        await downloader.telecharger_fichier(
            url=asset.url,
            destination=cible,
            sha256_attendu=asset.sha256,
            taille_attendue=asset.size,
            on_progress=_on_progress,
            transport=transport,
        )

        _maj_progression("Vérification et préparation…", 85)
        if info.legere:
            _maj_progression("Extraction de la mise à jour…", 90)
            # Laisse un court délai au serveur HTTP pour répondre avant arrêt si appelé en fond.
            def _arret_differe() -> None:
                if arreter_serveur is not None:
                    arreter_serveur()
                else:
                    installer.arreter_serveur_propre()

            await asyncio.to_thread(
                installer.appliquer_maj_legere,
                cible,
                config.APP_DIR,
                lambda: None,
            )
            _maj_progression("Redémarrage de Jarvis…", 100)
            await asyncio.sleep(0.3)
            _arret_differe()
        else:
            _maj_progression("Vérification et lancement de l'installeur…", 95)
            await asyncio.to_thread(installer.appliquer_maj_complete, cible)
            _maj_progression("Installeur lancé", 100)
    except Exception as exc:
        logger.warning("Échec de l'installation de la mise à jour : %s", exc)
        _maj_progression("Erreur", 0, erreur=str(exc))
    finally:
        with _lock:
            _etat_service["installation_en_cours"] = False


async def demarrer_installation(
    transport: httpx.AsyncBaseTransport | None = None,
    arreter_serveur: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Démarre l'installation asynchrone de la mise à jour disponible.

    Refuse l'installation en mode développement (absence de ``APP_DIR/installed.flag``)
    ou lorsqu'aucune mise à jour n'est disponible.

    Args:
        transport: Transport ``httpx`` optionnel pour tests isolés.
        arreter_serveur: Callback d'arrêt serveur optionnel (tests).

    Returns:
        ``{"ok": True, "job_id": str, "statut": "en_cours"}``.

    Raises:
        ErreurMiseAJour: Si ``installed.flag`` est absent ou aucune mise à jour disponible.
    """
    global _tache_install

    if not est_mode_installe():
        raise ErreurMiseAJour(
            "Installation automatique désactivée en mode développement (fichier installed.flag absent)."
        )

    with _lock:
        info: InfoRelease | None = _etat_service.get("release_info")
        disponible = bool(_etat_service.get("disponible"))
        if not disponible or info is None:
            raise ErreurMiseAJour("Aucune mise à jour disponible à installer.")
        if _etat_service["installation_en_cours"] and _etat_service["job_id"]:
            return {
                "ok": True,
                "job_id": str(_etat_service["job_id"]),
                "statut": "en_cours",
            }
        job_id = f"maj-{uuid.uuid4().hex[:8]}"
        _etat_service["installation_en_cours"] = True
        _etat_service["job_id"] = job_id
        _etat_service["progression"] = {
            "etape": "Téléchargement de la mise à jour…",
            "pourcent": 5,
            "erreur": None,
        }

    _tache_install = asyncio.create_task(
        _executer_installation(info, transport=transport, arreter_serveur=arreter_serveur)
    )
    return {"ok": True, "job_id": job_id, "statut": "en_cours"}


async def boucle_verification_periodique(
    delai_initial_s: float = DELAI_DEMARRAGE_S,
    intervalle_s: float = INTERVALLE_VERIF_S,
) -> None:
    """Vérifie périodiquement les mises à jour (délai initial 30 s, puis toutes les 6 h).

    Ne propage jamais d'exception si GitHub est injoignable afin de ne pas affecter
    la stabilité du serveur local.
    """
    try:
        if delai_initial_s > 0:
            await asyncio.sleep(delai_initial_s)
        while True:
            try:
                if lire_mode() != "jamais":
                    await verifier(forcer=False)
            except Exception as exc:
                logger.warning("Vérification périodique des mises à jour échouée : %s", exc)
            await asyncio.sleep(intervalle_s)
    except asyncio.CancelledError:
        with suppress(Exception):
            pass
        raise
