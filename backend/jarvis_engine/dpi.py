"""Activation de la prise en charge DPI Per-Monitor V2 pour coordonnées physiques exactes."""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger(__name__)

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2


def activer() -> bool:
    """Active le mode DPI Per-Monitor V2 afin d'obtenir des coordonnées en pixels physiques.

    Tente ``user32.SetProcessDpiAwarenessContext(-4)``, puis se replie sur
    ``shcore.SetProcessDpiAwareness(2)`` sur les versions Windows antérieures.
    Sans effet hors Windows ou si le contexte DPI est déjà verrouillé par le processus.

    Returns:
        ``True`` si l'activation DPI a réussi lors de cet appel, ``False`` sinon.

    Effets de bord:
        Modifie la politique DPI du processus courant via les DLL système Windows.
    """
    if sys.platform != "win32":
        return False

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False

    try:
        user32 = windll.user32
        # -4 correspond à DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (Windows 10 1703+).
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
            logger.debug("DPI Per-Monitor V2 activé via SetProcessDpiAwarenessContext.")
            return True
    except (AttributeError, OSError, ValueError) as exc:
        logger.debug("SetProcessDpiAwarenessContext indisponible (%s).", exc)

    try:
        shcore = windll.shcore
        # 2 correspond à PROCESS_PER_MONITOR_DPI_AWARE ; HRESULT == 0 indique S_OK.
        hresult = int(shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE))
        if hresult == 0:
            logger.debug("DPI Per-Monitor activé via SetProcessDpiAwareness(2).")
            return True
        logger.debug("SetProcessDpiAwareness(2) ignoré (HRESULT=%s).", hresult)
    except (AttributeError, OSError, ValueError) as exc:
        logger.debug("SetProcessDpiAwareness(2) indisponible (%s).", exc)

    return False
