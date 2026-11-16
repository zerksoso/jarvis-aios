"""Point d'entrée de l'overlay desktop transparent PySide6 (processus sans console)."""

from __future__ import annotations

import logging
import sys
import webbrowser
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from overlay import obtenir_racine
from overlay.cards import CardsContainer
from overlay.client import JarvisClient
from overlay.hud_layer import HudLayer
from overlay.orb import OrbWidget
from overlay.subtitles import SubtitlesBubble
from overlay.voice import GlobalHotkeyListener, VoiceOrchestrator
from overlay.voice_stream import ConversationVocaleOverlay
from overlay.win32_utils import activer_dpi

DASHBOARD_URL = "http://127.0.0.1:5230"
logger = logging.getLogger("overlay")


def _configurer_logs() -> None:
    """Configure un fichier log rotatif dans ``runtime/logs/overlay.log``.

    Évite d'écrire sur ``sys.stderr`` lorsque celui-ci est ``None`` sous ``pythonw.exe``.

    Effets de bord:
        Crée ``runtime/logs/`` et attache un ``RotatingFileHandler`` au logger racine.
    """
    log_dir = obtenir_racine() / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "overlay.log"

    racine = logging.getLogger()
    if racine.handlers:
        return
    racine.setLevel(logging.INFO)
    formateur = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler_fichier = RotatingFileHandler(
        log_file,
        maxBytes=1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler_fichier.setFormatter(formateur)
    racine.addHandler(handler_fichier)

    if sys.stderr is not None:
        handler_console = logging.StreamHandler()
        handler_console.setFormatter(formateur)
        racine.addHandler(handler_console)


def _creer_icone_tray() -> QIcon:
    """Dessine une icône vectorielle cyan de l'orbe Jarvis pour la zone de notification."""
    taille = 64
    pixmap = QPixmap(taille, taille)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    halo = QRadialGradient(taille / 2, taille / 2, taille * 0.48)
    halo.setColorAt(0.0, QColor(34, 211, 238, 220))
    halo.setColorAt(0.55, QColor(8, 145, 178, 170))
    halo.setColorAt(1.0, QColor(8, 145, 178, 0))
    painter.setBrush(halo)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, taille - 4, taille - 4)

    coeur = QRadialGradient(taille * 0.42, taille * 0.38, taille * 0.32)
    coeur.setColorAt(0.0, QColor(224, 247, 250))
    coeur.setColorAt(0.45, QColor(34, 211, 238))
    coeur.setColorAt(1.0, QColor(12, 74, 110))
    painter.setBrush(coeur)
    painter.drawEllipse(14, 14, taille - 28, taille - 28)
    painter.end()
    return QIcon(pixmap)


def _ouvrir_tableau_de_bord() -> None:
    """Ouvre l'interface web Jarvis dans le navigateur par défaut."""
    webbrowser.open(DASHBOARD_URL)


def main() -> int:
    """Initialise l'overlay PySide6, verrouille l'instance unique et lance la boucle Qt.

    Returns:
        Code de sortie du processus (0 si une instance tourne déjà ou à l'arrêt normal).

    Effets de bord:
        Active le mode DPI Per-Monitor V2, crée ``runtime/overlay.lock``, affiche l'orbe,
        le calque HUD, les sous-titres, les cartes et lance le client WebSocket et le
        raccourci global ``Ctrl+Espace``.
    """
    _configurer_logs()
    activer_dpi()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    runtime_dir = obtenir_racine() / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    verrou = QLockFile(str(runtime_dir / "overlay.lock"))
    verrou.setStaleLockTime(0)
    if not verrou.tryLock(100):
        logger.info("Une instance de l'overlay Jarvis est déjà active ; arrêt.")
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis Overlay")
    app.setQuitOnLastWindowClosed(False)

    hud = HudLayer()
    orb = OrbWidget()
    subtitles = SubtitlesBubble()
    cards = CardsContainer()

    client = JarvisClient(token_path=runtime_dir / "token.txt")
    hotkey = GlobalHotkeyListener()
    voice_stream = ConversationVocaleOverlay(token_path=runtime_dir / "token.txt")
    orchestrator = VoiceOrchestrator(
        client=client,
        hud=hud,
        orb=orb,
        subtitles=subtitles,
        cards=cards,
        hotkey=hotkey,
        voice_stream=voice_stream,
    )

    # Connexions visuelles entre l'orbe, les sous-titres et le calque de guidage.
    orb.position_changee.connect(subtitles.suivre_orbe)
    orb.position_changee.connect(lambda rect: hud.set_orbe_centre(rect.center()))
    orb.double_clic_dashboard.connect(_ouvrir_tableau_de_bord)
    hud.guide_change.connect(orb.deplacer_pres_de)

    # Icône et menu de la zone de notification Windows.
    tray = QSystemTrayIcon(_creer_icone_tray(), app)
    tray.setToolTip("Jarvis Overlay")
    menu = QMenu()

    action_orbe = QAction("Masquer l'orbe", menu)

    def _basculer_orbe() -> None:
        if orb.isVisible():
            orb.hide()
            subtitles.hide()
            hud.set_orbe_centre(None)
            action_orbe.setText("Afficher l'orbe")
        else:
            orb.show()
            hud.set_orbe_centre(orb.geometry().center())
            subtitles.suivre_orbe(orb.geometry())
            action_orbe.setText("Masquer l'orbe")

    action_orbe.triggered.connect(_basculer_orbe)
    menu.addAction(action_orbe)

    action_continu = QAction("Mode conversation continue", menu)
    action_continu.setCheckable(True)
    action_continu.setChecked(False)

    def _basculer_mode_continu(coche: bool) -> None:
        nouveau_mode = "continu" if coche else "push"
        orchestrator.set_mode_declenchement(nouveau_mode)
        client.requete_http_async(
            "PUT",
            "/api/parametres",
            payload={"voix": {"declenchement": nouveau_mode}},
            timeout=5.0,
            on_error=lambda exc: logger.warning(
                "Impossible d'enregistrer voix.declenchement : %s", exc
            ),
        )

    def _sync_action_continu(mode: str) -> None:
        bloquer = action_continu.blockSignals(True)
        action_continu.setChecked(mode == "continu")
        action_continu.blockSignals(bloquer)

    action_continu.triggered.connect(_basculer_mode_continu)
    orchestrator.mode_declenchement_change.connect(_sync_action_continu)
    orchestrator.notification_demandee.connect(
        lambda titre, msg: tray.showMessage(
            titre,
            msg,
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )
    )
    menu.addAction(action_continu)

    action_effacer = QAction("Effacer les cadres", menu)
    action_effacer.triggered.connect(lambda: hud.effacer_guide(None))
    menu.addAction(action_effacer)

    action_dashboard = QAction("Ouvrir le tableau de bord", menu)
    action_dashboard.triggered.connect(_ouvrir_tableau_de_bord)
    menu.addAction(action_dashboard)

    menu.addSeparator()
    action_quitter = QAction("Quitter", menu)

    def _quitter() -> None:
        logger.info("Arrêt de l'overlay Jarvis demandé.")
        orchestrator.arreter()
        hotkey.arreter()
        client.arreter()
        hud.close()
        subtitles.close()
        cards.close()
        orb.close()
        tray.hide()
        if verrou.isLocked():
            verrou.unlock()
        app.quit()

    action_quitter.triggered.connect(_quitter)
    menu.addAction(action_quitter)
    tray.setContextMenu(menu)

    def _on_tray_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            _basculer_orbe()

    tray.activated.connect(_on_tray_activated)

    def _on_hotkey_enregistre(libelle: str, est_repli: bool) -> None:
        if not libelle:
            tray.setToolTip("Jarvis Overlay — Raccourci vocal indisponible")
            tray.showMessage(
                "Jarvis Overlay",
                "Impossible d'enregistrer Ctrl+Espace ni Ctrl+Alt+Espace.",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )
            return
        tray.setToolTip(f"Jarvis Overlay ({libelle})")
        if est_repli:
            tray.showMessage(
                "Jarvis Overlay",
                f"Ctrl+Espace déjà occupé : raccourci retenu = {libelle}",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    hotkey.enregistre.connect(_on_hotkey_enregistre)

    tray.show()
    hud.show()
    orb.show()
    hud.set_orbe_centre(orb.geometry().center())
    subtitles.suivre_orbe(orb.geometry())

    client.demarrer()
    hotkey.demarrer()

    try:
        return app.exec()
    finally:
        orchestrator.arreter()
        hotkey.arreter()
        client.arreter()
        if verrou.isLocked():
            verrou.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
