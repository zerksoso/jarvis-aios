"""Raccourci global Windows (Ctrl+Espace) et orchestration vocale/WS de l'overlay."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from overlay.cards import CardsContainer
from overlay.client import BASE_HTTP_URL, JarvisClient
from overlay.hud_layer import HudLayer
from overlay.orb import OrbWidget
from overlay.subtitles import SubtitlesBubble
from overlay.voice_stream import ConversationVocaleOverlay

logger = logging.getLogger(__name__)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
HOTKEY_ID = 1


class GlobalHotkeyListener(QObject):
    """Écouteur Windows de raccourci global dans un thread dédié avec boucle ``GetMessageW``.

    Enregistre ``Ctrl+Espace`` en priorité, ou ``Ctrl+Alt+Espace`` en repli si occupé,
    et émet un signal Qt vers le thread principal à chaque pression.
    """

    declenche = Signal()
    enregistre = Signal(str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialise l'écouteur sans démarrer encore le thread Win32."""
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._win_thread_id: int | None = None

    def demarrer(self) -> None:
        """Lance le thread dédié à l'enregistrement du raccourci et à sa boucle Win32."""
        if self._thread is not None and self._thread.is_alive():
            return
        if sys.platform != "win32":
            self.enregistre.emit("", False)
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._boucle_win32,
            name="jarvis-overlay-hotkey",
            daemon=True,
        )
        self._thread.start()

    def arreter(self) -> None:
        """Envoie ``WM_QUIT`` à la boucle Win32 du raccourci et attend la fin du thread."""
        self._stop_event.set()
        tid = self._win_thread_id
        if sys.platform == "win32" and tid:
            try:
                from ctypes import wintypes

                ctypes.windll.user32.PostThreadMessageW(
                    wintypes.DWORD(tid),
                    wintypes.UINT(WM_QUIT),
                    0,
                    0,
                )
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _boucle_win32(self) -> None:
        """Enregistre le raccourci global et consomme ``GetMessageW`` jusqu'à ``WM_QUIT``."""
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._win_thread_id = int(kernel32.GetCurrentThreadId())

        enregistre_ok = False
        try:
            if user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
                enregistre_ok = True
                self.enregistre.emit("Ctrl+Espace", False)
            elif user32.RegisterHotKey(
                None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE
            ):
                enregistre_ok = True
                self.enregistre.emit("Ctrl+Alt+Espace", True)
            else:
                logger.warning("Échec RegisterHotKey pour Ctrl+Espace et Ctrl+Alt+Espace.")
                self.enregistre.emit("", False)
                return

            msg = wintypes.MSG()
            while not self._stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.declenche.emit()
        except Exception as exc:
            logger.exception("Erreur dans la boucle raccourci global : %s", exc)
        finally:
            if enregistre_ok:
                try:
                    user32.UnregisterHotKey(None, HOTKEY_ID)
                except Exception:
                    pass
            self._win_thread_id = None


class VoiceOrchestrator(QObject):
    """Orchestre les événements WS, le déclenchement vocal streaming/talkie-walkie et les approbations."""

    mode_declenchement_change = Signal(str)
    notification_demandee = Signal(str, str)

    def __init__(
        self,
        client: JarvisClient,
        hud: HudLayer,
        orb: OrbWidget,
        subtitles: SubtitlesBubble,
        cards: CardsContainer,
        hotkey: GlobalHotkeyListener,
        voice_stream: ConversationVocaleOverlay | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Relie les signaux du client WS, du raccourci, des widgets HUD et du module vocal."""
        super().__init__(parent)
        self._client = client
        self._hud = hud
        self._orb = orb
        self._subtitles = subtitles
        self._cards = cards
        self._hotkey = hotkey
        self._voice_stream = voice_stream

        self._en_ecoute = False
        self._mode_declenchement = "push"

        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(1.0)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_media_error)

        if self._voice_stream is not None:
            self._voice_stream.etat_change.connect(self._orb.set_etat)
            self._voice_stream.niveau.connect(self._orb.set_niveau)
            self._voice_stream.partiel.connect(self._subtitles.on_voix_partiel)
            self._voice_stream.final.connect(self._subtitles.on_voix_final)
            self._voice_stream.reponse_delta.connect(self._subtitles.on_voix_reponse_delta)
            self._voice_stream.erreur.connect(self._subtitles.on_narration)
            self._voice_stream.repli_demande.connect(self._on_repli_talkie_walkie)

        self._client.connecte_change.connect(self._on_connecte_change)
        self._client.message_recu.connect(self.traiter_message_ws)
        self._hotkey.declenche.connect(self.basculer_ecoute)
        self._orb.clic_ecoute.connect(self.basculer_ecoute)
        self._cards.epingler_demande.connect(self._on_carte_epingler)
        self._cards.approbation_repondue.connect(self._on_approbation_repondue)

    @property
    def mode_declenchement(self) -> str:
        """Retourne le mode vocal configuré (``"push"`` ou ``"continu"``)."""
        return self._mode_declenchement

    def set_mode_declenchement(self, mode: str) -> None:
        """Met à jour le mode de déclenchement vocal (``"push"`` ou ``"continu"``)."""
        normalise = "continu" if str(mode).strip().lower() == "continu" else "push"
        self._mode_declenchement = normalise
        if self._voice_stream is not None:
            self._voice_stream.set_mode(normalise)
        self.mode_declenchement_change.emit(normalise)

    def arreter(self) -> None:
        """Interrompt toute capture/lecture audio en cours lors de la fermeture de l'overlay."""
        self._en_ecoute = False
        self._player.stop()
        if self._voice_stream is not None:
            self._voice_stream.arreter()

    def _on_connecte_change(self, connecte: bool) -> None:
        """Synchronise l'état de connexion sur l'orbe et charge ``voix.declenchement``."""
        self._orb.set_connecte(connecte)
        if not connecte:
            self._en_ecoute = False
            if self._voice_stream is not None and self._voice_stream.est_active:
                self._voice_stream.arreter()
            return
        self._client.get_async(
            "/api/parametres",
            timeout=5.0,
            on_success=self._synchroniser_parametres_voix,
        )

    def _synchroniser_parametres_voix(self, parametres: Any) -> None:
        """Synchronise ``voix.declenchement`` depuis ``GET /api/parametres``."""
        if not isinstance(parametres, dict):
            return
        voix_cfg = parametres.get("voix")
        if isinstance(voix_cfg, dict) and "declenchement" in voix_cfg:
            self.set_mode_declenchement(str(voix_cfg.get("declenchement") or "push"))

    def basculer_ecoute(self) -> None:
        """Démarre ou arrête la conversation vocale streaming (ou le talkie-walkie en repli)."""
        if self._voice_stream is not None:
            if self._voice_stream.est_active:
                self._voice_stream.arreter()
                return
            if not self._client.est_connecte:
                self._subtitles.on_narration("Serveur Jarvis hors ligne — reconnexion en cours…")
                return
            self._player.stop()
            self._voice_stream.demarrer(mode=self._mode_declenchement)
            return

        self._basculer_ecoute_talkie_walkie()

    def _on_repli_talkie_walkie(self, raison: str) -> None:
        """Bascule automatiquement vers le talkie-walkie historique si ``/ws/voix`` échoue."""
        logger.warning("Repli talkie-walkie déclenché : %s", raison)
        self.notification_demandee.emit(
            "Jarvis Overlay — Mode repli",
            "Conversation streaming (/ws/voix) indisponible : bascule sur le mode talkie-walkie.",
        )
        self._subtitles.on_narration("Mode streaming indisponible — bascule sur le talkie-walkie.")
        if self._client.est_connecte:
            self._basculer_ecoute_talkie_walkie()

    def _basculer_ecoute_talkie_walkie(self) -> None:
        """Démarre l'écoute micro HTTP historique, ou l'interrompt si déjà active."""
        if self._en_ecoute:
            self._client.envoyer_ws("ecoute", {"action": "fin"})
            return

        if not self._client.est_connecte:
            self._subtitles.on_narration("Serveur Jarvis hors ligne — reconnexion en cours…")
            return

        self._player.stop()
        self._en_ecoute = True
        self._orb.set_etat("ecoute")
        self._client.post_async(
            "/api/voix/ecouter",
            payload=None,
            timeout=30.0,
            on_success=self._apres_ecoute,
            on_error=self._erreur_ecoute,
        )

    def _apres_ecoute(self, resultat: Any) -> None:
        """Envoie le texte transcrit au chat Jarvis s'il est non vide."""
        self._en_ecoute = False
        texte = str((resultat or {}).get("texte") or "").strip() if isinstance(resultat, dict) else ""
        if not texte:
            self._orb.set_etat("repos")
            return

        self._orb.set_etat("reflexion")
        self._client.post_async(
            "/api/chat",
            payload={"cible": "agent", "id": "jarvis", "message": texte},
            timeout=15.0,
            on_error=self._erreur_chat,
        )

    def _erreur_ecoute(self, exc: Exception) -> None:
        """Rétablit l'orbe et affiche la raison si le microphone ou l'écoute échoue."""
        self._en_ecoute = False
        self._orb.set_etat("repos")
        self._subtitles.on_narration(str(exc))

    def _erreur_chat(self, exc: Exception) -> None:
        """Rétablit l'orbe si l'envoi du message transcrit vers ``/api/chat`` échoue."""
        logger.warning("Échec d'envoi du message vocal vers /api/chat : %s", exc)
        self._orb.set_etat("repos")
        self._subtitles.on_narration(str(exc))

    def traiter_message_ws(self, env: dict[str, Any]) -> None:
        """Aiguille chaque enveloppe WebSocket reçue vers le composant overlay concerné."""
        msg_type = env.get("type")
        data = env.get("data")
        if not isinstance(msg_type, str) or not isinstance(data, dict):
            return

        stream_actif = self._voice_stream is not None and self._voice_stream.est_active
        if stream_actif and msg_type in (
            "etat_jarvis",
            "chat_debut",
            "chat_delta",
            "chat_fin",
            "voix_parler",
        ):
            return

        if msg_type == "etat_jarvis":
            self._orb.set_etat(str(data.get("etat") or "repos"))
        elif msg_type == "guide":
            self._hud.afficher_guide(data)
        elif msg_type == "guide_effacer":
            guide_id = data.get("id")
            self._hud.effacer_guide(str(guide_id) if guide_id is not None else None)
        elif msg_type == "carte":
            self._cards.ajouter_carte(data)
        elif msg_type == "approbation_demande":
            self._cards.ajouter_approbation(data)
        elif msg_type == "approbation_resultat":
            self._cards.retirer_carte(str(data.get("id") or ""))
        elif msg_type == "narration":
            self._subtitles.on_narration(str(data.get("texte") or ""))
        elif msg_type == "chat_debut":
            self._subtitles.on_chat_debut(str(data.get("auteur") or ""))
        elif msg_type == "chat_delta":
            self._subtitles.on_chat_delta(
                str(data.get("auteur") or ""),
                str(data.get("delta") or ""),
            )
        elif msg_type == "chat_fin":
            auteur = str(data.get("auteur") or "")
            contenu = str(data.get("contenu") or "")
            self._subtitles.on_chat_fin(auteur, contenu)
            if auteur.strip().lower() == "jarvis" and contenu.strip():
                self._client.get_async(
                    "/api/parametres",
                    timeout=5.0,
                    on_success=lambda params, txt=contenu: self._apres_parametres(params, txt),
                )
        elif msg_type == "voix_parler":
            url = str(data.get("url") or "").strip()
            if url:
                url_complete = f"{BASE_HTTP_URL}{url}" if url.startswith("/") else url
                self._lire_audio(url_complete)

    def _apres_parametres(self, parametres: Any, contenu: str) -> None:
        """Déclenche ``POST /api/voix/parler`` si la synthèse vocale est activée."""
        if not isinstance(parametres, dict):
            return
        voix_cfg = parametres.get("voix")
        active = bool(voix_cfg.get("active", True)) if isinstance(voix_cfg, dict) else True
        if not active or not contenu.strip():
            return
        self._client.post_async(
            "/api/voix/parler",
            payload={"texte": contenu},
            timeout=20.0,
            on_error=lambda exc: logger.warning("Échec /api/voix/parler : %s", exc),
        )

    def _lire_audio(self, url: str) -> None:
        """Lit le flux TTS via ``QMediaPlayer`` en passant l'orbe en état ``parole``."""
        self._player.stop()
        self._player.setSource(QUrl(url))
        self._player.play()
        self._orb.set_etat("parole")

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Revient à l'état ``repos`` lorsque la lecture TTS se termine."""
        if state == QMediaPlayer.PlaybackState.StoppedState and self._orb.etat == "parole":
            self._orb.set_etat("repos")

    def _on_media_error(self, error: QMediaPlayer.Error, message: str) -> None:
        """Journalise une erreur de lecture multimédia et libère l'état ``parole``."""
        logger.warning("Erreur lecture audio QMediaPlayer (%s) : %s", error, message)
        if self._orb.etat == "parole":
            self._orb.set_etat("repos")

    def _on_carte_epingler(self, carte_id: str) -> None:
        """Envoie l'événement WS ``carte_epingler`` au backend."""
        self._client.envoyer_ws("carte_epingler", {"id": carte_id})

    def _on_approbation_repondue(self, app_id: str, decision: str) -> None:
        """Envoie la décision utilisateur ``approbation_reponse`` au backend."""
        self._client.envoyer_ws(
            "approbation_reponse",
            {"id": app_id, "decision": decision},
        )
