"""Utilitaires Windows (Win32 styles étendus, exclusion capture, DPI et conversion coordonnées)."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

WDA_EXCLUDEFROMCAPTURE = 0x00000011

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


@dataclass(frozen=True)
class GeometrieEcran:
    """Description logique et physique d'un écran pour la conversion de coordonnées."""

    log_x: int
    log_y: int
    log_w: int
    log_h: int
    dpr: float = 1.0
    phys_x: int | None = None
    phys_y: int | None = None
    phys_w: int | None = None
    phys_h: int | None = None

    def rect_physique(self) -> tuple[int, int, int, int]:
        """Retourne ``(x, y, w, h)`` en pixels physiques pour cet écran."""
        dpr = self.dpr if self.dpr > 0 else 1.0
        px = self.phys_x if self.phys_x is not None else round(self.log_x * dpr)
        py = self.phys_y if self.phys_y is not None else round(self.log_y * dpr)
        pw = self.phys_w if self.phys_w is not None else max(1, round(self.log_w * dpr))
        ph = self.phys_h if self.phys_h is not None else max(1, round(self.log_h * dpr))
        return px, py, pw, ph


def activer_dpi() -> bool:
    """Active la prise en charge DPI Per-Monitor V2 avant création de ``QApplication``.

    Tente d'abord ``backend.jarvis_engine.dpi.activer()``, sinon applique directement
    l'API Windows via ``ctypes``.

    Returns:
        ``True`` si l'activation a réussi ou est supportée, ``False`` sinon.
    """
    try:
        from backend.jarvis_engine import dpi as backend_dpi

        activer_fn = getattr(backend_dpi, "activer", None)
        if callable(activer_fn):
            activer_fn()
            return True
    except Exception:
        pass

    if sys.platform != "win32":
        return False

    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass

    try:
        shcore = ctypes.windll.shcore
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(2)
        return True
    except Exception as exc:
        logger.debug("Impossible d'activer DPI Per-Monitor : %s", exc)
        return False


def exclure_de_capture(hwnd: int) -> bool:
    """Exclut une fenêtre des captures d'écran Windows (``WDA_EXCLUDEFROMCAPTURE``).

    Args:
        hwnd: Identifiant natif Win32 de la fenêtre.

    Returns:
        ``True`` si ``SetWindowDisplayAffinity`` a réussi.
    """
    if sys.platform != "win32" or hwnd <= 0:
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        ok = user32.SetWindowDisplayAffinity(
            wintypes.HWND(hwnd),
            wintypes.DWORD(WDA_EXCLUDEFROMCAPTURE),
        )
        return bool(ok)
    except Exception as exc:
        logger.debug("SetWindowDisplayAffinity indisponible (%s).", exc)
        return False


def appliquer_style_overlay(
    hwnd: int,
    traversable: bool = False,
    exclure_capture: bool = True,
) -> bool:
    """Applique les styles Win32 étendus d'overlay et l'exclusion de capture sur ``hwnd``.

    Args:
        hwnd: Handle Win32 (``int(widget.winId())``).
        traversable: Si ``True``, ajoute ``WS_EX_LAYERED | WS_EX_TRANSPARENT`` pour laisser
            passer clics et survols vers les fenêtres sous-jacentes.
        exclure_capture: Si ``True``, applique ``WDA_EXCLUDEFROMCAPTURE (0x11)``.

    Returns:
        ``True`` si la configuration Win32 a été appliquée avec succès.

    Effets de bord:
        Modifie ``GWL_EXSTYLE``, force le rafraîchissement de frame ``HWND_TOPMOST`` et
        configure l'affinité d'affichage.
    """
    if sys.platform != "win32" or hwnd <= 0:
        return False

    masque = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if traversable:
        masque |= WS_EX_LAYERED | WS_EX_TRANSPARENT

    applique = False
    try:
        import win32con
        import win32gui

        style_actuel = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, style_actuel | masque)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE
            | win32con.SWP_NOSIZE
            | win32con.SWP_NOACTIVATE
            | win32con.SWP_FRAMECHANGED,
        )
        applique = True
    except Exception:
        try:
            user32 = ctypes.windll.user32
            style_actuel = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style_actuel | masque)
            user32.SetWindowPos(
                hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            )
            applique = True
        except Exception as exc:
            logger.debug("Échec d'application des styles Win32 sur %s (%s).", hwnd, exc)

    if exclure_capture:
        exclure_de_capture(hwnd)
    return applique


def configurer_fenetre_qt(
    widget: QWidget,
    traversable: bool = False,
    exclure_capture: bool = True,
) -> bool:
    """Configure les styles Win32 et l'exclusion de capture d'un widget PySide6 affiché."""
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False
    return appliquer_style_overlay(
        hwnd,
        traversable=traversable,
        exclure_capture=exclure_capture,
    )


def _contient(rect: tuple[int, int, int, int], px: float, py: float) -> bool:
    """Vérifie si ``(px, py)`` appartient au rectangle ``(x, y, w, h)``."""
    rx, ry, rw, rh = rect
    return rx <= px < rx + rw and ry <= py < ry + rh


def convertir_rect_physique_vers_logique(
    x: float,
    y: float,
    w: float,
    h: float,
    ecrans: Sequence[GeometrieEcran] | None = None,
    dpr_defaut: float = 1.0,
) -> tuple[int, int, int, int]:
    """Convertit un rectangle en pixels physiques vers des coordonnées logiques Qt.

    Sélectionne l'écran contenant le centre (ou le coin haut-gauche, sinon l'écran le plus
    proche) et applique son ``devicePixelRatio`` relatif à l'origine de cet écran.

    Args:
        x: Abscisse physique dans l'écran virtuel.
        y: Ordonnée physique dans l'écran virtuel.
        w: Largeur en pixels physiques.
        h: Hauteur en pixels physiques.
        ecrans: Liste des écrans disponibles (logique + physique + DPR).
        dpr_defaut: Facteur DPR de repli si ``ecrans`` est vide.

    Returns:
        Tuple ``(x, y, w, h)`` en coordonnées logiques entières Qt.
    """
    if not ecrans:
        dpr = dpr_defaut if dpr_defaut > 0 else 1.0
        return (
            round(x / dpr),
            round(y / dpr),
            max(1, round(w / dpr)),
            max(1, round(h / dpr)),
        )

    cx = x + w / 2.0
    cy = y + h / 2.0

    cible: GeometrieEcran | None = None
    for ecr in ecrans:
        if _contient(ecr.rect_physique(), cx, cy):
            cible = ecr
            break
    if cible is None:
        for ecr in ecrans:
            if _contient(ecr.rect_physique(), x, y):
                cible = ecr
                break
    if cible is None:
        def _dist2(ecr: GeometrieEcran) -> float:
            px0, py0, pw, ph = ecr.rect_physique()
            ecx = px0 + pw / 2.0
            ecy = py0 + ph / 2.0
            return (cx - ecx) ** 2 + (cy - ecy) ** 2

        cible = min(ecrans, key=_dist2)

    px0, py0, _, _ = cible.rect_physique()
    dpr = cible.dpr if cible.dpr > 0 else 1.0
    lx = round(cible.log_x + (x - px0) / dpr)
    ly = round(cible.log_y + (y - py0) / dpr)
    lw = max(1, round(w / dpr))
    lh = max(1, round(h / dpr))
    return lx, ly, lw, lh


def _moniteurs_physiques_win32() -> dict[str, tuple[int, int, int, int]]:
    """Retourne la carte ``nom_peripherique -> (x, y, w, h)`` physique via Win32."""
    if sys.platform != "win32":
        return {}
    resultat: dict[str, tuple[int, int, int, int]] = {}
    try:
        import win32api

        for hmon, _hdc, _rect in win32api.EnumDisplayMonitors():
            info = win32api.GetMonitorInfo(hmon)
            device = str(info.get("Device") or "")
            left, top, right, bottom = info["Monitor"]
            if device:
                resultat[device] = (left, top, right - left, bottom - top)
    except Exception:
        return {}
    return resultat


def rect_physique_vers_logique(x: float, y: float, w: float, h: float) -> QRect:
    """Convertit un rectangle en pixels physiques vers ``QRect`` logique via ``QGuiApplication``.

    Args:
        x: Abscisse physique virtuelle.
        y: Ordonnée physique virtuelle.
        w: Largeur physique.
        h: Hauteur physique.

    Returns:
        ``QRect`` en coordonnées logiques Qt prêtes pour l'affichage.
    """
    screens = QGuiApplication.screens()
    if not screens:
        lx, ly, lw, lh = convertir_rect_physique_vers_logique(x, y, w, h)
        return QRect(lx, ly, lw, lh)

    win32_rects = _moniteurs_physiques_win32()
    ecrans: list[GeometrieEcran] = []
    for scr in screens:
        g = scr.geometry()
        dpr = float(scr.devicePixelRatio() or 1.0)
        phys = win32_rects.get(scr.name())
        if phys is not None:
            px, py, pw, ph = phys
            ecrans.append(
                GeometrieEcran(
                    log_x=g.x(),
                    log_y=g.y(),
                    log_w=g.width(),
                    log_h=g.height(),
                    dpr=dpr,
                    phys_x=px,
                    phys_y=py,
                    phys_w=pw,
                    phys_h=ph,
                )
            )
        else:
            ecrans.append(
                GeometrieEcran(
                    log_x=g.x(),
                    log_y=g.y(),
                    log_w=g.width(),
                    log_h=g.height(),
                    dpr=dpr,
                )
            )

    if len(ecrans) == 1 or not win32_rects:
        prim = QGuiApplication.primaryScreen()
        prim_dpr = float(prim.devicePixelRatio() if prim else 1.0) or 1.0
        scr_at = QGuiApplication.screenAt(QPoint(round(x / prim_dpr), round(y / prim_dpr)))
        if scr_at is not None and len(ecrans) == 1:
            dpr = float(scr_at.devicePixelRatio() or 1.0)
            lx, ly, lw, lh = convertir_rect_physique_vers_logique(x, y, w, h, dpr_defaut=dpr)
            return QRect(lx, ly, lw, lh)

    lx, ly, lw, lh = convertir_rect_physique_vers_logique(x, y, w, h, ecrans=ecrans)
    return QRect(lx, ly, lw, lh)
