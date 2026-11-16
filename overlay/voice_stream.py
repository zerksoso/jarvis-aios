"""Conversation vocale streaming pour l'overlay desktop via WebSocket ``/ws/voix``."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import json
import logging
import math
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
import numpy as np
import sounddevice as sd
from PySide6.QtCore import QBuffer, QByteArray, QIODeviceBase, QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import InvalidHandshake, InvalidStatus, WebSocketException

from overlay.client import BASE_HTTP_URL, lire_jeton

logger = logging.getLogger(__name__)

DEFAULT_VOIX_WS_URL = "ws://127.0.0.1:5230/ws/voix"
TAUX_ECHANTILLONNAGE = 16000
CANAUX = 1
BLOC_ECHANTILLONS = 320  # 20 ms à 16 kHz (640 octets en PCM int16 LE)
INTERVALLE_NIVEAU_SORTIE_MS = 33
INTERVALLE_NIVEAU_MICRO_MIN_S = 0.030

ModeDeclenchement = Literal["push", "continu"]


def calculer_rms_pcm16(pcm_bytes: bytes) -> float:
    """Calcule le niveau RMS normalisé ``[0.0, 1.0]`` d'une trame PCM 16 bits signée.

    Args:
        pcm_bytes: Trame brute PCM int16 little-endian.

    Returns:
        Niveau d'amplitude normalisé entre ``0.0`` (silence) et ``1.0`` (parole forte).
    """
    nb_octets = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if nb_octets < 2:
        return 0.0
    echantillons = np.frombuffer(pcm_bytes[:nb_octets], dtype=np.int16)
    if echantillons.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(echantillons.astype(np.float32) ** 2))) / 32768.0
    if not math.isfinite(rms):
        return 0.0
    return max(0.0, min(1.0, rms * 5.0))


def resoudre_peripherique_micro(
    micro: Any,
    query_devices_fn: Callable[[], Any] | None = None,
) -> int | str | None:
    """Convertit le paramètre ``voix.micro`` en identifiant ``sounddevice`` sûr.

    Accepte un index entier, une chaîne numérique, un nom (ou sous-chaîne) de périphérique
    d'entrée, ou ``None`` / ``"defaut"`` pour le microphone système par défaut. En cas de nom
    introuvable, revient au périphérique système par défaut (``None``).

    Args:
        micro: Valeur brute issue de ``voix.micro`` dans ``GET /api/parametres``.
        query_devices_fn: Fonction de requête périphériques (``sd.query_devices`` par défaut).

    Returns:
        Index entier, nom de périphérique ou ``None`` (défaut système).
    """
    if micro is None or isinstance(micro, bool):
        return None
    if isinstance(micro, int):
        return micro if micro >= 0 else None

    texte = str(micro).strip()
    if not texte or texte.lower() in ("default", "defaut", "défaut", "systeme", "système"):
        return None
    if texte.isdigit():
        return int(texte)

    requete = query_devices_fn or sd.query_devices
    try:
        peripheriques = list(requete())
    except Exception as exc:
        logger.debug("Impossible d'énumérer les microphones (%s), passage direct du nom.", exc)
        return texte

    entrees = [
        d
        for d in peripheriques
        if isinstance(d, dict) and int(d.get("max_input_channels") or 0) > 0
    ]
    cible_min = texte.lower()
    for dev in entrees:
        nom = str(dev.get("name") or "")
        if nom.strip().lower() == cible_min:
            return int(dev["index"]) if "index" in dev else nom
    for dev in entrees:
        nom = str(dev.get("name") or "")
        if cible_min in nom.lower():
            return int(dev["index"]) if "index" in dev else nom

    logger.warning("Microphone configuré %r introuvable, repli sur le défaut système.", texte)
    return None


def enveloppe_simulee(temps_s: float | None = None) -> float:
    """Génère une enveloppe vocale simulée dans ``[0.15, 0.90]`` si ``pycaw`` est indisponible.

    Args:
        temps_s: Horodatage optionnel en secondes (``time.monotonic()`` par défaut).

    Returns:
        Niveau simulé borné entre ``0.0`` et ``1.0``.
    """
    t = time.monotonic() if temps_s is None else float(temps_s)
    val = 0.25 + 0.40 * abs(math.sin(t * 7.2)) + 0.25 * abs(math.sin(t * 13.7))
    return max(0.0, min(1.0, val))


class LecteurCreteSortieWindows:
    """Lit le niveau de crête de la sortie audio Windows via ``pycaw`` (``IAudioMeterInformation``).

    Repli silencieux vers ``None`` si ``pycaw`` est absent, hors Windows ou si l'interface
    WASAPI échoue, permettant au consommateur d'utiliser une enveloppe simulée.
    """

    def __init__(self, peak_getter: Callable[[], float] | None = None) -> None:
        """Initialise le lecteur de crête audio avec un fournisseur optionnel pour les tests."""
        self._peak_getter = peak_getter
        self._meter: Any | None = None
        self._indisponible = False

    def _initialiser_meter(self) -> Any | None:
        """Résout ``IAudioMeterInformation`` sur le périphérique de rendu audio par défaut."""
        if self._indisponible or sys.platform != "win32":
            self._indisponible = True
            return None
        try:
            import comtypes
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

            with contextlib.suppress(Exception):
                comtypes.CoInitialize()

            speakers = AudioUtilities.GetSpeakers()
            if speakers is None:
                self._indisponible = True
                return None
            brut = getattr(speakers, "_dev", speakers)
            interface = brut.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None)
            self._meter = ctypes.cast(interface, ctypes.POINTER(IAudioMeterInformation))
            return self._meter
        except Exception as exc:
            logger.debug("pycaw indisponible pour la mesure de crête sortie (%s).", exc)
            self._indisponible = True
            self._meter = None
            return None

    def lire_niveau(self) -> float | None:
        """Retourne le niveau de crête ``[0.0, 1.0]`` ou ``None`` si la sonde est indisponible."""
        if self._peak_getter is not None:
            try:
                val = float(self._peak_getter())
                return max(0.0, min(1.0, val)) if math.isfinite(val) else 0.0
            except Exception:
                return None

        if self._meter is None and not self._indisponible:
            self._initialiser_meter()
        if self._meter is None:
            return None

        try:
            peak = float(self._meter.GetPeakValue())
            if not math.isfinite(peak):
                return 0.0
            return max(0.0, min(1.0, peak))
        except Exception as exc:
            logger.debug("Lecture GetPeakValue échouée (%s), repli enveloppe simulée.", exc)
            self._meter = None
            self._indisponible = True
            return None


def lire_niveau_sortie_ou_simule(
    lecteur: LecteurCreteSortieWindows,
    temps_s: float | None = None,
) -> float:
    """Retourne le niveau de crête Windows via ``pycaw`` ou une enveloppe simulée en repli."""
    mesure = lecteur.lire_niveau()
    if mesure is not None:
        return mesure
    return enveloppe_simulee(temps_s)


class FileAudioSequentielle:
    """File de lecture ordonnée par numéro de séquence ``seq`` (tolérante aux arrivées hors ordre)."""

    def __init__(self) -> None:
        """Initialise la file vide à ``seq = 0``."""
        self._next_seq = 0
        self._en_attente: dict[int, tuple[bytes, str]] = {}

    @property
    def prochain_seq(self) -> int:
        """Retourne le prochain numéro de séquence attendu."""
        return self._next_seq

    def est_vide(self) -> bool:
        """Indique s'il ne reste aucun segment en attente."""
        return len(self._en_attente) == 0

    def reinitialiser(self) -> None:
        """Vide immédiatement tous les segments en attente et réinitialise ``seq = 0``."""
        self._en_attente.clear()
        self._next_seq = 0

    def ajouter(self, seq: int, donnees: bytes, mime: str = "audio/mpeg") -> None:
        """Insère un segment audio complet dans la file ordonnée.

        Args:
            seq: Numéro de séquence croissant du tour courant (à partir de 0).
            donnees: Octets complets MP3 ou WAV de la phrase.
            mime: Type MIME audio (``audio/mpeg`` ou ``audio/wav``).
        """
        if not donnees or seq < self._next_seq:
            return
        self._en_attente[seq] = (donnees, mime)

    def suivant_pret(self) -> tuple[int, bytes, str] | None:
        """Extrait et retourne ``(seq, donnees, mime)`` si ``prochain_seq`` est disponible."""
        if self._next_seq not in self._en_attente:
            return None
        seq = self._next_seq
        donnees, mime = self._en_attente.pop(seq)
        self._next_seq += 1
        return seq, donnees, mime

    def forcer_suivant_disponible(self) -> tuple[int, bytes, str] | None:
        """Extrait le plus petit ``seq`` restant après ``reponse_fin`` en cas de trou réseau."""
        if not self._en_attente:
            return None
        seq = min(self._en_attente)
        donnees, mime = self._en_attente.pop(seq)
        self._next_seq = seq + 1
        return seq, donnees, mime


class ConversationVocaleOverlay(QObject):
    """Client de conversation vocale streaming ``/ws/voix`` pour l'overlay PySide6.

    Capture le microphone en PCM 16 kHz mono int16 (trames de 320 échantillons / 20 ms)
    dans un thread asyncio dédié, lit les phrases audio TTS en mémoire via ``QMediaPlayer`` +
    ``QBuffer`` dans l'ordre strict de ``seq``, gère le barge-in et expose les signaux Qt
    de statut, sous-titres et niveau vocal.
    """

    etat_change = Signal(str)
    partiel = Signal(str)
    final = Signal(str)
    niveau = Signal(float)
    reponse_delta = Signal(str)
    reponse_fin = Signal()
    erreur = Signal(str)
    repli_demande = Signal(str)

    _sig_message_json = Signal(int, dict)
    _sig_trame_binaire = Signal(int, bytes)
    _sig_ws_indisponible = Signal(int, str)
    _sig_ws_ferme = Signal(int, str)
    _sig_niveau_micro = Signal(int, float)

    def __init__(
        self,
        token_path: Path | None = None,
        ws_url: str = DEFAULT_VOIX_WS_URL,
        http_url: str = BASE_HTTP_URL,
        agent: str = "jarvis",
        stream_factory: Callable[..., Any] | None = None,
        ws_connect_fn: Callable[..., Any] | None = None,
        lecteur_sortie: LecteurCreteSortieWindows | None = None,
        player: Any | None = None,
        audio_output: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Initialise la file audio, le lecteur multimédia Qt et les hooks injectables."""
        super().__init__(parent)
        self._token_path = token_path
        self._ws_url = ws_url.rstrip("/")
        self._http_url = http_url.rstrip("/")
        self._agent = agent
        self._stream_factory = stream_factory or sd.RawInputStream
        self._ws_connect_fn = ws_connect_fn or ws_connect
        self._lecteur_sortie = lecteur_sortie or LecteurCreteSortieWindows()

        if player is None:
            self._audio_output: Any = audio_output or QAudioOutput(self)
            if hasattr(self._audio_output, "setVolume"):
                self._audio_output.setVolume(1.0)
            self._player: Any = QMediaPlayer(self)
            if hasattr(self._player, "setAudioOutput"):
                self._player.setAudioOutput(self._audio_output)
        else:
            self._audio_output = audio_output
            self._player = player

        if hasattr(self._player, "mediaStatusChanged"):
            self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        if hasattr(self._player, "playbackStateChanged"):
            self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        if hasattr(self._player, "errorOccurred"):
            self._player.errorOccurred.connect(self._on_media_error)

        self._file_audio = FileAudioSequentielle()
        self._meta_audio_attente: tuple[int, str] | None = None
        self._en_lecture_audio = False
        self._reponse_fin_recue = False
        self._buffer_actif: QBuffer | None = None
        self._donnees_actives: QByteArray | None = None

        self._mode: ModeDeclenchement = "push"
        self._active = False
        self._session_id = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_async: asyncio.Event | None = None
        self._ws_courant: Any | None = None

        self._timer_niveau_sortie = QTimer(self)
        self._timer_niveau_sortie.setInterval(INTERVALLE_NIVEAU_SORTIE_MS)
        self._timer_niveau_sortie.timeout.connect(self._tic_niveau_sortie)

        self._sig_message_json.connect(self._on_message_json_thread)
        self._sig_trame_binaire.connect(self._on_trame_binaire_thread)
        self._sig_ws_indisponible.connect(self._on_ws_indisponible_thread)
        self._sig_ws_ferme.connect(self._on_ws_ferme_thread)
        self._sig_niveau_micro.connect(self._on_niveau_micro_thread)

    @property
    def est_active(self) -> bool:
        """Indique si une session vocale streaming est actuellement ouverte."""
        return self._active

    @property
    def mode(self) -> ModeDeclenchement:
        """Retourne le mode de déclenchement actif (``"push"`` ou ``"continu"``)."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Met à jour le mode de déclenchement (``"push"`` ou ``"continu"``)."""
        self._mode = "continu" if str(mode).strip().lower() == "continu" else "push"

    def demarrer(self, mode: str | None = None, agent: str | None = None) -> None:
        """Démarre la capture micro et la connexion WebSocket ``/ws/voix``.

        Si une session est déjà active, elle est arrêtée proprement avant d'en ouvrir une nouvelle.

        Args:
            mode: ``"push"`` (une question/réponse) ou ``"continu"`` (mains libres).
            agent: Identifiant d'agent cible optionnel (``"jarvis"`` par défaut).

        Effets de bord:
            Lance le thread asyncio dédié et passe l'état en ``"ecoute"``.
        """
        if self._active:
            self.arreter()

        if mode is not None:
            self.set_mode(mode)
        if agent:
            self._agent = agent

        self._session_id += 1
        session_id = self._session_id
        self._active = True
        self._reponse_fin_recue = False
        self._meta_audio_attente = None
        self._file_audio.reinitialiser()
        self._en_lecture_audio = False
        self._stop_event.clear()

        self._thread = threading.Thread(
            target=self._entree_thread,
            args=(session_id,),
            name="jarvis-overlay-voice-stream",
            daemon=True,
        )
        self._thread.start()
        self.etat_change.emit("ecoute")

    def basculer(self, mode: str | None = None) -> bool:
        """Démarre la session si inactive, ou l'arrête si déjà en cours.

        Returns:
            ``True`` si la session vient de démarrer, ``False`` si elle a été arrêtée.
        """
        if self._active:
            self.arreter()
            return False
        self.demarrer(mode=mode)
        return True

    def interrompre(self) -> None:
        """Coupe immédiatement la lecture TTS et signale ``interrompre`` sans fermer la session."""
        self._stopper_audio_immediat()
        self._reponse_fin_recue = False
        loop = self._loop
        if self._active and loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._envoyer_json_async, {"type": "interrompre"})
        if self._active:
            self.etat_change.emit("ecoute")

    def arreter(self) -> None:
        """Ferme proprement le WebSocket, le flux micro et la lecture audio en cours.

        Effets de bord:
            Invalide la session courante, vide la file audio et repasse à l'état ``"repos"``.
        """
        etait_active = self._active
        self._session_id += 1
        self._active = False
        self._reponse_fin_recue = False
        self._stop_event.set()
        self._stopper_audio_immediat()

        loop = self._loop
        if loop is not None and not loop.is_closed():
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._signaler_arret_async)

        if self._thread is not None and self._thread.is_alive():
            if threading.current_thread() is not self._thread:
                self._thread.join(timeout=2.0)
        self._thread = None

        if etait_active:
            self.etat_change.emit("repos")

    def _signaler_arret_async(self) -> None:
        """Envoie ``{"type":"fin"}``, réveille l'événement d'arrêt et ferme la socket active."""
        if self._ws_courant is not None:
            asyncio.create_task(self._fermer_ws_proprement(self._ws_courant))
        if self._stop_async is not None:
            self._stop_async.set()

    def _envoyer_json_async(self, payload: dict[str, Any]) -> None:
        """Planifie l'envoi d'une trame JSON sur le WebSocket courant depuis la boucle asyncio."""
        ws = self._ws_courant
        if ws is not None:
            asyncio.create_task(self._envoyer_trame_silencieux(ws, json.dumps(payload, ensure_ascii=False)))

    @staticmethod
    async def _envoyer_trame_silencieux(ws: Any, trame: str) -> None:
        """Envoie une trame texte WebSocket sans propager d'erreur de fermeture."""
        with contextlib.suppress(Exception):
            await ws.send(trame)

    @staticmethod
    async def _fermer_ws_proprement(ws: Any) -> None:
        """Envoie ``fin`` puis ferme la connexion WebSocket sans lever d'exception."""
        with contextlib.suppress(Exception):
            await ws.send(json.dumps({"type": "fin"}))
        with contextlib.suppress(Exception):
            await ws.close()

    def _entree_thread(self, session_id: int) -> None:
        """Point d'entrée du thread asyncio de capture micro et WebSocket ``/ws/voix``."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._boucle_voix(session_id))
        finally:
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None
            self._stop_async = None
            self._ws_courant = None

    async def _lire_parametres_voix(self) -> dict[str, Any]:
        """Récupère les paramètres vocaux via ``GET /api/parametres`` sans bloquer Qt."""
        if not self._http_url:
            return {}
        entetes: dict[str, str] = {}
        jeton = lire_jeton(self._token_path)
        if jeton:
            entetes["X-Jarvis-Token"] = jeton
        try:
            async with httpx.AsyncClient(timeout=2.5) as http:
                resp = await http.get(f"{self._http_url}/api/parametres", headers=entetes)
            if resp.status_code >= 400:
                return {}
            donnees = resp.json()
            if isinstance(donnees, dict) and isinstance(donnees.get("voix"), dict):
                return dict(donnees["voix"])
        except Exception as exc:
            logger.debug("Lecture /api/parametres impossible avant /ws/voix (%s).", exc)
        return {}

    async def _boucle_voix(self, session_id: int) -> None:
        """Connecte ``/ws/voix``, configure la session et relaie micro/réponses jusqu'à l'arrêt."""
        stop_async = asyncio.Event()
        self._stop_async = stop_async
        if self._stop_event.is_set() or session_id != self._session_id:
            stop_async.set()
            return

        voix_cfg = await self._lire_parametres_voix()
        if self._stop_event.is_set() or session_id != self._session_id:
            return

        device = resoudre_peripherique_micro(voix_cfg.get("micro"))
        jeton = lire_jeton(self._token_path)
        url = f"{self._ws_url}?token={quote(jeton)}" if jeton else self._ws_url

        pret_event = asyncio.Event()
        audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)
        loop = asyncio.get_running_loop()

        try:
            async with self._ws_connect_fn(
                url,
                open_timeout=5.0,
                ping_interval=20.0,
                ping_timeout=20.0,
            ) as ws:
                self._ws_courant = ws
                await ws.send(json.dumps({"type": "config", "agent": self._agent}, ensure_ascii=False))

                lecteur = asyncio.create_task(self._tache_lecture_ws(ws, session_id, pret_event))
                envoyeur = asyncio.create_task(
                    self._tache_envoi_micro(ws, session_id, device, pret_event, audio_queue, loop, stop_async)
                )
                arret_task = asyncio.create_task(stop_async.wait())

                _, restantes = await asyncio.wait(
                    {lecteur, envoyeur, arret_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for tache in restantes:
                    tache.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await tache

                if not pret_event.is_set() and not self._stop_event.is_set() and session_id == self._session_id:
                    self._sig_ws_indisponible.emit(
                        session_id,
                        "Le serveur vocal /ws/voix a fermé la connexion avant d'être prêt.",
                    )
                    return
        except (OSError, TimeoutError, InvalidStatus, InvalidHandshake, WebSocketException) as exc:
            if not pret_event.is_set() and not self._stop_event.is_set() and session_id == self._session_id:
                logger.info("WebSocket /ws/voix indisponible (%s), demande de repli talkie-walkie.", exc)
                self._sig_ws_indisponible.emit(session_id, f"WebSocket /ws/voix indisponible ({exc}).")
                return
            if not self._stop_event.is_set() and session_id == self._session_id:
                self._sig_ws_ferme.emit(session_id, f"Connexion vocale interrompue : {exc}")
                return
        except Exception as exc:
            if not self._stop_event.is_set() and session_id == self._session_id:
                logger.exception("Erreur inattendue dans la session vocale streaming : %s", exc)
                self._sig_ws_ferme.emit(session_id, str(exc))
                return
        finally:
            self._ws_courant = None

        if not self._stop_event.is_set() and session_id == self._session_id:
            self._sig_ws_ferme.emit(session_id, "")

    async def _tache_lecture_ws(self, ws: Any, session_id: int, pret_event: asyncio.Event) -> None:
        """Reçoit les messages JSON et trames audio binaires depuis ``/ws/voix``."""
        async for trame in ws:
            if self._stop_event.is_set() or session_id != self._session_id:
                break
            if isinstance(trame, (bytes, bytearray, memoryview)):
                self._sig_trame_binaire.emit(session_id, bytes(trame))
                continue
            if not isinstance(trame, str):
                continue
            try:
                msg = json.loads(trame)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "pret":
                pret_event.set()
            self._sig_message_json.emit(session_id, msg)

    async def _tache_envoi_micro(
        self,
        ws: Any,
        session_id: int,
        device: int | str | None,
        pret_event: asyncio.Event,
        audio_queue: asyncio.Queue[bytes],
        loop: asyncio.AbstractEventLoop,
        stop_async: asyncio.Event,
    ) -> None:
        """Attend ``pret``, ouvre ``RawInputStream`` et pousse les blocs 20 ms vers le WS."""
        await pret_event.wait()
        if self._stop_event.is_set() or stop_async.is_set() or session_id != self._session_id:
            return

        def _mettre_en_file(bloc: bytes) -> None:
            if audio_queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    audio_queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                audio_queue.put_nowait(bloc)

        def _callback_audio(indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
            if self._stop_event.is_set() or session_id != self._session_id:
                return
            copie = bytes(indata)
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_mettre_en_file, copie)

        try:
            stream = self._stream_factory(
                samplerate=TAUX_ECHANTILLONNAGE,
                blocksize=BLOC_ECHANTILLONS,
                device=device,
                channels=CANAUX,
                dtype="int16",
                callback=_callback_audio,
            )
        except Exception as exc:
            logger.warning("Ouverture du microphone impossible (%s).", exc)
            self._sig_ws_ferme.emit(session_id, f"Microphone indisponible : {exc}")
            stop_async.set()
            return

        dernier_niveau = 0.0
        try:
            with stream:
                while not self._stop_event.is_set() and not stop_async.is_set() and session_id == self._session_id:
                    bloc = await audio_queue.get()
                    if self._stop_event.is_set() or stop_async.is_set() or session_id != self._session_id:
                        break
                    await ws.send(bloc)
                    maintenant = time.monotonic()
                    if maintenant - dernier_niveau >= INTERVALLE_NIVEAU_MICRO_MIN_S:
                        dernier_niveau = maintenant
                        self._sig_niveau_micro.emit(session_id, calculer_rms_pcm16(bloc))
        except Exception as exc:
            if not self._stop_event.is_set() and session_id == self._session_id:
                logger.debug("Arrêt de l'envoi audio micro (%s).", exc)
                raise

    # --- Slots thread Qt ---

    def _on_message_json_thread(self, session_id: int, msg: dict[str, Any]) -> None:
        """Filtre la session active avant d'aiguiller un message JSON sur le thread Qt."""
        if session_id != self._session_id or not self._active:
            return
        self.traiter_message_json(msg)

    def _on_trame_binaire_thread(self, session_id: int, donnees: bytes) -> None:
        """Filtre la session active avant de traiter une trame audio binaire sur le thread Qt."""
        if session_id != self._session_id or not self._active:
            return
        self.traiter_trame_binaire(donnees)

    def _on_ws_indisponible_thread(self, session_id: int, raison: str) -> None:
        """Ferme la session streaming et demande le repli talkie-walkie."""
        if session_id != self._session_id:
            return
        self.arreter()
        self.repli_demande.emit(raison)

    def _on_ws_ferme_thread(self, session_id: int, raison: str) -> None:
        """Nettoie la session après déconnexion inattendue du serveur ou erreur micro."""
        if session_id != self._session_id or not self._active:
            return
        self.arreter()
        if raison:
            self.erreur.emit(raison)

    def _on_niveau_micro_thread(self, session_id: int, valeur: float) -> None:
        """Transmet le niveau RMS du microphone à l'orbe pendant l'écoute."""
        if session_id != self._session_id or not self._active or self._en_lecture_audio:
            return
        self.niveau.emit(valeur)

    def traiter_message_json(self, msg: dict[str, Any]) -> None:
        """Traite un événement JSON reçu depuis ``/ws/voix`` sur le thread Qt.

        Args:
            msg: Enveloppe JSON conforme au contrat ``ARCHITECTURE-VOIX.md``.
        """
        msg_type = str(msg.get("type") or "")
        if msg_type == "pret":
            self.etat_change.emit("ecoute")
        elif msg_type == "parole_debut":
            self._stopper_audio_immediat()
            self._reponse_fin_recue = False
            self.etat_change.emit("ecoute")
        elif msg_type == "partiel":
            self.partiel.emit(str(msg.get("texte") or ""))
        elif msg_type == "final":
            self._stopper_audio_immediat()
            self._reponse_fin_recue = False
            self.final.emit(str(msg.get("texte") or ""))
            self.etat_change.emit("reflexion")
        elif msg_type == "rien":
            if not self._en_lecture_audio:
                self.etat_change.emit("ecoute")
        elif msg_type == "reponse_delta":
            delta = str(msg.get("texte") or "")
            if delta:
                self.reponse_delta.emit(delta)
        elif msg_type == "audio_debut":
            seq_brut = msg.get("seq", 0)
            seq = int(seq_brut) if isinstance(seq_brut, int) and seq_brut >= 0 else 0
            mime = str(msg.get("mime") or "audio/mpeg")
            self._meta_audio_attente = (seq, mime)
            self._reponse_fin_recue = False
        elif msg_type == "reponse_fin":
            self._reponse_fin_recue = True
            self._demarrer_lecture_suivante()
        elif msg_type == "stop_audio":
            self._stopper_audio_immediat()
            self._reponse_fin_recue = False
            if self._active:
                self.etat_change.emit("ecoute")
        elif msg_type == "erreur":
            message = str(msg.get("message") or "Erreur vocale du serveur.")
            self.erreur.emit(message)
            if not self._en_lecture_audio and self._active:
                self.etat_change.emit("ecoute")

    def traiter_trame_binaire(self, donnees: bytes) -> None:
        """Associe une trame audio binaire au dernier ``audio_debut`` et lance la lecture ordonnée."""
        meta = self._meta_audio_attente
        self._meta_audio_attente = None
        if meta is None:
            return
        seq, mime = meta
        self._file_audio.ajouter(seq, donnees, mime)
        self._demarrer_lecture_suivante()

    # --- Lecture audio QMediaPlayer + QBuffer ---

    def _liberer_buffer_actif(self) -> None:
        """Détache et libère proprement le ``QBuffer`` en mémoire après lecture ou arrêt."""
        if self._buffer_actif is not None:
            if hasattr(self._player, "setSource"):
                self._player.setSource(QUrl())
            self._buffer_actif.close()
            self._buffer_actif.deleteLater()
            self._buffer_actif = None
        self._donnees_actives = None

    def _demarrer_lecture_suivante(self) -> None:
        """Lance la phrase ``seq`` suivante dès qu'elle est prête, ou termine le tour."""
        if self._en_lecture_audio:
            return

        suivant = self._file_audio.suivant_pret()
        if suivant is None and self._reponse_fin_recue and not self._file_audio.est_vide():
            suivant = self._file_audio.forcer_suivant_disponible()

        if suivant is None:
            self._verifier_fin_reponse()
            return

        _seq, donnees, mime = suivant
        self._en_lecture_audio = True
        self._liberer_buffer_actif()

        self._donnees_actives = QByteArray(donnees)
        buf = QBuffer(self._donnees_actives, self)
        buf.open(QIODeviceBase.OpenModeFlag.ReadOnly)
        self._buffer_actif = buf

        nom_virtuel = "phrase.wav" if "wav" in mime.lower() else "phrase.mp3"
        self.etat_change.emit("parole")
        if not self._timer_niveau_sortie.isActive():
            self._timer_niveau_sortie.start()

        if hasattr(self._player, "setSourceDevice"):
            self._player.setSourceDevice(buf, QUrl(nom_virtuel))
        if hasattr(self._player, "play"):
            self._player.play()

    def _terminer_piste_courante(self) -> None:
        """Enchaîne immédiatement la phrase suivante ou conclut la réponse si terminée."""
        if not self._en_lecture_audio:
            return
        self._en_lecture_audio = False
        self._liberer_buffer_actif()
        self._demarrer_lecture_suivante()

    def _verifier_fin_reponse(self) -> None:
        """Conclut le tour vocal une fois ``reponse_fin`` reçue et la file audio épuisée."""
        if not self._reponse_fin_recue or self._en_lecture_audio or not self._file_audio.est_vide():
            return
        self._reponse_fin_recue = False
        if self._timer_niveau_sortie.isActive():
            self._timer_niveau_sortie.stop()
        self.niveau.emit(0.0)
        self.reponse_fin.emit()

        if self._mode == "push":
            self.arreter()
        elif self._active:
            self.etat_change.emit("ecoute")

    def _stopper_audio_immediat(self) -> None:
        """Interrompt immédiatement la lecture audio et vide toute la file ``seq`` en mémoire."""
        self._file_audio.reinitialiser()
        self._meta_audio_attente = None
        self._en_lecture_audio = False
        if self._timer_niveau_sortie.isActive():
            self._timer_niveau_sortie.stop()
        if hasattr(self._player, "stop"):
            self._player.stop()
        self._liberer_buffer_actif()
        self.niveau.emit(0.0)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Enchaîne sans trou dès que le média courant atteint ``EndOfMedia``."""
        if self._en_lecture_audio and status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._terminer_piste_courante()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Repli d'enchaînement si le lecteur passe en ``StoppedState`` en fin de piste."""
        if self._en_lecture_audio and state == QMediaPlayer.PlaybackState.StoppedState:
            self._terminer_piste_courante()

    def _on_media_error(self, error: QMediaPlayer.Error, message: str) -> None:
        """Journalise une erreur de décodage et passe à la phrase suivante sans bloquer la file."""
        logger.warning("Erreur lecture streaming QMediaPlayer (%s) : %s", error, message)
        if self._en_lecture_audio:
            self._terminer_piste_courante()

    def _tic_niveau_sortie(self) -> None:
        """Lit toutes les 33 ms le niveau de crête de sortie audio Windows (ou simulé)."""
        if not self._en_lecture_audio:
            if self._timer_niveau_sortie.isActive():
                self._timer_niveau_sortie.stop()
            self.niveau.emit(0.0)
            return
        self.niveau.emit(lire_niveau_sortie_ou_simule(self._lecteur_sortie))
