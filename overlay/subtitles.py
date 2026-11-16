"""Bulle de sous-titres transparente au-dessus de l'orbe (2 lignes max, fondu 6 s)."""

from __future__ import annotations

import re

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
    QTextLayout,
)
from PySide6.QtWidgets import QWidget

from overlay.win32_utils import configurer_fenetre_qt

LARGEUR_MAX_BULLE = 340
LARGEUR_MIN_BULLE = 150
DELAI_INACTIVITE_MS = 6000
INTERVALLE_FONDU_MS = 16
PAS_OPACITE_FONDU = 0.045

_RE_BALISES = re.compile(r"(\*\*|__|~~|`)")
_RE_TITRE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)


def compacter_texte_sous_titre(texte: str) -> str:
    """Nettoie un texte Markdown/chat en une ligne compacte pour les sous-titres."""
    if not texte:
        return ""
    propre = _RE_TITRE.sub("", texte)
    propre = _RE_BALISES.sub("", propre)
    return re.sub(r"\s+", " ", propre).strip()


class SubtitlesBubble(QWidget):
    """Bulle flottante traversable affichant la dernière narration ou réponse de Jarvis.

    Limitée à 2 lignes avec élision à droite et fondu progressif après 6 s d'inactivité.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise la bulle cachée avec timers d'inactivité (6 s) et de fondu."""
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

        self._font = QFont("Segoe UI", 10, QFont.Weight.Medium)
        self._texte_brut = ""
        self._tampon_chat = ""
        self._lignes: list[str] = []
        self._opacite = 1.0
        self._orb_rect = QRect(0, 0, 110, 110)

        self._timer_inactivite = QTimer(self)
        self._timer_inactivite.setSingleShot(True)
        self._timer_inactivite.setInterval(DELAI_INACTIVITE_MS)
        self._timer_inactivite.timeout.connect(self._demarrer_fondu)

        self._timer_fondu = QTimer(self)
        self._timer_fondu.setInterval(INTERVALLE_FONDU_MS)
        self._timer_fondu.timeout.connect(self._etape_fondu)

        self.hide()

    def showEvent(self, event: QShowEvent | None) -> None:
        """Applique les styles traversables et l'exclusion de capture à l'affichage."""
        super().showEvent(event)
        configurer_fenetre_qt(self, traversable=True, exclure_capture=True)

    def suivre_orbe(self, orb_rect: QRect) -> None:
        """Mémorise la géométrie de l'orbe et replace la bulle juste au-dessus."""
        self._orb_rect = QRect(orb_rect)
        if self.isVisible():
            self._repositionner()

    def on_narration(self, texte: str) -> None:
        """Affiche une narration temps réel de Jarvis."""
        self.afficher_texte(texte)

    def on_chat_debut(self, auteur: str) -> None:
        """Réinitialise le tampon de streaming lorsqu'une réponse de Jarvis démarre."""
        if auteur.strip().lower() == "jarvis":
            self._tampon_chat = ""

    def on_chat_delta(self, auteur: str, delta: str) -> None:
        """Ajoute un fragment de réponse ``jarvis`` et met à jour la bulle en direct."""
        if auteur.strip().lower() != "jarvis" or not delta:
            return
        self._tampon_chat += delta
        self.afficher_texte(self._tampon_chat)

    def on_chat_fin(self, auteur: str, contenu: str) -> None:
        """Affiche le texte final de la réponse ``jarvis`` et relance le décompte 6 s."""
        if auteur.strip().lower() != "jarvis":
            return
        self._tampon_chat = contenu or self._tampon_chat
        if self._tampon_chat.strip():
            self.afficher_texte(self._tampon_chat)

    def on_voix_partiel(self, texte: str) -> None:
        """Affiche en direct la transcription partielle de la parole utilisateur."""
        self._tampon_chat = ""
        if texte.strip():
            self.afficher_texte(texte)

    def on_voix_final(self, texte: str) -> None:
        """Affiche la transcription finale de l'utilisateur et réinitialise le tampon de réponse."""
        self._tampon_chat = ""
        if texte.strip():
            self.afficher_texte(texte)

    def on_voix_reponse_delta(self, delta: str) -> None:
        """Ajoute un fragment de réponse vocale ``reponse_delta`` et met à jour la bulle en direct."""
        if not delta:
            return
        self._tampon_chat += delta
        self.afficher_texte(self._tampon_chat)

    def afficher_texte(self, texte: str) -> None:
        """Met en forme le texte sur 2 lignes max, affiche la bulle et relance le fondu 6 s."""
        propre = compacter_texte_sous_titre(texte)
        if not propre:
            return

        self._texte_brut = propre
        self._lignes = self._decouper_deux_lignes(propre)
        self._timer_fondu.stop()
        self._opacite = 1.0
        self._recalculer_taille()
        self._repositionner()
        if not self.isVisible():
            self.show()
        self.update()
        self._timer_inactivite.start()

    def _decouper_deux_lignes(self, texte: str) -> list[str]:
        """Découpe ``texte`` en 2 lignes maximum avec élision à droite sur la seconde."""
        fm = QFontMetrics(self._font)
        largeur_texte_max = float(LARGEUR_MAX_BULLE - 28)

        layout = QTextLayout(texte, self._font)
        layout.beginLayout()
        l1 = layout.createLine()
        if not l1.isValid():
            layout.endLayout()
            return []
        l1.setLineWidth(largeur_texte_max)
        s1 = texte[l1.textStart() : l1.textStart() + l1.textLength()].rstrip()

        l2 = layout.createLine()
        layout.endLayout()
        if not l2.isValid():
            return [s1] if s1 else []

        reste = texte[l2.textStart() :].strip()
        if not reste:
            return [s1] if s1 else []
        s2 = fm.elidedText(reste, Qt.TextElideMode.ElideRight, int(largeur_texte_max))
        return [s1, s2]

    def _recalculer_taille(self) -> None:
        """Ajuste les dimensions de la bulle au contenu (1 ou 2 lignes)."""
        fm = QFontMetrics(self._font)
        largeurs = [fm.horizontalAdvance(ligne) for ligne in self._lignes] or [80]
        w = max(LARGEUR_MIN_BULLE, min(LARGEUR_MAX_BULLE, max(largeurs) + 28))
        nb_lignes = max(1, min(2, len(self._lignes)))
        h = nb_lignes * fm.lineSpacing() + 18
        self.resize(w, h)

    def _repositionner(self) -> None:
        """Centre la bulle au-dessus de l'orbe en restant dans les bornes de l'écran."""
        scr = QGuiApplication.screenAt(self._orb_rect.center()) or QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else QRect(0, 0, 1920, 1080)

        x = self._orb_rect.center().x() - self.width() // 2
        y = self._orb_rect.top() - self.height() - 10
        if y < avail.top() + 8:
            y = self._orb_rect.bottom() + 10

        x = max(avail.left() + 8, min(x, avail.right() - self.width() - 8))
        y = max(avail.top() + 8, min(y, avail.bottom() - self.height() - 8))
        self.move(QPoint(x, y))

    def _demarrer_fondu(self) -> None:
        """Lance la diminution progressive d'opacité après 6 s d'inactivité."""
        if self.isVisible():
            self._timer_fondu.start()

    def _etape_fondu(self) -> None:
        """Réduit l'opacité par palier puis masque la bulle lorsque transparente."""
        self._opacite = max(0.0, self._opacite - PAS_OPACITE_FONDU)
        if self._opacite <= 0.0:
            self._timer_fondu.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, event: QPaintEvent | None) -> None:
        """Dessine la bulle translucide sombre avec bordure cyan et texte antialiasé."""
        del event
        if not self._lignes or self._opacite <= 0.0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setOpacity(self._opacite)

        rect = QRectF(1.0, 1.0, float(self.width() - 2), float(self.height() - 2))
        painter.setBrush(QColor(8, 12, 24, 228))
        painter.setPen(QPen(QColor(34, 211, 238, 125), 1.2))
        painter.drawRoundedRect(rect, 12.0, 12.0)

        painter.setFont(self._font)
        painter.setPen(QColor(241, 245, 249))
        fm = QFontMetrics(self._font)
        y = 10 + fm.ascent()
        for ligne in self._lignes[:2]:
            painter.drawText(14, y, ligne)
            y += fm.lineSpacing()

        painter.end()
