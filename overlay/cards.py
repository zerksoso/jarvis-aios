"""Cartes HUD empilées sur le bord droit (largeur 380 px, Markdown, max 4 cartes, approbations)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from overlay.win32_utils import configurer_fenetre_qt

LARGEUR_CARTE = 380
MAX_CARTES = 4
MARGE_DROITE = 20
MARGE_HAUT = 24


@dataclass
class CarteOverlay:
    """Modèle de données d'une carte HUD ou d'une demande d'approbation."""

    id: str
    titre: str
    contenu_md: str
    type: str = "info"
    epinglee: bool = False
    est_approbation: bool = False
    risque: str = ""
    source: str = ""
    action: dict[str, Any] = field(default_factory=dict)


class FileCartes:
    """File bornée des cartes HUD actives (sans dépendance graphique Qt)."""

    def __init__(self, max_cartes: int = MAX_CARTES) -> None:
        """Initialise la file avec une capacité maximale (4 par défaut)."""
        if max_cartes < 1:
            raise ValueError("max_cartes doit être >= 1.")
        self.max_cartes = max_cartes
        self._cartes: list[CarteOverlay] = []

    def ajouter(self, carte: CarteOverlay) -> list[CarteOverlay]:
        """Ajoute ou met à jour une carte en tête et évince l'excédent au-delà de ``max_cartes``."""
        self._cartes = [c for c in self._cartes if c.id != carte.id]
        self._cartes.insert(0, carte)
        if len(self._cartes) > self.max_cartes:
            self._cartes = self._cartes[: self.max_cartes]
        return list(self._cartes)

    def marquer_epinglee(self, carte_id: str, epinglee: bool = True) -> bool:
        """Met à jour l'état épinglé d'une carte présente dans la file."""
        for carte in self._cartes:
            if carte.id == carte_id:
                carte.epinglee = epinglee
                return True
        return False

    def retirer(self, carte_id: str) -> bool:
        """Supprime la carte identifiée par ``carte_id`` si présente."""
        avant = len(self._cartes)
        self._cartes = [c for c in self._cartes if c.id != carte_id]
        return len(self._cartes) != avant

    def vider(self) -> None:
        """Supprime toutes les cartes de la file."""
        self._cartes.clear()

    def lister(self) -> list[CarteOverlay]:
        """Retourne une copie ordonnée (plus récente en premier) des cartes actives."""
        return list(self._cartes)


def _construire_markdown_approbation(data: dict[str, Any]) -> str:
    """Formate le corps Markdown d'une demande d'approbation utilisateur."""
    resume = str(data.get("resume") or "Action sensible en attente d'autorisation.").strip()
    risque = str(data.get("risque") or "irreversible").strip()
    action = data.get("action")
    lignes = [resume, "", f"**Risque :** `{risque}`"]
    if isinstance(action, dict) and action:
        detail = json.dumps(action, ensure_ascii=False, indent=2)
        lignes.extend(["", "```json", detail, "```"])
    return "\n".join(lignes)


class CarteWidget(QFrame):
    """Carte HUD translucide (cyan pour info, ambre pour demande d'approbation)."""

    epingler_clique = Signal(str)
    fermer_clique = Signal(str)
    approbation_decidee = Signal(str, str)

    def __init__(self, carte: CarteOverlay, parent: QWidget | None = None) -> None:
        """Construit l'interface d'une carte HUD ou d'approbation avec défilement interne."""
        super().__init__(parent)
        self._carte = carte
        self.setFixedWidth(LARGEUR_CARTE)

        accent = "#f59e0b" if carte.est_approbation else "#22d3ee"
        bord_alpha = "rgba(245, 158, 11, 0.72)" if carte.est_approbation else "rgba(34, 211, 238, 0.42)"
        fond = "rgba(18, 15, 10, 238)" if carte.est_approbation else "rgba(10, 15, 30, 235)"

        self.setObjectName("JarvisHudCard")
        self.setStyleSheet(
            f"""
            QFrame#JarvisHudCard {{
                background-color: {fond};
                border: 1px solid {bord_alpha};
                border-radius: 12px;
            }}
            QLabel#BadgeType {{
                color: {accent};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.6px;
                text-transform: uppercase;
            }}
            QLabel#TitreCarte {{
                color: #f8fafc;
                font-size: 13px;
                font-weight: 600;
            }}
            QTextBrowser {{
                background: transparent;
                border: none;
                color: #e2e8f0;
                font-size: 12px;
                selection-background-color: rgba(34, 211, 238, 0.35);
            }}
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
                color: #e2e8f0;
                font-size: 11px;
                font-weight: 600;
                padding: 4px 10px;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.14);
            }}
            QPushButton#BtnAutoriser {{
                background-color: rgba(245, 158, 11, 0.24);
                border: 1px solid rgba(245, 158, 11, 0.75);
                color: #fbbf24;
            }}
            QPushButton#BtnAutoriser:hover {{
                background-color: rgba(245, 158, 11, 0.38);
            }}
            QPushButton#BtnRefuser:hover {{
                background-color: rgba(248, 113, 113, 0.22);
                border-color: rgba(248, 113, 113, 0.65);
                color: #fca5a5;
            }}
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # En-tête : badge type/source + boutons compacts ou titre.
        entete = QHBoxLayout()
        entete.setContentsMargins(0, 0, 0, 0)
        entete.setSpacing(6)

        badge_texte = (
            f"Accord requis · {carte.source or 'jarvis'}"
            if carte.est_approbation
            else (carte.type or "info")
        )
        badge = QLabel(badge_texte.upper(), self)
        badge.setObjectName("BadgeType")
        entete.addWidget(badge)
        entete.addStretch(1)
        layout.addLayout(entete)

        titre = QLabel(carte.titre, self)
        titre.setObjectName("TitreCarte")
        titre.setWordWrap(True)
        layout.addWidget(titre)

        contenu = QTextBrowser(self)
        contenu.setFrameShape(QFrame.Shape.NoFrame)
        contenu.setOpenExternalLinks(True)
        contenu.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contenu.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        vp = contenu.viewport()
        if vp is not None:
            vp.setAutoFillBackground(False)
        contenu.setMarkdown(carte.contenu_md or "")
        contenu.document().setTextWidth(float(LARGEUR_CARTE - 36))
        hauteur_doc = int(contenu.document().size().height()) + 10
        contenu.setFixedHeight(max(44, min(160, hauteur_doc)))
        layout.addWidget(contenu)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 2, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)

        if carte.est_approbation:
            btn_refuser = QPushButton("Refuser", self)
            btn_refuser.setObjectName("BtnRefuser")
            btn_refuser.clicked.connect(lambda _checked=False: self.approbation_decidee.emit(carte.id, "non"))

            btn_autoriser = QPushButton("Autoriser", self)
            btn_autoriser.setObjectName("BtnAutoriser")
            btn_autoriser.clicked.connect(lambda _checked=False: self.approbation_decidee.emit(carte.id, "oui"))

            actions.addWidget(btn_refuser)
            actions.addWidget(btn_autoriser)
        else:
            lib_epingler = "Épinglée" if carte.epinglee else "Épingler"
            btn_epingler = QPushButton(lib_epingler, self)
            btn_epingler.clicked.connect(lambda _checked=False: self.epingler_clique.emit(carte.id))

            btn_fermer = QPushButton("Fermer", self)
            btn_fermer.clicked.connect(lambda _checked=False: self.fermer_clique.emit(carte.id))

            actions.addWidget(btn_epingler)
            actions.addWidget(btn_fermer)

        layout.addLayout(actions)


class CardsContainer(QWidget):
    """Conteneur interactif empilant jusqu'à 4 cartes HUD sur le bord droit de l'écran."""

    epingler_demande = Signal(str)
    approbation_repondue = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialise la fenêtre conteneur sans bordure et masquée tant qu'elle est vide."""
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(LARGEUR_CARTE)

        self._file = FileCartes(max_cartes=MAX_CARTES)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)

        self.hide()

    def showEvent(self, event: QShowEvent | None) -> None:
        """Applique les styles non traversables et l'exclusion de capture à l'affichage."""
        super().showEvent(event)
        configurer_fenetre_qt(self, traversable=False, exclure_capture=True)

    def ajouter_carte(self, data: dict[str, Any]) -> None:
        """Ajoute ou met à jour une carte standard reçue de l'événement WS ``carte``."""
        carte_id = str(data.get("id") or "").strip()
        if not carte_id:
            return
        carte = CarteOverlay(
            id=carte_id,
            titre=str(data.get("titre") or "Information").strip(),
            contenu_md=str(data.get("contenu_md") or ""),
            type=str(data.get("type") or "info").strip(),
            epinglee=bool(data.get("epinglee", False)),
            est_approbation=False,
        )
        self._file.ajouter(carte)
        self._rafraichir_widgets()

    def ajouter_approbation(self, data: dict[str, Any]) -> None:
        """Affiche une demande d'approbation WS ``approbation_demande`` en carte ambre."""
        app_id = str(data.get("id") or "").strip()
        if not app_id:
            return
        source = str(data.get("source") or "jarvis").strip()
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        carte = CarteOverlay(
            id=app_id,
            titre=f"Confirmation d'action ({source})",
            contenu_md=_construire_markdown_approbation(data),
            type="approbation",
            epinglee=False,
            est_approbation=True,
            risque=str(data.get("risque") or "irreversible"),
            source=source,
            action=action,
        )
        self._file.ajouter(carte)
        self._rafraichir_widgets()

    def retirer_carte(self, carte_id: str) -> None:
        """Retire une carte (ou approbation résolue) et masque la pile si vide."""
        if not carte_id:
            return
        if self._file.retirer(carte_id):
            self._rafraichir_widgets()

    def _on_epingler(self, carte_id: str) -> None:
        """Relaye la demande d'épinglage WS et met à jour l'état visuel du bouton."""
        self._file.marquer_epinglee(carte_id, True)
        self.epingler_demande.emit(carte_id)
        self._rafraichir_widgets()

    def _on_approbation(self, app_id: str, decision: str) -> None:
        """Transmet la décision ``oui``/``non`` et ferme la carte d'approbation."""
        self.approbation_repondue.emit(app_id, decision)
        self.retirer_carte(app_id)

    def _rafraichir_widgets(self) -> None:
        """Reconstruit la pile visuelle des cartes actives et ajuste sa position à droite."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        cartes = self._file.lister()
        if not cartes:
            self.hide()
            return

        for carte in cartes:
            w = CarteWidget(carte, self)
            w.epingler_clique.connect(self._on_epingler)
            w.fermer_clique.connect(self.retirer_carte)
            w.approbation_decidee.connect(self._on_approbation)
            self._layout.addWidget(w)

        self._layout.activate()
        self.adjustSize()
        self._repositionner()
        if not self.isVisible():
            self.show()

    def _repositionner(self) -> None:
        """Ancre la pile de cartes sur le bord droit de l'écran principal."""
        scr = QGuiApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else QRect(0, 0, 1920, 1080)
        x = avail.right() - LARGEUR_CARTE - MARGE_DROITE + 1
        y = avail.top() + MARGE_HAUT
        self.move(QPoint(max(avail.left() + 8, x), y))
