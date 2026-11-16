"""Tests overlay du mode Pont web (Ctrl+Espace/clic orbe -> endpoint, crête audio pycaw, sous-titre)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from overlay.cards import CardsContainer
from overlay.hud_layer import HudLayer
from overlay.orb import OrbWidget
from overlay.subtitles import SubtitlesBubble
from overlay.voice import GlobalHotkeyListener, VoiceOrchestrator
from overlay.voice_stream import LecteurCreteSortieWindows


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """Fournit une instance ``QApplication`` offscreen partagée pour les tests Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


class _FauxClientPontWeb(QObject):
    """Double de ``JarvisClient`` enregistrant les appels REST et simulant les réponses."""

    connecte_change = Signal(bool)
    message_recu = Signal(dict)

    def __init__(self, connecte: bool = True) -> None:
        super().__init__()
        self.est_connecte = connecte
        self.appels_post: list[tuple[str, Any, Callable[[Any], None] | None]] = []
        self.appels_get: list[str] = []

    def get_async(self, chemin: str, **_kwargs: Any) -> None:
        self.appels_get.append(chemin)

    def post_async(
        self,
        chemin: str,
        payload: Any = None,
        on_success: Callable[[Any], None] | None = None,
        **_kwargs: Any,
    ) -> None:
        self.appels_post.append((chemin, payload, on_success))

    def envoyer_ws(self, msg_type: str, data: dict[str, Any] | None = None) -> bool:
        del msg_type, data
        return True


class _FauxVoiceStreamQt(QObject):
    """Espion ``QObject`` compatible avec les connexions de signaux de ``VoiceOrchestrator``."""

    etat_change = Signal(str)
    partiel = Signal(str)
    final = Signal(str)
    niveau = Signal(float)
    reponse_delta = Signal(str)
    erreur = Signal(str)
    repli_demande = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.est_active = False
        self.appels_demarrer: list[str | None] = []
        self.appels_arreter = 0
        self.modes: list[str] = []

    def set_mode(self, mode: str) -> None:
        self.modes.append(mode)

    def demarrer(self, mode: str | None = None) -> None:
        self.est_active = True
        self.appels_demarrer.append(mode)

    def arreter(self) -> None:
        self.est_active = False
        self.appels_arreter += 1


def test_overlay_mode_web_appelle_endpoint_et_anime_crete_puis_repos_5s(
    qt_app: QApplication,
) -> None:
    """En mode ``web``, Ctrl+Espace et clic orbe appellent ``/api/voix/web/ouvrir``, sans pipeline local."""
    del qt_app
    client = _FauxClientPontWeb(connecte=True)
    hud = HudLayer()
    orb = OrbWidget()
    orb.set_connecte(True)
    subtitles = SubtitlesBubble()
    cards = CardsContainer()
    hotkey = GlobalHotkeyListener()
    voice_stream = _FauxVoiceStreamQt()

    peak_courant = [0.0]
    temps_courant = [100.0]
    lecteur = LecteurCreteSortieWindows(peak_getter=lambda: peak_courant[0])

    orchestrator = VoiceOrchestrator(
        client=client,  # type: ignore[arg-type]
        hud=hud,
        orb=orb,
        subtitles=subtitles,
        cards=cards,
        hotkey=hotkey,
        voice_stream=voice_stream,  # type: ignore[arg-type]
        lecteur_sortie=lecteur,
        horloge_fn=lambda: temps_courant[0],
        delai_silence_web_s=5.0,
    )

    try:
        # Synchronisation via message WS parametres_maj en mode web
        orchestrator.traiter_message_ws(
            {
                "type": "parametres_maj",
                "data": {"voix": {"mode": "web", "web_service": "chatgpt"}},
            }
        )
        assert orchestrator.mode_voix == "web"

        # 1. Déclenchement via raccourci Ctrl+Espace et clic orbe
        hotkey.declenche.emit()
        orb.clic_ecoute.emit()

        assert len(client.appels_post) == 2
        assert [c[0] for c in client.appels_post] == [
            "/api/voix/web/ouvrir",
            "/api/voix/web/ouvrir",
        ]
        assert voice_stream.appels_demarrer == []
        assert not any(c[0] == "/api/voix/ecouter" for c in client.appels_post)

        # 2. Réponse de l'endpoint -> état parole, sous-titre court et timer pycaw actif
        on_ok = client.appels_post[0][2]
        assert on_ok is not None
        on_ok({"ok": True, "action": "ouverture", "service": "chatgpt"})
        assert orb.etat == "parole"
        assert subtitles._texte_brut == "ChatGPT ouvert — lance le mode vocal"
        assert orchestrator._timer_pont_web.isActive() is True

        # 3. Son actif même au-delà de 5 s -> reste en parole et suit la crête audio
        temps_courant[0] = 108.0
        peak_courant[0] = 0.72
        orchestrator._tic_audio_pont_web()
        assert orb.etat == "parole"
        assert orb.niveau_cible == pytest.approx(0.72)

        # 4. Silence < 5 s -> reste en parole
        temps_courant[0] = 112.0
        peak_courant[0] = 0.0
        orchestrator._tic_audio_pont_web()
        assert orb.etat == "parole"
        assert orb.niveau_cible == 0.0

        # 5. Silence >= 5 s -> retour au repos et arrêt du timer
        temps_courant[0] = 113.1
        orchestrator._tic_audio_pont_web()
        assert orb.etat == "repos"
        assert orchestrator._timer_pont_web.isActive() is False

        # 6. Service Gemini -> sous-titre adapté
        on_ok({"ok": True, "action": "premier_plan", "service": "gemini"})
        assert subtitles._texte_brut == "Gemini ouvert — lance le mode vocal"
        assert orb.etat == "parole"

        # 7. Retour en mode local -> relance le pipeline local normalement
        orchestrator.traiter_message_ws(
            {
                "type": "parametres_maj",
                "data": {"voix": {"mode": "local"}},
            }
        )
        assert orchestrator.mode_voix == "local"
        assert orb.etat == "repos"
        orchestrator.basculer_ecoute()
        assert voice_stream.appels_demarrer == ["push"]
    finally:
        orchestrator.arreter()
        hud.close()
        orb.close()
        subtitles.close()
        cards.close()
