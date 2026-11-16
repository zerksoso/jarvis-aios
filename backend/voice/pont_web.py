"""Pont web vocal simple (sans pilotage du site) pour ChatGPT et Gemini."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

from backend.jarvis_engine.hands import activer_fenetre_hwnd

logger = logging.getLogger(__name__)

ServiceWeb = Literal["chatgpt", "gemini"]
NavigateurWeb = Literal["defaut", "chrome", "edge"]

# Liste blanche stricte d'URL autorisées : aucune autre URL ne peut être ouverte.
URLS_AUTORISEES: dict[str, str] = {
    "chatgpt": "https://chatgpt.com/",
    "gemini": "https://gemini.google.com/app",
}
_ENSEMBLE_URLS_AUTORISEES: frozenset[str] = frozenset(URLS_AUTORISEES.values())

TITRES_FENETRES: dict[str, tuple[str, ...]] = {
    "chatgpt": ("chatgpt",),
    "gemini": ("gemini",),
}

_NAVIGATEURS_AUTORISES: frozenset[str] = frozenset({"defaut", "chrome", "edge"})

_EMPLACEMENTS_NAVIGATEURS: dict[str, tuple[tuple[str, str], ...]] = {
    "chrome": (
        ("ProgramFiles", "Google/Chrome/Application/chrome.exe"),
        ("ProgramFiles(x86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ),
    "edge": (
        ("ProgramFiles", "Microsoft/Edge/Application/msedge.exe"),
        ("ProgramFiles(x86)", "Microsoft/Edge/Application/msedge.exe"),
        ("LOCALAPPDATA", "Microsoft/Edge/Application/msedge.exe"),
    ),
}


def resoudre_url_service(service: str) -> tuple[str, str]:
    """Résout le service demandé vers l'URL fixe correspondante dans la liste blanche.

    Args:
        service: Identifiant du service (``"chatgpt"`` ou ``"gemini"``).

    Returns:
        Tuple ``(service_normalise, url_liste_blanche)``.

    Raises:
        ValueError: Si ``service`` ne fait pas partie de la liste blanche.
    """
    cle = str(service or "").strip().lower()
    url = URLS_AUTORISEES.get(cle)
    if url is None:
        raise ValueError(f"Service web non autorisé : {service!r}")
    return cle, url


def _lire_variable_env(nom: str) -> str:
    """Lit une variable d'environnement de façon insensible à la casse."""
    direct = os.environ.get(nom)
    if direct:
        return direct
    cible = nom.lower()
    for cle, val in os.environ.items():
        if cle.lower() == cible and val:
            return val
    return ""


def trouver_executable_navigateur(navigateur: str) -> Path | None:
    """Recherche l'exécutable Chrome ou Edge dans les emplacements Windows usuels.

    Args:
        navigateur: ``"defaut"``, ``"chrome"`` ou ``"edge"``.

    Returns:
        Chemin ``Path`` vers l'exécutable s'il existe, ou ``None`` pour ``"defaut"``
        ou si le navigateur est introuvable.

    Raises:
        ValueError: Si ``navigateur`` n'est pas reconnu.
    """
    nav = str(navigateur or "defaut").strip().lower()
    if nav not in _NAVIGATEURS_AUTORISES:
        raise ValueError(f"Navigateur non autorisé : {navigateur!r}")
    if nav == "defaut":
        return None

    for var_env, sous_chemin in _EMPLACEMENTS_NAVIGATEURS[nav]:
        base = _lire_variable_env(var_env)
        if not base:
            continue
        candidat = Path(base) / sous_chemin
        if candidat.is_file():
            return candidat
    return None


def trouver_fenetre_par_titre(mots_cles: tuple[str, ...]) -> int | None:
    """Recherche une fenêtre Windows visible de premier niveau dont le titre contient un mot-clé.

    Args:
        mots_cles: Sous-chaînes insensibles à la casse recherchées dans le titre de fenêtre.

    Returns:
        Le premier ``HWND`` correspondant, ou ``None`` si aucune fenêtre ne correspond.
    """
    if sys.platform != "win32":
        return None

    cibles = tuple(m.strip().lower() for m in mots_cles if m.strip())
    if not cibles:
        return None

    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        trouvees: list[int] = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def _cb(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            longueur = int(user32.GetWindowTextLengthW(hwnd))
            if longueur <= 0:
                return True
            tampon = ctypes.create_unicode_buffer(longueur + 1)
            user32.GetWindowTextW(hwnd, tampon, longueur + 1)
            titre = tampon.value.lower()
            if any(mot in titre for mot in cibles):
                trouvees.append(int(hwnd))
            return True

        user32.EnumWindows(_cb, 0)
        return trouvees[0] if trouvees else None
    except Exception as exc:
        logger.warning("Énumération des fenêtres Windows impossible (%s).", exc)
        return None


def mettre_au_premier_plan(hwnd: int) -> bool:
    """Restaure une fenêtre existante et la place au premier plan sous Windows.

    Args:
        hwnd: Descripteur Win32 de la fenêtre cible.

    Returns:
        ``True`` si la fenêtre est passée au premier plan.

    Effets de bord:
        Réutilise la technique ``activer_fenetre_hwnd`` (impulsion Alt + ``SW_RESTORE`` +
        ``SetForegroundWindow``).
    """
    return activer_fenetre_hwnd(hwnd, delai_s=0.15)


def _ouvrir_url(url: str, navigateur: str) -> None:
    """Ouvre une URL strictement présente dans la liste blanche via le navigateur choisi.

    Effets de bord:
        Lance ``chrome.exe`` / ``msedge.exe`` avec ``--app=<url>`` si trouvé, sinon
        délègue à ``os.startfile(url)``.
    """
    if url not in _ENSEMBLE_URLS_AUTORISEES:
        raise ValueError(f"URL hors liste blanche interdite : {url!r}")

    exe = trouver_executable_navigateur(navigateur)
    if exe is not None:
        subprocess.Popen([str(exe), f"--app={url}"])
        return

    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise RuntimeError("Ouverture par défaut (os.startfile) disponible uniquement sous Windows.")
    startfile(url)


def ouvrir_service_web(service: str = "chatgpt", navigateur: str = "defaut") -> dict[str, Any]:
    """Met au premier plan la fenêtre du service vocal web, ou l'ouvre si absente.

    N'injecte jamais de code et ne pilote pas le site : l'utilisateur lance lui-même
    le mode vocal sur ChatGPT ou Gemini.

    Args:
        service: Service ciblé (``"chatgpt"`` ou ``"gemini"``).
        navigateur: Navigateur d'ouverture (``"defaut"``, ``"chrome"`` ou ``"edge"``).

    Returns:
        Dictionnaire ``{"ok": True, "action": "premier_plan" | "ouverture", "service": str}``.

    Raises:
        ValueError: Si ``service`` ou ``navigateur`` est hors liste blanche.
        RuntimeError | OSError: Si le lancement du navigateur échoue.

    Effets de bord:
        Place la fenêtre existante au premier plan ou lance un processus navigateur / ``os.startfile``.
    """
    cle_service, url = resoudre_url_service(service)
    nav = str(navigateur or "defaut").strip().lower()
    if nav not in _NAVIGATEURS_AUTORISES:
        raise ValueError(f"Navigateur non autorisé : {navigateur!r}")

    hwnd = trouver_fenetre_par_titre(TITRES_FENETRES[cle_service])
    if hwnd is not None:
        mettre_au_premier_plan(hwnd)
        return {
            "ok": True,
            "action": "premier_plan",
            "service": cle_service,
        }

    _ouvrir_url(url, nav)
    return {
        "ok": True,
        "action": "ouverture",
        "service": cle_service,
    }
