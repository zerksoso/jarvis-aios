"""Calque HUD plein écran transparent et traversable pour les cadres de guidage visuel."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPolygonF,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from overlay.win32_utils import configurer_fenetre_qt, rect_physique_vers_logique

COULEUR_DEFAUT_HEX = "#22d3ee"
DUREE_APPARITION_S = 0.250
INTERVALLE_ANIM_MS = 16  # 60 i/s max uniquement lorsqu'un cadre est affiché

_RE_DEJA_NUMEROTE = re.compile(r"^\s*\d+\s*/\s*\d+\b")


@dataclass(frozen=True)
class BoiteGuidage:
    """Représentation logique Qt d'un cadre de guidage à dessiner sur le calque HUD."""

    rect_global: QRect
    label: str
    couleur: QColor


def formater_label_boite(label: str, index: int, total: int) -> str:
    """Formate l'étiquette numérotée affichée au-dessus d'un cadre de guidage.

    Évite de dupliquer le préfixe si ``label`` commence déjà par ``"1/2 …"``.

    Args:
        label: Libellé brut reçu dans ``guide``.
        index: Position base 0 du cadre.
        total: Nombre total de cadres dans le message ``guide``.

    Returns:
        Chaîne formatée prête pour la pastille HUD.
    """
    texte = label.strip()
    nb = max(1, total)
    if not texte:
        return f"{index + 1}/{nb}"
    if _RE_DEJA_NUMEROTE.match(texte):
        return texte
    return f"{index + 1}/{nb} {texte}"


def _parser_couleur(brut: object) -> QColor:
    """Analyse une couleur hex/nommée avec repli sur le cyan Jarvis."""
    if isinstance(brut, str) and brut.strip():
        col = QColor(brut.strip())
        if col.isValid():
            return col
    return QColor(COULEUR_DEFAUT_HEX)


class HudLayer(QWidget):
    """Calque plein écran couvrant le bureau virtuel pour guider l'utilisateur visuellement.

    Traversable à la souris (``WindowTransparentForInput`` + ``WS_EX_TRANSPARENT``), sans
    prise de focus et exclu des captures d'écran (``WDA_EXCLUDEFROMCAPTURE``).
    """

    guide_change = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise la fenêtre calque et le timer d'animation 60 i/s (arrêté au repos)."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._guide_id: str | None = None
        self._boites: list[BoiteGuidage] = []
        self._orbe_centre_global: QPoint | None = None
        self._apparition_debut: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(INTERVALLE_ANIM_MS)
        self._timer.timeout.connect(self.update)

        self._connecter_ecrans()
        self.sync_geometrie()

    def _connecter_ecrans(self) -> None:
        """Surveille l'ajout, le retrait ou le redimensionnement des écrans."""
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            return
        app.screenAdded.connect(lambda _scr: self.sync_geometrie())
        app.screenRemoved.connect(lambda _scr: self.sync_geometrie())
        app.primaryScreenChanged.connect(lambda _scr: self.sync_geometrie())
        prim = QGuiApplication.primaryScreen()
        if prim is not None:
            prim.virtualGeometryChanged.connect(lambda _rect: self.sync_geometrie())

    def sync_geometrie(self) -> None:
        """Aligne la géométrie du calque sur l'ensemble du bureau virtuel."""
        prim = QGuiApplication.primaryScreen()
        if prim is not None:
            self.setGeometry(prim.virtualGeometry())

    def showEvent(self, event: QShowEvent | None) -> None:
        """Applique les styles Win32 traversables et l'exclusion de capture à l'affichage."""
        super().showEvent(event)
        configurer_fenetre_qt(self, traversable=True, exclure_capture=True)

    def set_orbe_centre(self, centre_global: QPoint | None) -> None:
        """Met à jour la position du centre de l'orbe pour dessiner la flèche de guidage."""
        self._orbe_centre_global = QPoint(centre_global) if centre_global is not None else None
        if self._boites:
            self.update()

    def rect_boite_active(self) -> QRect | None:
        """Retourne le rectangle logique global du premier cadre actif, ou ``None``."""
        if not self._boites:
            return None
        return QRect(self._boites[0].rect_global)

    def afficher_guide(self, data: dict[str, Any]) -> None:
        """Affiche un ou plusieurs cadres de guidage reçus du WebSocket ``guide``.

        Convertit les coordonnées physiques de ``data["boites"]`` en coordonnées logiques Qt
        et lance l'animation d'apparition (250 ms) puis la pulsation à 60 i/s.

        Args:
            data: Charge utile ``{id, boites:[{x,y,w,h,label,couleur}], attendre_clic}``.
        """
        self.sync_geometrie()
        brutes = data.get("boites")
        if not isinstance(brutes, list) or not brutes:
            self.effacer_guide(None)
            return

        items_valides = [item for item in brutes if isinstance(item, dict)]
        total = len(items_valides)
        nouvelles: list[BoiteGuidage] = []
        for idx, item in enumerate(items_valides):
            try:
                x = float(item.get("x", 0))
                y = float(item.get("y", 0))
                w = float(item.get("w", 40))
                h = float(item.get("h", 40))
            except (TypeError, ValueError):
                continue
            rect_log = rect_physique_vers_logique(x, y, w, h)
            label = formater_label_boite(str(item.get("label") or ""), idx, total)
            couleur = _parser_couleur(item.get("couleur"))
            nouvelles.append(BoiteGuidage(rect_global=rect_log, label=label, couleur=couleur))

        if not nouvelles:
            self.effacer_guide(None)
            return

        guide_id_brut = data.get("id")
        self._guide_id = str(guide_id_brut) if guide_id_brut is not None else None
        self._boites = nouvelles
        self._apparition_debut = time.monotonic()
        if not self._timer.isActive():
            self._timer.start()
        self.guide_change.emit(self.rect_boite_active())
        self.update()

    def effacer_guide(self, guide_id: str | None = None) -> None:
        """Efface les cadres actifs (tous si ``guide_id`` est ``None``) et coupe le timer."""
        if guide_id is not None and self._guide_id is not None and str(guide_id) != self._guide_id:
            return
        if not self._boites and self._guide_id is None:
            return
        self._boites = []
        self._guide_id = None
        if self._timer.isActive():
            self._timer.stop()
        self.guide_change.emit(None)
        self.update()

    def _dessiner_fleche_orbe(
        self,
        painter: QPainter,
        origine_locale: QPointF,
        rect_cible: QRectF,
        couleur: QColor,
        opacite: float,
    ) -> None:
        """Dessine une ligne fine lumineuse et une flèche entre l'orbe et le cadre actif."""
        centre_cible = rect_cible.center()
        dx = centre_cible.x() - origine_locale.x()
        dy = centre_cible.y() - origine_locale.y()
        dist = math.hypot(dx, dy)
        if dist < 90.0:
            return

        ux = dx / dist
        uy = dy / dist
        depart = QPointF(origine_locale.x() + ux * 58.0, origine_locale.y() + uy * 58.0)

        # Arrêt sur le bord du rectangle cible plutôt qu'en son centre.
        demi_w = max(1.0, rect_cible.width() / 2.0 + 8.0)
        demi_h = max(1.0, rect_cible.height() / 2.0 + 8.0)
        tx = demi_w / abs(ux) if abs(ux) > 1e-4 else float("inf")
        ty = demi_h / abs(uy) if abs(uy) > 1e-4 else float("inf")
        retrait = min(tx, ty)
        arrivee = QPointF(centre_cible.x() - ux * retrait, centre_cible.y() - uy * retrait)

        if math.hypot(arrivee.x() - depart.x(), arrivee.y() - depart.y()) < 16.0:
            return

        c_halo = QColor(couleur)
        c_halo.setAlphaF(0.22 * opacite)
        pen_halo = QPen(c_halo, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_halo)
        painter.drawLine(depart, arrivee)

        c_ligne = QColor(couleur)
        c_ligne.setAlphaF(0.82 * opacite)
        pen_ligne = QPen(c_ligne, 1.8, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
        pen_ligne.setDashPattern([6.0, 5.0])
        painter.setPen(pen_ligne)
        painter.drawLine(depart, arrivee)

        # Pointe de flèche vers la cible.
        taille_pointe = 9.0
        angle = math.atan2(arrivee.y() - depart.y(), arrivee.x() - depart.x())
        p1 = QPointF(
            arrivee.x() - taille_pointe * math.cos(angle - math.pi / 6),
            arrivee.y() - taille_pointe * math.sin(angle - math.pi / 6),
        )
        p2 = QPointF(
            arrivee.x() - taille_pointe * math.cos(angle + math.pi / 6),
            arrivee.y() - taille_pointe * math.sin(angle + math.pi / 6),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(c_ligne)
        painter.drawPolygon(QPolygonF([arrivee, p1, p2]))

    def _dessiner_pastille(
        self,
        painter: QPainter,
        rect_local: QRectF,
        texte: str,
        couleur: QColor,
        opacite: float,
    ) -> None:
        """Dessine la pastille d'étiquette numérotée au-dessus (ou en dessous) du cadre."""
        font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        pad_x = 12
        hauteur_pil = 26
        largeur_texte = fm.horizontalAdvance(texte)
        largeur_pil = min(max(64, largeur_texte + pad_x * 2), max(120, self.width() - 24))

        px = max(8.0, min(rect_local.left(), float(self.width()) - largeur_pil - 8.0))
        py = rect_local.top() - hauteur_pil - 8.0
        if py < 8.0:
            py = min(float(self.height()) - hauteur_pil - 8.0, rect_local.bottom() + 8.0)

        pil_rect = QRectF(px, py, float(largeur_pil), float(hauteur_pil))

        fond = QColor(11, 16, 32, int(235 * opacite))
        bord = QColor(couleur)
        bord.setAlpha(int(220 * opacite))
        painter.setBrush(fond)
        painter.setPen(QPen(bord, 1.4))
        painter.drawRoundedRect(pil_rect, hauteur_pil / 2.0, hauteur_pil / 2.0)

        txt_col = QColor(248, 250, 252, int(255 * opacite))
        painter.setPen(txt_col)
        elide = fm.elidedText(texte, Qt.TextElideMode.ElideRight, largeur_pil - pad_x * 2)
        painter.drawText(pil_rect, Qt.AlignmentFlag.AlignCenter, elide)

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """Dessine les cadres lumineux, leur halo, la pastille et la flèche depuis l'orbe."""
        del event
        if not self._boites:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        maintenant = time.monotonic()
        ecoule = max(0.0, maintenant - self._apparition_debut)
        t = min(1.0, ecoule / DUREE_APPARITION_S)
        ease = 1.0 - (1.0 - t) ** 3
        pulsation = 0.5 + 0.5 * math.sin(ecoule * 3.8)

        decalage = self.geometry().topLeft()

        # Flèche de l'orbe vers le premier cadre actif.
        if self._orbe_centre_global is not None:
            orbe_local = QPointF(self._orbe_centre_global - decalage)
            cible_rect = QRectF(self._boites[0].rect_global.translated(-decalage))
            self._dessiner_fleche_orbe(painter, orbe_local, cible_rect, self._boites[0].couleur, ease)

        for boite in self._boites:
            rect_local = QRectF(boite.rect_global.translated(-decalage))
            expansion = (1.0 - ease) * 14.0
            rect_anim = rect_local.adjusted(-expansion, -expansion, expansion, expansion)

            # Halo multicouche doux + pulsation.
            for largeur_halo, alpha_base in ((12.0, 0.10), (7.0, 0.18), (4.0, 0.30)):
                col_halo = QColor(boite.couleur)
                col_halo.setAlphaF(alpha_base * (0.75 + 0.25 * pulsation) * ease)
                painter.setPen(QPen(col_halo, largeur_halo))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect_anim, 10.0, 10.0)

            # Fond subtil + bordure principale nette.
            remplissage = QColor(boite.couleur)
            remplissage.setAlphaF((0.06 + 0.04 * pulsation) * ease)
            bordure = QColor(boite.couleur)
            bordure.setAlphaF((0.86 + 0.14 * pulsation) * ease)

            chemin = QPainterPath()
            chemin.addRoundedRect(rect_anim, 10.0, 10.0)
            painter.fillPath(chemin, remplissage)
            painter.setPen(QPen(bordure, 2.2))
            painter.drawPath(chemin)

            self._dessiner_pastille(painter, rect_anim, boite.label, boite.couleur, ease)

        painter.end()
