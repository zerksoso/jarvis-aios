"""Orbe flottant animé Jarvis (110 px) : machine d'états, peinture QPainter et interactions."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QMouseEvent,
    QMoveEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QRadialGradient,
    QShowEvent,
)
from PySide6.QtWidgets import QWidget

from overlay.win32_utils import configurer_fenetre_qt

TAILLE_ORBE = 110
MARGE_ECRAN = 32
ECART_CADRE = 24
INTERVALLE_ACTIF_MS = 16  # 60 i/s max quand actif ou en mouvement
INTERVALLE_REPOS_MS = 50  # 20 i/s au repos / déconnecté

ETATS_ORBE = ("repos", "ecoute", "reflexion", "parole", "action", "deconnecte")


@dataclass(frozen=True)
class PresetOrbe:
    """Paramètres visuels et temporels associés à un état de l'orbe."""

    couleur_hex: str
    couleur_secondaire_hex: str
    vitesse_rot: float
    amplitude_pulsation: float
    freq_pulsation: float
    fps: int


PRESETS_ORBE: dict[str, PresetOrbe] = {
    "repos": PresetOrbe("#22d3ee", "#0891b2", 0.55, 0.04, 1.8, 20),
    "ecoute": PresetOrbe("#38bdf8", "#0284c7", 1.15, 0.11, 4.4, 60),
    "reflexion": PresetOrbe("#a78bfa", "#7c3aed", 1.65, 0.06, 3.2, 60),
    "parole": PresetOrbe("#2dd4bf", "#0d9488", 1.10, 0.13, 6.0, 60),
    "action": PresetOrbe("#f59e0b", "#d97706", 2.30, 0.08, 5.2, 60),
    "deconnecte": PresetOrbe("#64748b", "#475569", 0.22, 0.02, 1.0, 20),
}


class MachineEtatsOrbe:
    """Machine d'états pure de l'orbe (indépendante de toute fenêtre Qt)."""

    def __init__(self, connecte: bool = False, etat_initial: str = "repos") -> None:
        """Initialise l'état de connexion, l'état backend et l'indicateur de déplacement."""
        self.connecte = connecte
        self.etat_backend = etat_initial if etat_initial in ETATS_ORBE and etat_initial != "deconnecte" else "repos"
        self.en_mouvement = False

    @property
    def etat_effectif(self) -> str:
        """Retourne l'état actif affiché (``"deconnecte"`` si hors ligne)."""
        if not self.connecte:
            return "deconnecte"
        return self.etat_backend

    def set_connecte(self, connecte: bool) -> str:
        """Met à jour l'état de connexion au backend et retourne l'état effectif."""
        self.connecte = bool(connecte)
        if not self.connecte:
            # L'état du backend est inconnu après une coupure : on repartira au repos.
            self.etat_backend = "repos"
            return "deconnecte"
        if self.etat_backend not in ETATS_ORBE or self.etat_backend == "deconnecte":
            self.etat_backend = "repos"
        return self.etat_effectif

    def set_etat(self, etat: str) -> str:
        """Applique une transition d'état validée et retourne l'état effectif résultant."""
        normalise = (etat or "").strip().lower()
        if normalise == "deconnecte":
            self.connecte = False
            return "deconnecte"
        if normalise not in ETATS_ORBE:
            normalise = "repos"
        self.etat_backend = normalise
        return self.etat_effectif

    def set_en_mouvement(self, en_mouvement: bool) -> None:
        """Indique si l'orbe glisse actuellement vers ou depuis une cible."""
        self.en_mouvement = bool(en_mouvement)

    def preset(self) -> PresetOrbe:
        """Retourne le préréglage visuel correspondant à ``etat_effectif``."""
        return PRESETS_ORBE[self.etat_effectif]

    def intervalle_timer_ms(self) -> int:
        """Retourne l'intervalle ``QTimer`` (16 ms à 60 i/s si actif/mobile, 50 ms à 20 i/s au repos)."""
        if self.en_mouvement or self.preset().fps >= 60:
            return INTERVALLE_ACTIF_MS
        return INTERVALLE_REPOS_MS


def calculer_position_repos(
    avail_x: int,
    avail_y: int,
    avail_w: int,
    avail_h: int,
    taille: int = TAILLE_ORBE,
    marge: int = MARGE_ECRAN,
) -> tuple[int, int]:
    """Calcule le coin haut-gauche par défaut de l'orbe en bas à droite de l'écran principal."""
    x = avail_x + max(0, avail_w - taille - marge)
    y = avail_y + max(0, avail_h - taille - marge)
    return x, y


def _clamped_pos(
    x: int,
    y: int,
    avail: tuple[int, int, int, int],
    taille: int,
    marge: int = 8,
) -> tuple[int, int]:
    """Contraint une position d'orbe à l'intérieur d'un rectangle disponible."""
    ax, ay, aw, ah = avail
    min_x = ax + marge
    min_y = ay + marge
    max_x = max(min_x, ax + aw - taille - marge)
    max_y = max(min_y, ay + ah - taille - marge)
    return max(min_x, min(x, max_x)), max(min_y, min(y, max_y))


def calculer_position_pres_de_cadre(
    cadre: tuple[int, int, int, int],
    avail: tuple[int, int, int, int],
    taille: int = TAILLE_ORBE,
    ecart: int = ECART_CADRE,
) -> tuple[int, int]:
    """Calcule une position d'orbe sans recouvrement à côté d'un cadre de guidage actif.

    Essaie dans l'ordre : droite, gauche, dessous, dessus, en restant dans ``avail``.
    """
    cx, cy, cw, ch = cadre
    ax, ay, aw, ah = avail
    centre_y = cy + (ch - taille) // 2
    centre_x = cx + (cw - taille) // 2

    candidats = [
        (cx + cw + ecart, centre_y),
        (cx - taille - ecart, centre_y),
        (centre_x, cy + ch + ecart),
        (centre_x, cy - taille - ecart),
    ]
    for px, py in candidats:
        if (
            px >= ax + 8
            and py >= ay + 8
            and px + taille <= ax + aw - 8
            and py + taille <= ay + ah - 8
        ):
            return px, py

    return _clamped_pos(candidats[0][0], candidats[0][1], avail, taille)


def borner_niveau(niveau: float) -> float:
    """Borne un niveau audio dans ``[0.0, 1.0]`` en ignorant ``NaN`` et infinis.

    Args:
        niveau: Valeur brute de niveau audio.

    Returns:
        Flottant fini compris entre ``0.0`` et ``1.0``.
    """
    try:
        valeur = float(niveau)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(valeur):
        return 0.0
    return max(0.0, min(1.0, valeur))


def lisser_niveau(courant: float, cible: float, dt: float, tau: float = 0.08) -> float:
    """Applique un lissage exponentiel entre le niveau affiché et le niveau mesuré.

    Args:
        courant: Niveau lissé précédent dans ``[0.0, 1.0]``.
        cible: Niveau instantané mesuré dans ``[0.0, 1.0]``.
        dt: Pas de temps écoulé en secondes.
        tau: Constante de temps du filtre exponentiel en secondes (0,08 s par défaut).

    Returns:
        Nouveau niveau lissé dans ``[0.0, 1.0]``.
    """
    c = borner_niveau(courant)
    t = borner_niveau(cible)
    if dt <= 0.0 or tau <= 0.0:
        return t
    alpha = 1.0 - math.exp(-dt / tau)
    return borner_niveau(c + (t - c) * alpha)


class OrbWidget(QWidget):
    """Widget flottant circulaire 110 px représentant l'état temps réel de Jarvis.

    Fenêtre interactive sans bordure, toujours au premier plan, non activante au repos,
    exclue des captures d'écran Windows.
    """

    clic_ecoute = Signal()
    double_clic_dashboard = Signal()
    position_changee = Signal(QRect)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise l'orbe en état ``deconnecte`` en bas à droite de l'écran principal."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(TAILLE_ORBE, TAILLE_ORBE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._machine = MachineEtatsOrbe(connecte=False, etat_initial="repos")
        self._debut_anim = time.monotonic()
        self._dernier_tick = self._debut_anim

        # Couleur interpolée courante pour des transitions d'état fluides.
        preset_init = self._machine.preset()
        self._couleur_courante = QColor(preset_init.couleur_hex)
        self._couleur_sec_courante = QColor(preset_init.couleur_secondaire_hex)
        self._phase = 0.0
        self._niveau_cible = 0.0
        self._niveau_lisse = 0.0

        self._home_pos = self._calculer_home_pos()
        self._cible_pos: QPoint | None = None
        self._pos_flottante = QPointF(float(self._home_pos.x()), float(self._home_pos.y()))
        self.move(self._home_pos)

        # Gestion souris : distinction clic simple / double-clic / glisser.
        self._en_glisser = False
        self._a_bouge = False
        self._press_global = QPoint()
        self._offset_drag = QPoint()
        self._timer_clic = QTimer(self)
        self._timer_clic.setSingleShot(True)
        self._timer_clic.setInterval(220)
        self._timer_clic.timeout.connect(self.clic_ecoute.emit)

        self._timer = QTimer(self)
        self._timer.setInterval(self._machine.intervalle_timer_ms())
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @property
    def etat(self) -> str:
        """Retourne l'état courant affiché par l'orbe."""
        return self._machine.etat_effectif

    @property
    def niveau(self) -> float:
        """Retourne le niveau audio lissé courant (0–1)."""
        return self._niveau_lisse

    @property
    def niveau_cible(self) -> float:
        """Retourne le dernier niveau audio cible reçu via ``set_niveau`` (0–1)."""
        return self._niveau_cible

    def set_niveau(self, niveau: float) -> None:
        """Définit le niveau audio cible (0–1) modulant le rayon et le halo en douceur.

        Args:
            niveau: Amplitude normalisée (micro en écoute ou sortie Windows en parole).
        """
        self._niveau_cible = borner_niveau(niveau)

    def _ecran_disponible_tuple(self, point_ref: QPoint | None = None) -> tuple[int, int, int, int]:
        """Retourne ``(x, y, w, h)`` de la zone utile de l'écran concerné."""
        scr = QGuiApplication.screenAt(point_ref) if point_ref is not None else None
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        if scr is None:
            return (0, 0, 1920, 1080)
        g = scr.availableGeometry()
        return g.x(), g.y(), g.width(), g.height()

    def _calculer_home_pos(self) -> QPoint:
        """Calcule la position par défaut en bas à droite de l'écran principal."""
        ax, ay, aw, ah = self._ecran_disponible_tuple(None)
        hx, hy = calculer_position_repos(ax, ay, aw, ah, TAILLE_ORBE, MARGE_ECRAN)
        return QPoint(hx, hy)

    def _maj_cadence_timer(self) -> None:
        """Ajuste l'intervalle du timer entre 50 ms (20 i/s) et 16 ms (60 i/s)."""
        cible = self._machine.intervalle_timer_ms()
        if self._timer.interval() != cible:
            self._timer.setInterval(cible)

    def set_connecte(self, connecte: bool) -> None:
        """Met à jour l'état de connexion WebSocket de l'orbe."""
        effectif = self._machine.set_connecte(connecte)
        if effectif in ("repos", "deconnecte"):
            self._niveau_cible = 0.0
        self._maj_cadence_timer()

    def set_etat(self, etat: str) -> None:
        """Change l'état visuel de l'orbe (``repos``, ``ecoute``, ``reflexion``, ``parole``, ``action``, ``deconnecte``)."""
        effectif = self._machine.set_etat(etat)
        if effectif in ("repos", "deconnecte"):
            self._niveau_cible = 0.0
        self._maj_cadence_timer()

    def deplacer_pres_de(self, rect_global: QRect | None) -> None:
        """Fait glisser l'orbe près d'un cadre actif, ou le ramène à sa position d'ancrage."""
        if rect_global is None:
            self._cible_pos = QPoint(self._home_pos)
            self._machine.set_en_mouvement(True)
            self._maj_cadence_timer()
            return

        avail = self._ecran_disponible_tuple(rect_global.center())
        tx, ty = calculer_position_pres_de_cadre(
            (rect_global.x(), rect_global.y(), rect_global.width(), rect_global.height()),
            avail,
            TAILLE_ORBE,
            ECART_CADRE,
        )
        self._cible_pos = QPoint(tx, ty)
        self._pos_flottante = QPointF(float(self.x()), float(self.y()))
        self._machine.set_en_mouvement(True)
        self._maj_cadence_timer()

    def showEvent(self, event: QShowEvent | None) -> None:
        """Configure les styles Win32 non traversables + exclusion capture à l'affichage."""
        super().showEvent(event)
        configurer_fenetre_qt(self, traversable=False, exclure_capture=True)
        self.position_changee.emit(self.geometry())

    def moveEvent(self, event: QMoveEvent | None) -> None:
        """Notifie les widgets suiveurs (sous-titres, flèche HUD) à chaque déplacement."""
        super().moveEvent(event)
        self.position_changee.emit(self.geometry())

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """Démarre le suivi d'un éventuel glisser-déposer."""
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._en_glisser = True
        self._a_bouge = False
        self._press_global = event.globalPosition().toPoint()
        self._offset_drag = self._press_global - self.pos()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        """Déplace l'orbe si le curseur dépasse le seuil de glissement."""
        if event is None or not self._en_glisser:
            return
        pos_globale = event.globalPosition().toPoint()
        if not self._a_bouge and (pos_globale - self._press_global).manhattanLength() > 5:
            self._a_bouge = True
            self._timer_clic.stop()
            self._cible_pos = None
            self._machine.set_en_mouvement(False)

        if self._a_bouge:
            nouvelle = pos_globale - self._offset_drag
            self.move(nouvelle)
            self._pos_flottante = QPointF(float(nouvelle.x()), float(nouvelle.y()))
            self._home_pos = QPoint(nouvelle)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        """Déclenche le clic simple (après délai anti double-clic) ou finalise le glisser."""
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._en_glisser and not self._a_bouge:
            self._timer_clic.start()
        self._en_glisser = False
        self._a_bouge = False
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        """Annule le clic simple en attente et ouvre le tableau de bord web."""
        if event is None or event.button() != Qt.MouseButton.LeftButton:
            return
        self._timer_clic.stop()
        self._en_glisser = False
        self._a_bouge = False
        self.double_clic_dashboard.emit()
        event.accept()

    @staticmethod
    def _interpoler_couleur(courante: QColor, cible: QColor, alpha: float) -> QColor:
        """Interpole linéairement deux couleurs ``QColor`` selon ``alpha``."""
        r = round(courante.red() + (cible.red() - courante.red()) * alpha)
        g = round(courante.green() + (cible.green() - courante.green()) * alpha)
        b = round(courante.blue() + (cible.blue() - courante.blue()) * alpha)
        return QColor(r, g, b)

    def _tick(self) -> None:
        """Met à jour la phase d'animation, l'interpolation de couleur et le glissement doux."""
        maintenant = time.monotonic()
        dt = min(0.08, max(0.001, maintenant - self._dernier_tick))
        self._dernier_tick = maintenant

        preset = self._machine.preset()
        self._phase += dt * preset.vitesse_rot
        self._niveau_lisse = lisser_niveau(self._niveau_lisse, self._niveau_cible, dt)

        alpha_col = 1.0 - math.exp(-dt / 0.16)
        self._couleur_courante = self._interpoler_couleur(
            self._couleur_courante, QColor(preset.couleur_hex), alpha_col
        )
        self._couleur_sec_courante = self._interpoler_couleur(
            self._couleur_sec_courante, QColor(preset.couleur_secondaire_hex), alpha_col
        )

        if self._cible_pos is not None and not self._en_glisser:
            alpha_pos = 1.0 - math.exp(-dt / 0.11)
            nx = self._pos_flottante.x() + (self._cible_pos.x() - self._pos_flottante.x()) * alpha_pos
            ny = self._pos_flottante.y() + (self._cible_pos.y() - self._pos_flottante.y()) * alpha_pos
            self._pos_flottante = QPointF(nx, ny)
            dx = abs(self._cible_pos.x() - nx)
            dy = abs(self._cible_pos.y() - ny)
            if dx < 1.0 and dy < 1.0:
                self.move(self._cible_pos)
                self._cible_pos = None
                self._machine.set_en_mouvement(False)
                self._maj_cadence_timer()
            else:
                self.move(round(nx), round(ny))

        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """Dessine l'orbe dégradé, les anneaux orbitaux animés et l'onde vocale."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0
        ecoule = time.monotonic() - self._debut_anim

        preset = self._machine.preset()
        etat = self._machine.etat_effectif
        col = self._couleur_courante
        col_sec = self._couleur_sec_courante
        puls = math.sin(ecoule * preset.freq_pulsation)
        niv = self._niveau_lisse

        # 1. Halo radial externe modulé par l'amplitude vocale.
        rayon_halo = min(
            w * 0.495,
            (w * 0.46) * (1.0 + puls * preset.amplitude_pulsation * 0.35 + niv * 0.12),
        )
        halo = QRadialGradient(cx, cy, rayon_halo)
        c0 = QColor(col)
        c0.setAlpha(95 if etat != "deconnecte" else 42)
        c1 = QColor(col_sec)
        c1.setAlpha(35 if etat != "deconnecte" else 14)
        c2 = QColor(col_sec)
        c2.setAlpha(0)
        halo.setColorAt(0.0, c0)
        halo.setColorAt(0.6, c1)
        halo.setColorAt(1.0, c2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(QPointF(cx, cy), rayon_halo, rayon_halo)

        # 2. Anneaux orbitaux animés.
        specs_anneaux = [
            (40.0, 0.56, self._phase * 58.0, 1.8, 175),
            (45.0, 0.42, -self._phase * 42.0 + 48.0, 1.3, 125),
        ]
        for rayon_a, ratio_y, angle_deg, epaisseur, alpha_a in specs_anneaux:
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(angle_deg)
            c_ring = QColor(col)
            c_ring.setAlpha(alpha_a if etat != "deconnecte" else 75)
            pen = QPen(c_ring, epaisseur)
            if etat == "deconnecte":
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(0.0, 0.0), rayon_a, rayon_a * ratio_y)
            painter.restore()

        # 3. Onde supplémentaire en écoute / parole / action.
        if etat in ("ecoute", "parole", "action"):
            onde = 0.5 + 0.5 * math.sin(ecoule * 7.5)
            r_onde = 32.0 + onde * 14.0
            c_onde = QColor(col)
            c_onde.setAlpha(int(165 * (1.0 - onde * 0.7)))
            painter.setPen(QPen(c_onde, 1.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), r_onde, r_onde)

        # 4. Sphère principale dégradée (obsidian + reflet spéculaire) modulée par le niveau.
        rayon_sphere = 28.0 * (1.0 + puls * preset.amplitude_pulsation * 0.45 + niv * 0.18)
        grad_sphere = QRadialGradient(cx - rayon_sphere * 0.30, cy - rayon_sphere * 0.32, rayon_sphere * 1.45)
        reflet = QColor(232, 248, 255) if etat != "deconnecte" else QColor(148, 163, 184)
        grad_sphere.setColorAt(0.0, reflet)
        grad_sphere.setColorAt(0.28, col)
        grad_sphere.setColorAt(0.75, col_sec)
        grad_sphere.setColorAt(1.0, QColor(7, 11, 22, 245))

        painter.setBrush(grad_sphere)
        c_bord = QColor(col)
        c_bord.setAlpha(210 if etat != "deconnecte" else 120)
        painter.setPen(QPen(c_bord, 1.5))
        painter.drawEllipse(QPointF(cx, cy), rayon_sphere, rayon_sphere)

        # 5. Noyau central / barres d'amplitude vocale en état parole.
        if etat == "parole":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(248, 250, 252, 225))
            echelle_barre = 0.45 + 0.55 * max(niv, 0.15)
            for idx, offset_x in enumerate((-10.0, -3.5, 3.5, 10.0)):
                hauteur_b = 5.0 + 13.0 * echelle_barre * abs(math.sin(ecoule * 10.5 + idx * 1.1))
                painter.drawRoundedRect(
                    QRectF(cx + offset_x - 1.8, cy - hauteur_b / 2.0, 3.6, hauteur_b),
                    1.8,
                    1.8,
                )
        else:
            r_noyau = 7.5 + (2.5 if etat in ("ecoute", "reflexion", "action") else 0.0) * (0.5 + 0.5 * puls)
            noyau = QRadialGradient(cx, cy, r_noyau)
            noyau.setColorAt(0.0, QColor(255, 255, 255, 215 if etat != "deconnecte" else 90))
            noyau.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(noyau)
            painter.drawEllipse(QPointF(cx, cy), r_noyau, r_noyau)

        painter.end()
