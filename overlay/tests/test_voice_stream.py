"""Tests unitaires et d'intégration (sans micro ni serveur) de la voix streaming overlay."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication
from websockets.exceptions import InvalidStatus

from overlay.cards import CardsContainer
from overlay.hud_layer import HudLayer
from overlay.orb import OrbWidget, borner_niveau, lisser_niveau
from overlay.subtitles import SubtitlesBubble
from overlay.voice import GlobalHotkeyListener, VoiceOrchestrator
from overlay.voice_stream import (
    BLOC_ECHANTILLONS,
    ConversationVocaleOverlay,
    FileAudioSequentielle,
    LecteurCreteSortieWindows,
    calculer_rms_pcm16,
    lire_niveau_sortie_ou_simule,
    resoudre_peripherique_micro,
)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    """Fournit une instance ``QApplication`` offscreen partagée pour les tests Qt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def _pomper_jusqua(
    app: QApplication,
    condition: Callable[[], bool],
    timeout_s: float = 2.5,
) -> bool:
    """Traite la boucle Qt jusqu'à ce que ``condition()`` soit vraie ou expiration."""
    limite = time.monotonic() + timeout_s
    while time.monotonic() < limite:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return condition()


class FauxLecteurMedia(QObject):
    """Double de ``QMediaPlayer`` mémorisant les tampons ``QBuffer`` lus et les arrêts."""

    mediaStatusChanged = Signal(object)
    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.lectures: list[tuple[bytes, str]] = []
        self.nb_stops = 0
        self.en_cours = False

    def setSource(self, source: QUrl) -> None:
        del source

    def setSourceDevice(self, device: Any, source_url: QUrl) -> None:
        donnees = bytes(device.data())
        self.lectures.append((donnees, source_url.toString()))

    def play(self) -> None:
        self.en_cours = True

    def stop(self) -> None:
        self.nb_stops += 1
        if self.en_cours:
            self.en_cours = False
            self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)

    def simuler_fin_piste(self) -> None:
        """Simule la fin naturelle de la phrase en cours (``EndOfMedia``)."""
        self.en_cours = False
        self.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.EndOfMedia)


class FauxFluxAudio:
    """Faux ``sounddevice.RawInputStream`` injectant une trame PCM 20 ms à l'ouverture."""

    def __init__(
        self,
        *,
        callback: Callable[[bytes, int, Any, Any], None],
        trames: list[bytes] | None = None,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self._callback = callback
        self._trames = trames if trames is not None else [b"\x10\x05" * BLOC_ECHANTILLONS]
        self.ouvert = False
        self.ferme = False

    def __enter__(self) -> FauxFluxAudio:
        self.ouvert = True
        for trame in self._trames:
            self._callback(trame, BLOC_ECHANTILLONS, None, None)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        self.ferme = True


class FauxWebSocket:
    """Faux client WebSocket asynchrone scénarisant ``/ws/voix`` sans serveur réseau."""

    def __init__(self, messages_entrants: list[str | bytes] | None = None) -> None:
        self.envoyes_json: list[dict[str, Any]] = []
        self.envoyes_binaires: list[bytes] = []
        self.ferme = False
        self._queue: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        for item in messages_entrants or []:
            self._queue.put_nowait(item)

    def pousser(self, item: str | bytes | None) -> None:
        """Injecte une trame serveur vers le lecteur asynchrone."""
        self._queue.put_nowait(item)

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, (bytes, bytearray)):
            self.envoyes_binaires.append(bytes(data))
        else:
            self.envoyes_json.append(json.loads(data))

    async def close(self) -> None:
        self.ferme = True
        self._queue.put_nowait(None)

    def __aiter__(self) -> FauxWebSocket:
        return self

    async def __anext__(self) -> str | bytes:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item


class _ContexteWs:
    """Gestionnaire ``async with`` enveloppant ``FauxWebSocket`` ou levant une erreur."""

    def __init__(self, ws: FauxWebSocket | None = None, erreur: Exception | None = None) -> None:
        self._ws = ws
        self._erreur = erreur

    async def __aenter__(self) -> FauxWebSocket:
        if self._erreur is not None:
            raise self._erreur
        assert self._ws is not None
        return self._ws

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        del exc_type, exc, tb
        if self._ws is not None:
            await self._ws.close()


class FauxClientJarvis(QObject):
    """Double minimal de ``JarvisClient`` pour vérifier le repli talkie-walkie."""

    connecte_change = Signal(bool)
    message_recu = Signal(dict)

    def __init__(self, connecte: bool = True) -> None:
        super().__init__()
        self.est_connecte = connecte
        self.appels_post: list[tuple[str, Any]] = []
        self.appels_get: list[str] = []
        self.envois_ws: list[tuple[str, dict[str, Any]]] = []

    def get_async(self, chemin: str, **_kwargs: Any) -> None:
        self.appels_get.append(chemin)

    def post_async(self, chemin: str, payload: Any = None, **_kwargs: Any) -> None:
        self.appels_post.append((chemin, payload))

    def envoyer_ws(self, msg_type: str, data: dict[str, Any] | None = None) -> bool:
        self.envois_ws.append((msg_type, data or {}))
        return True


def test_file_audio_ordre_seq_avec_retard(qt_app: QApplication) -> None:
    """Une phrase ``seq=1`` reçue avant ``seq=0`` attend ``seq=0`` puis s'enchaîne dans l'ordre."""
    del qt_app
    lecteur = FauxLecteurMedia()
    conv = ConversationVocaleOverlay(http_url="", player=lecteur)
    etats: list[str] = []
    conv.etat_change.connect(etats.append)

    # Arrivée en avance de seq=1 : ne doit pas démarrer avant seq=0.
    conv.traiter_message_json({"type": "audio_debut", "seq": 1, "mime": "audio/wav", "phrase": "Deux"})
    conv.traiter_trame_binaire(b"WAV-1")
    assert lecteur.lectures == []

    # Arrivée en retard de seq=0 : doit démarrer immédiatement.
    conv.traiter_message_json({"type": "audio_debut", "seq": 0, "mime": "audio/mpeg", "phrase": "Un"})
    conv.traiter_trame_binaire(b"MP3-0")
    assert lecteur.lectures == [(b"MP3-0", "phrase.mp3")]
    assert etats[-1] == "parole"

    # Fin de seq=0 : enchaînement immédiat vers seq=1 déjà en attente.
    lecteur.simuler_fin_piste()
    assert lecteur.lectures == [(b"MP3-0", "phrase.mp3"), (b"WAV-1", "phrase.wav")]

    conv.arreter()


def test_vidage_sur_stop_audio_et_barge_in(qt_app: QApplication) -> None:
    """``stop_audio`` et ``parole_debut`` coupent la lecture et vident les segments en attente."""
    del qt_app
    lecteur = FauxLecteurMedia()
    conv = ConversationVocaleOverlay(http_url="", player=lecteur)
    conv._active = True
    etats: list[str] = []
    conv.etat_change.connect(etats.append)

    conv.traiter_message_json({"type": "audio_debut", "seq": 0, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"SEG-0")
    conv.traiter_message_json({"type": "audio_debut", "seq": 1, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"SEG-1")
    conv.traiter_message_json({"type": "audio_debut", "seq": 2, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"SEG-2")
    assert len(lecteur.lectures) == 1

    # Ordre serveur stop_audio -> arrêt immédiat + vidage file.
    conv.traiter_message_json({"type": "stop_audio"})
    assert lecteur.nb_stops >= 1
    assert not lecteur.en_cours
    assert conv._file_audio.est_vide()
    assert conv._file_audio.prochain_seq == 0
    assert etats[-1] == "ecoute"

    # Vérifie que la fin éventuelle d'une piste ne relance rien et que parole_debut vide aussi.
    lecteur.simuler_fin_piste()
    assert len(lecteur.lectures) == 1

    conv.traiter_message_json({"type": "audio_debut", "seq": 0, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"SEG-B0")
    conv.traiter_message_json({"type": "audio_debut", "seq": 1, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"SEG-B1")
    assert len(lecteur.lectures) == 2

    conv.traiter_message_json({"type": "parole_debut"})
    assert not lecteur.en_cours
    assert conv._file_audio.est_vide()
    assert etats[-1] == "ecoute"

    conv.arreter()


def test_bascule_push_et_continu(qt_app: QApplication) -> None:
    """En mode ``push``, ``reponse_fin`` + fin audio arrête ; en ``continu``, reste à l'écoute."""
    lecteur = FauxLecteurMedia()
    ws_push = FauxWebSocket([json.dumps({"type": "pret"})])
    conv = ConversationVocaleOverlay(
        http_url="",
        stream_factory=lambda **kw: FauxFluxAudio(**kw),
        ws_connect_fn=lambda *_a, **_kw: _ContexteWs(ws=ws_push),
        player=lecteur,
    )
    etats: list[str] = []
    conv.etat_change.connect(etats.append)

    # Mode push : s'arrête après reponse_fin puis fin de lecture.
    conv.demarrer(mode="push")
    assert _pomper_jusqua(qt_app, lambda: any(m.get("type") == "config" for m in ws_push.envoyes_json))
    assert ws_push.envoyes_json[0] == {"type": "config", "agent": "jarvis"}
    assert _pomper_jusqua(qt_app, lambda: len(ws_push.envoyes_binaires) >= 1)

    conv.traiter_message_json({"type": "audio_debut", "seq": 0, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"AUDIO-PUSH")
    conv.traiter_message_json({"type": "reponse_fin"})
    assert conv.est_active is True  # Tant que la lecture n'est pas terminée

    lecteur.simuler_fin_piste()
    assert conv.est_active is False
    assert etats[-1] == "repos"

    # Mode continu : reste actif et revient à "ecoute" jusqu'à bascule manuelle.
    ws_continu = FauxWebSocket([json.dumps({"type": "pret"})])
    conv._ws_connect_fn = lambda *_a, **_kw: _ContexteWs(ws=ws_continu)
    conv.demarrer(mode="continu")
    assert _pomper_jusqua(qt_app, lambda: len(ws_continu.envoyes_binaires) >= 1)

    conv.traiter_message_json({"type": "audio_debut", "seq": 0, "mime": "audio/mpeg"})
    conv.traiter_trame_binaire(b"AUDIO-CONT")
    conv.traiter_message_json({"type": "reponse_fin"})
    lecteur.simuler_fin_piste()
    assert conv.est_active is True
    assert etats[-1] == "ecoute"

    assert conv.basculer() is False
    assert conv.est_active is False
    assert etats[-1] == "repos"


@pytest.mark.parametrize(
    "erreur_ws",
    [
        ConnectionRefusedError("connexion refusée"),
        InvalidStatus(type("Resp404", (), {"status_code": 404})()),
    ],
)
def test_repli_talkie_walkie_si_ws_indisponible(
    qt_app: QApplication,
    tmp_path: Path,
    erreur_ws: Exception,
) -> None:
    """Si ``/ws/voix`` refuse la connexion ou renvoie 404, bascule sur l'ancien talkie-walkie."""
    token_file = tmp_path / "token.txt"
    token_file.write_text("jeton-test", encoding="utf-8")

    client = FauxClientJarvis(connecte=True)
    hud = HudLayer()
    orb = OrbWidget()
    subtitles = SubtitlesBubble()
    cards = CardsContainer()
    hotkey = GlobalHotkeyListener()
    lecteur = FauxLecteurMedia()

    voice_stream = ConversationVocaleOverlay(
        token_path=token_file,
        http_url="",
        stream_factory=lambda **kw: FauxFluxAudio(**kw),
        ws_connect_fn=lambda *_a, **_kw: _ContexteWs(erreur=erreur_ws),
        player=lecteur,
    )
    orchestrator = VoiceOrchestrator(
        client=client,  # type: ignore[arg-type]
        hud=hud,
        orb=orb,
        subtitles=subtitles,
        cards=cards,
        hotkey=hotkey,
        voice_stream=voice_stream,
    )
    notifications: list[tuple[str, str]] = []
    orchestrator.notification_demandee.connect(lambda t, m: notifications.append((t, m)))

    orchestrator.basculer_ecoute()
    assert _pomper_jusqua(qt_app, lambda: len(client.appels_post) == 1)

    assert client.appels_post[0][0] == "/api/voix/ecouter"
    assert len(notifications) == 1
    assert "talkie-walkie" in notifications[0][1].lower()
    assert voice_stream.est_active is False

    orchestrator.arreter()
    hud.close()
    orb.close()
    subtitles.close()
    cards.close()


def test_sous_titres_partiel_et_reponse_delta(qt_app: QApplication) -> None:
    """Les signaux ``partiel`` puis ``reponse_delta`` mettent à jour la bulle en direct."""
    del qt_app
    bulle = SubtitlesBubble()
    bulle.on_voix_partiel("Bonjour Jar")
    assert bulle._texte_brut == "Bonjour Jar"

    bulle.on_voix_final("Bonjour Jarvis")
    assert bulle._texte_brut == "Bonjour Jarvis"

    bulle.on_voix_reponse_delta("Salut ")
    bulle.on_voix_reponse_delta("comment puis-je aider ?")
    assert bulle._texte_brut == "Salut comment puis-je aider ?"
    bulle.close()


def test_lissage_niveau_orbe_et_sortie_audio(qt_app: QApplication) -> None:
    """Vérifie le bornage, le lissage exponentiel de ``OrbWidget.set_niveau`` et le repli pycaw."""
    del qt_app
    assert borner_niveau(-0.5) == 0.0
    assert borner_niveau(2.5) == 1.0
    assert borner_niveau(float("nan")) == 0.0

    n1 = lisser_niveau(0.0, 1.0, dt=0.016, tau=0.08)
    n2 = lisser_niveau(n1, 1.0, dt=0.016, tau=0.08)
    assert 0.0 < n1 < n2 < 1.0

    orb = OrbWidget()
    orb.set_connecte(True)
    orb.set_etat("ecoute")
    orb.set_niveau(0.8)
    orb._tick()
    apres_1 = orb.niveau
    orb._tick()
    apres_2 = orb.niveau
    assert 0.0 < apres_1 < apres_2 <= 0.8

    orb.set_etat("repos")
    assert orb.niveau_cible == 0.0
    orb.close()

    # RMS PCM16 et lecteur de crête / enveloppe simulée.
    assert calculer_rms_pcm16(b"\x00\x00" * 320) == 0.0
    assert calculer_rms_pcm16(b"\xff\x3f" * 320) > 0.5

    lecteur_capteur = LecteurCreteSortieWindows(peak_getter=lambda: 0.65)
    assert lire_niveau_sortie_ou_simule(lecteur_capteur) == pytest.approx(0.65)

    lecteur_absent = LecteurCreteSortieWindows(peak_getter=lambda: (_ for _ in ()).throw(RuntimeError("ko")))
    simule = lire_niveau_sortie_ou_simule(lecteur_absent, temps_s=1.2)
    assert 0.0 <= simule <= 1.0


def test_resolution_micro_et_file_hors_ordre() -> None:
    """Vérifie la résolution de ``voix.micro`` et la remise à zéro de ``FileAudioSequentielle``."""
    assert resoudre_peripherique_micro(None) is None
    assert resoudre_peripherique_micro("") is None
    assert resoudre_peripherique_micro("defaut") is None
    assert resoudre_peripherique_micro(2) == 2
    assert resoudre_peripherique_micro("3") == 3

    faux_periphs = [
        {"index": 0, "name": "Haut-parleurs", "max_input_channels": 0},
        {"index": 4, "name": "Micro USB Shure", "max_input_channels": 2},
    ]
    assert resoudre_peripherique_micro("shure", query_devices_fn=lambda: faux_periphs) == 4
    assert resoudre_peripherique_micro("inexistant", query_devices_fn=lambda: faux_periphs) is None

    file = FileAudioSequentielle()
    file.ajouter(1, b"b")
    assert file.suivant_pret() is None
    file.ajouter(0, b"a")
    assert file.suivant_pret() == (0, b"a", "audio/mpeg")
    assert file.suivant_pret() == (1, b"b", "audio/mpeg")
    assert file.est_vide() is True
