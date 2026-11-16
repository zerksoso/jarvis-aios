"""Inspection Windows UI Automation (fenêtre active, éléments visibles et ciblage au point)."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ELEMENTS_DEFAUT = 300
MAX_PROFONDEUR_DEFAUT = 12
TIMEOUT_PARCOURS_S = 2.0

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _nom_processus_depuis_pid(pid: int) -> str:
    """Résout le nom d'exécutable (ex. ``notepad.exe``) d'un processus Windows par son PID."""
    if sys.platform != "win32" or pid <= 0:
        return ""

    # QueryFullProcessImageNameW fonctionne même vers des processus 64 bits ou protégés.
    try:
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
        if handle:
            try:
                taille = wintypes.DWORD(512)
                buf = ctypes.create_unicode_buffer(512)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(taille)):
                    return Path(buf.value).name
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass

    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            pid,
        )
        try:
            exe = win32process.GetModuleFileNameEx(handle, 0)
            return Path(exe).name
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def fenetre_active() -> dict[str, Any]:
    """Retourne les métadonnées de la fenêtre Windows actuellement au premier plan.

    Returns:
        Dictionnaire ``{"titre": str, "processus": str, "pid": int, "rect": {"x", "y", "w", "h"}}``.
        En cas d'indisponibilité des APIs Windows, retourne des valeurs vides par défaut.
    """
    vide: dict[str, Any] = {
        "titre": "",
        "processus": "",
        "pid": 0,
        "rect": {"x": 0, "y": 0, "w": 0, "h": 0},
    }
    if sys.platform != "win32":
        return vide

    try:
        import win32gui
        import win32process

        hwnd = int(win32gui.GetForegroundWindow() or 0)
        if hwnd <= 0:
            return vide
        titre = str(win32gui.GetWindowText(hwnd) or "")
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        processus = _nom_processus_depuis_pid(int(pid))
        return {
            "titre": titre,
            "processus": processus,
            "pid": int(pid),
            "rect": {
                "x": int(left),
                "y": int(top),
                "w": max(0, int(right - left)),
                "h": max(0, int(bottom - top)),
            },
        }
    except ImportError:
        logger.debug("pywin32 indisponible ; repli pygetwindow/ctypes pour fenetre_active.")
    except Exception as exc:
        logger.debug("Erreur win32gui dans fenetre_active : %s", exc)

    # Repli pygetwindow + ctypes si pywin32 a échoué.
    try:
        import pygetwindow as gw

        win = gw.getActiveWindow()
        if win is None:
            return vide
        hwnd = int(getattr(win, "_hWnd", 0) or 0)
        pid = 0
        if hwnd:
            pid_c = ctypes.c_ulong(0)
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_c))
            pid = int(pid_c.value)
        return {
            "titre": str(win.title or ""),
            "processus": _nom_processus_depuis_pid(pid),
            "pid": pid,
            "rect": {
                "x": int(win.left),
                "y": int(win.top),
                "w": max(0, int(win.width)),
                "h": max(0, int(win.height)),
            },
        }
    except Exception as exc:
        logger.warning("Impossible de lire la fenêtre active : %s", exc)
        return vide


def _rect_depuis_controle(ctrl: Any) -> dict[str, int] | None:
    """Extrait ``{"x", "y", "w", "h"}`` d'un contrôle ``uiautomation`` ou ``None`` si vide."""
    rect = ctrl.BoundingRectangle
    w = int(rect.width())
    h = int(rect.height())
    if w <= 0 or h <= 0:
        return None
    return {
        "x": int(rect.left),
        "y": int(rect.top),
        "w": w,
        "h": h,
    }


def elements(
    max: int = MAX_ELEMENTS_DEFAUT,
    *,
    max_profondeur: int = MAX_PROFONDEUR_DEFAUT,
    timeout_s: float = TIMEOUT_PARCOURS_S,
) -> list[dict[str, Any]]:
    """Parcourt les contrôles visibles et nommés de la fenêtre active via UI Automation.

    Initialise COM dans le thread courant via ``UIAutomationInitializerInThread()`` et
    borne le parcours en largeur à ``max_profondeur`` (12), ``max`` éléments (300) et
    ``timeout_s`` (2 s). Prévu pour être appelé via ``asyncio.to_thread``.

    Args:
        max: Nombre maximal de contrôles retournés (défaut 300).
        max_profondeur: Profondeur maximale de parcours de l'arbre UIA (défaut 12).
        timeout_s: Budget temps maximal en secondes (défaut 2.0 s).

    Returns:
        Liste de ``{"index", "nom", "role", "rect": {"x", "y", "w", "h"}, "actif"}``.
    """
    if sys.platform != "win32":
        return []

    limite = int(max)
    if limite < 1:
        limite = 1

    try:
        import uiautomation as auto
    except ImportError:
        logger.warning("Module 'uiautomation' indisponible : liste d'éléments UIA vide.")
        return []

    resultats: list[dict[str, Any]] = []
    deadline = time.monotonic() + float(timeout_s)

    try:
        with auto.UIAutomationInitializerInThread():
            hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
            racine = auto.ControlFromHandle(hwnd) if hwnd else auto.GetForegroundControl()
            if racine is None:
                return []

            file_attente: deque[tuple[Any, int]] = deque([(racine, 0)])
            while file_attente and len(resultats) < limite and time.monotonic() < deadline:
                ctrl, profondeur = file_attente.popleft()

                if profondeur > 0:
                    try:
                        nom = str(ctrl.Name or "").strip()
                        if nom and not bool(ctrl.IsOffscreen):
                            rect = _rect_depuis_controle(ctrl)
                            if rect is not None:
                                role = str(ctrl.ControlTypeName or "")
                                actif = bool(ctrl.IsEnabled)
                                resultats.append(
                                    {
                                        "index": len(resultats) + 1,
                                        "nom": nom,
                                        "role": role,
                                        "rect": rect,
                                        "actif": actif,
                                    }
                                )
                                if len(resultats) >= limite:
                                    break
                    except Exception:
                        # Un contrôle peut être détruit pendant le parcours ; on l'ignore.
                        pass

                if profondeur >= max_profondeur or time.monotonic() >= deadline:
                    continue

                try:
                    enfant = ctrl.GetFirstChildControl()
                    while enfant is not None and time.monotonic() < deadline:
                        file_attente.append((enfant, profondeur + 1))
                        enfant = enfant.GetNextSiblingControl()
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("Erreur lors du parcours UI Automation : %s", exc)

    return resultats


def _serialiser_controle_ponctuel(ctrl: Any) -> dict[str, Any] | None:
    """Convertit un contrôle UIA ponctuel en dictionnaire normalisé avec indicateur mot de passe."""
    if ctrl is None:
        return None
    try:
        nom = str(ctrl.Name or "").strip()
        role = str(ctrl.ControlTypeName or "")
        rect_obj = ctrl.BoundingRectangle
        rect = {
            "x": int(rect_obj.left),
            "y": int(rect_obj.top),
            "w": int(rect_obj.width()),
            "h": int(rect_obj.height()),
        }
        est_mdp = bool(ctrl.IsPassword)
        return {
            "nom": nom,
            "role": role,
            "rect": rect,
            "est_mot_de_passe": est_mdp,
        }
    except Exception:
        return None


def element_au_point(x: int, y: int) -> dict[str, Any] | None:
    """Retourne les informations du contrôle UIA situé aux coordonnées écran ``(x, y)``.

    Args:
        x: Abscisse physique en pixels.
        y: Ordonnée physique en pixels.

    Returns:
        Dictionnaire ``{"nom", "role", "rect", "est_mot_de_passe"}`` ou ``None``.
    """
    if sys.platform != "win32":
        return None
    try:
        import uiautomation as auto
    except ImportError:
        logger.debug("Module 'uiautomation' absent pour element_au_point.")
        return None

    try:
        with auto.UIAutomationInitializerInThread():
            ctrl = auto.ControlFromPoint(int(x), int(y))
            return _serialiser_controle_ponctuel(ctrl)
    except Exception as exc:
        logger.debug("element_au_point(%s, %s) a échoué : %s", x, y, exc)
        return None


def element_focus() -> dict[str, Any] | None:
    """Retourne les informations du contrôle UIA ayant actuellement le focus clavier.

    Returns:
        Dictionnaire ``{"nom", "role", "rect", "est_mot_de_passe"}`` ou ``None``.
    """
    if sys.platform != "win32":
        return None
    try:
        import uiautomation as auto
    except ImportError:
        return None

    try:
        with auto.UIAutomationInitializerInThread():
            ctrl = auto.GetFocusedControl()
            return _serialiser_controle_ponctuel(ctrl)
    except Exception as exc:
        logger.debug("element_focus() a échoué : %s", exc)
        return None
