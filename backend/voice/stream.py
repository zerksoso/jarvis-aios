"""Pipeline de conversation vocale quasi temps réel (VAD Silero, découpage phrases, session WS)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend import config, db, settings_store
from backend.api import chat
from backend.api.chat import ChatRequest
from backend.brain import memories
from backend.llm.agy_session import MODELE_RAPIDE_DEFAUT, SessionAgyChaude
from backend.llm.llm_local_session import SessionLLMLocale
from backend.voice import kyutai, stt, tts
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

MAX_PROFIL_RAPIDE_CHARS = 3000
MARQUEUR_AGIR = "[[AGIR]]"
PHRASE_ACTION_EN_COURS = "Je m'en occupe."

FREQUENCE_ECHANTILLONNAGE = 16000
TAILLE_FENETRE_VAD = 512
TAILLE_CONTEXTE_SILERO = 64

SEUIL_VAD_DEFAUT = 0.5
DUREE_DEBUT_PAROLE_MS_DEFAUT = 200
DUREE_BARGE_IN_MS_DEFAUT = 300
DUREE_FIN_SILENCE_MS_DEFAUT = 700
DUREE_PRE_ROLL_MS_DEFAUT = 300
DUREE_MAX_ENONCE_MS_DEFAUT = 30_000
INTERVALLE_PARTIEL_S_DEFAUT = 0.6
LONGUEUR_MIN_PHRASE = 25

_ABREVIATIONS = frozenset(
    {
        "m",
        "mr",
        "mme",
        "mlle",
        "dr",
        "pr",
        "me",
        "mgr",
        "st",
        "ste",
        "sr",
        "jr",
        "etc",
        "ex",
        "cf",
        "vs",
        "env",
        "approx",
        "max",
        "min",
        "tel",
        "tél",
        "ref",
        "réf",
        "fig",
        "art",
        "al",
        "chap",
        "vol",
        "p",
        "pp",
        "n",
        "no",
        "n°",
        "av",
        "ap",
        "janv",
        "févr",
        "fevr",
        "avr",
        "juil",
        "sept",
        "oct",
        "nov",
        "déc",
        "dec",
    }
)

_RE_MOT_AVANT_POINT = re.compile(r"([A-Za-zÀ-ÖØ-öø-ÿ0-9°]+)$")
_RE_LISTE_NUMEROTEE = re.compile(r"^\s*\d+$")
_RE_ABREV_COMPOSEE = re.compile(
    r"(?:^|[\s(])(?:c\.?-?à\.?-?d|p\.?\s*ex|s\.?v\.?p|j\.?-?c)\.$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class EvenementVAD:
    """Événement émis par ``DetecteurParole`` lors de l'analyse d'un flux audio.

    Attributes:
        type: ``"debut"`` dès que la parole est confirmée, ``"fin"`` à la fin de l'énoncé.
        audio: Tampon ``float32`` 16 kHz mono complet (pré-roll inclus) pour ``"fin"``.
        raison: Cause de clôture de l'énoncé (``"silence"`` ou ``"duree_max"``).
    """

    type: Literal["debut", "fin"]
    audio: np.ndarray | None = None
    raison: str | None = None


def _obtenir_modele_vad() -> Any:
    """Charge paresseusement le modèle ONNX Silero VAD fourni par ``faster_whisper.vad``."""
    from faster_whisper.vad import get_vad_model

    return get_vad_model()


class DetecteurParole:
    """Détecteur de parole en flux basé sur Silero VAD (fenêtres 512 échantillons à 16 kHz).

    Gère un tampon glissant de pré-roll, un délai de confirmation d'amorce, la clôture
    sur silence prolongé et le plafonnement de durée d'énoncé sans aucune écriture disque.
    """

    def __init__(
        self,
        vad: Any | None = None,
        *,
        seuil: float = SEUIL_VAD_DEFAUT,
        debut_ms: int = DUREE_DEBUT_PAROLE_MS_DEFAUT,
        fin_silence_ms: int = DUREE_FIN_SILENCE_MS_DEFAUT,
        pre_roll_ms: int = DUREE_PRE_ROLL_MS_DEFAUT,
        max_duree_ms: int = DUREE_MAX_ENONCE_MS_DEFAUT,
        frequence: int = FREQUENCE_ECHANTILLONNAGE,
    ) -> None:
        """Initialise l'automate VAD en flux.

        Args:
            vad: Modèle Silero VAD ou fonction testable ``(fenetre_f32) -> prob``.
                Si ``None``, charge ``faster_whisper.vad.get_vad_model()`` à la demande.
            seuil: Seuil de probabilité de voix (défaut ``0.5``).
            debut_ms: Durée minimale de voix continue pour confirmer le début (défaut ``200`` ms).
            fin_silence_ms: Durée de silence après parole déclenchant la fin (défaut ``700`` ms).
            pre_roll_ms: Durée d'audio conservée avant le début détecté (défaut ``300`` ms).
            max_duree_ms: Durée maximale d'un énoncé avant coupe forcée (défaut ``30_000`` ms).
            frequence: Fréquence d'échantillonnage en Hz (défaut ``16000``).
        """
        self._vad = vad
        self.seuil = float(seuil)
        self.debut_ms = int(debut_ms)
        self.fin_silence_ms = int(fin_silence_ms)
        self.pre_roll_ms = int(pre_roll_ms)
        self.max_duree_ms = int(max_duree_ms)
        self.frequence = int(frequence)

        self._pre_roll_samples = int(self.frequence * self.pre_roll_ms / 1000)
        self._fin_silence_samples = int(self.frequence * self.fin_silence_ms / 1000)
        self._max_enonce_samples = int(self.frequence * self.max_duree_ms / 1000)

        self._reste = np.zeros(0, dtype=np.float32)
        self._pre_roll = np.zeros(0, dtype=np.float32)
        self._candidat_parole: list[np.ndarray] = []
        self._tampon_enonce: list[np.ndarray] = []
        self._en_parole = False
        self._voix_consec_samples = 0
        self._silence_samples = 0
        self._enonce_samples = 0

        # État récurrent Silero VAD v6 conservé par session pour le traitement trame par trame.
        self._silero_h = np.zeros((1, 1, 128), dtype=np.float32)
        self._silero_c = np.zeros((1, 1, 128), dtype=np.float32)
        self._silero_context = np.zeros(TAILLE_CONTEXTE_SILERO, dtype=np.float32)

    @property
    def parole_active(self) -> bool:
        """Indique si un énoncé vocal est actuellement ouvert."""
        return self._en_parole

    def reinitialiser(self) -> None:
        """Réinitialise complètement les tampons et l'état interne du détecteur."""
        self._reste = np.zeros(0, dtype=np.float32)
        self._pre_roll = np.zeros(0, dtype=np.float32)
        self._candidat_parole.clear()
        self._tampon_enonce.clear()
        self._en_parole = False
        self._voix_consec_samples = 0
        self._silence_samples = 0
        self._enonce_samples = 0
        self._silero_h.fill(0.0)
        self._silero_c.fill(0.0)
        self._silero_context.fill(0.0)

    def audio_courant(self) -> np.ndarray:
        """Retourne une copie de l'audio accumulé pour l'énoncé en cours (vide sinon)."""
        if not self._en_parole or not self._tampon_enonce:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._tampon_enonce).copy()

    def _evaluer_fenetre(self, fenetre: np.ndarray) -> float:
        """Calcule la probabilité de voix sur une fenêtre de 512 échantillons float32."""
        if self._vad is None:
            self._vad = _obtenir_modele_vad()

        session = getattr(self._vad, "session", None)
        if session is not None:
            entree = np.concatenate([self._silero_context, fenetre])[None, :]
            sortie, self._silero_h, self._silero_c = session.run(
                None,
                {"input": entree, "h": self._silero_h, "c": self._silero_c},
            )
            self._silero_context = fenetre[-TAILLE_CONTEXTE_SILERO:].copy()
            return float(np.asarray(sortie).reshape(-1)[0])

        res = self._vad(fenetre)
        return float(np.asarray(res).reshape(-1)[0])

    def _ajouter_pre_roll(self, morceau: np.ndarray) -> None:
        """Ajoute des échantillons au tampon circulaire de pré-roll borné à ``pre_roll_ms``."""
        if self._pre_roll_samples <= 0 or morceau.size == 0:
            return
        if self._pre_roll.size == 0:
            self._pre_roll = morceau[-self._pre_roll_samples :].copy()
        else:
            combine = np.concatenate([self._pre_roll, morceau])
            self._pre_roll = combine[-self._pre_roll_samples :].copy()

    def _terminer_enonce(self) -> None:
        """Clôture l'énoncé actif tout en conservant le reliquat non fenêtré."""
        self._en_parole = False
        self._tampon_enonce.clear()
        self._candidat_parole.clear()
        self._voix_consec_samples = 0
        self._silence_samples = 0
        self._enonce_samples = 0
        self._pre_roll = np.zeros(0, dtype=np.float32)

    @staticmethod
    def _convertir_float32(donnees: bytes | np.ndarray) -> np.ndarray:
        """Convertit des octets PCM int16 LE ou un tableau numpy en vecteur float32 [-1, 1]."""
        if isinstance(donnees, (bytes, bytearray, memoryview)):
            brut = bytes(donnees)
            taille_paire = len(brut) - (len(brut) % 2)
            if taille_paire <= 0:
                return np.zeros(0, dtype=np.float32)
            return np.frombuffer(brut[:taille_paire], dtype="<i2").astype(np.float32) / 32768.0

        tableau = np.asarray(donnees)
        if tableau.size == 0:
            return np.zeros(0, dtype=np.float32)
        if np.issubdtype(tableau.dtype, np.integer):
            return (tableau.astype(np.float32) / 32768.0).reshape(-1)
        return tableau.astype(np.float32, copy=False).reshape(-1)

    def alimenter(self, donnees: bytes | np.ndarray) -> list[EvenementVAD]:
        """Injecte un bloc audio et retourne les transitions VAD détectées.

        Args:
            donnees: Octets PCM 16 kHz mono int16 LE (longueur paire) ou tableau numpy.

        Returns:
            Liste ordonnée d'``EvenementVAD`` (``"debut"`` et/ou ``"fin"``).
        """
        echantillons = self._convertir_float32(donnees)
        if echantillons.size == 0:
            return []

        flux = (
            np.concatenate([self._reste, echantillons])
            if self._reste.size > 0
            else echantillons
        )
        nb_fenetres = flux.size // TAILLE_FENETRE_VAD
        if nb_fenetres == 0:
            self._reste = flux.copy()
            return []

        limite = nb_fenetres * TAILLE_FENETRE_VAD
        self._reste = flux[limite:].copy()

        evenements: list[EvenementVAD] = []
        for idx in range(nb_fenetres):
            debut_idx = idx * TAILLE_FENETRE_VAD
            fenetre = flux[debut_idx : debut_idx + TAILLE_FENETRE_VAD]
            est_voix = self._evaluer_fenetre(fenetre) >= self.seuil

            if not self._en_parole:
                debut_samples = int(self.frequence * self.debut_ms / 1000)
                if est_voix:
                    self._voix_consec_samples += TAILLE_FENETRE_VAD
                    self._candidat_parole.append(fenetre)
                    if self._voix_consec_samples >= debut_samples:
                        self._en_parole = True
                        self._silence_samples = 0
                        morceaux: list[np.ndarray] = []
                        if self._pre_roll.size > 0:
                            morceaux.append(self._pre_roll)
                        morceaux.extend(self._candidat_parole)
                        self._tampon_enonce = morceaux
                        self._enonce_samples = sum(len(m) for m in morceaux)
                        self._candidat_parole.clear()
                        self._pre_roll = np.zeros(0, dtype=np.float32)
                        evenements.append(EvenementVAD(type="debut"))
                else:
                    if self._candidat_parole:
                        self._ajouter_pre_roll(np.concatenate(self._candidat_parole))
                        self._candidat_parole.clear()
                    self._voix_consec_samples = 0
                    self._ajouter_pre_roll(fenetre)
            else:
                self._tampon_enonce.append(fenetre)
                self._enonce_samples += TAILLE_FENETRE_VAD
                if est_voix:
                    self._silence_samples = 0
                else:
                    self._silence_samples += TAILLE_FENETRE_VAD

                if self._enonce_samples >= self._max_enonce_samples:
                    audio_fin = np.concatenate(self._tampon_enonce)[: self._max_enonce_samples].copy()
                    self._terminer_enonce()
                    evenements.append(EvenementVAD(type="fin", audio=audio_fin, raison="duree_max"))
                elif self._silence_samples >= self._fin_silence_samples:
                    audio_fin = np.concatenate(self._tampon_enonce).copy()
                    self._terminer_enonce()
                    evenements.append(EvenementVAD(type="fin", audio=audio_fin, raison="silence"))

        return evenements


def _est_fin_phrase(texte: str, pos_ponct: int) -> bool:
    """Indique si la ponctuation à ``pos_ponct`` clôt réellement une phrase parlée."""
    caractere = texte[pos_ponct]
    if caractere in ("!", "?", "…", ":"):
        return True
    if caractere != ".":
        return False

    # Points de suspension "..." : fin de phrase dès qu'ils sont suivis d'un espace.
    if pos_ponct > 0 and texte[pos_ponct - 1] == ".":
        return True

    prefixe = texte[:pos_ponct]
    debut_ligne = prefixe.rfind("\n") + 1
    ligne_avant = prefixe[debut_ligne:]
    if _RE_LISTE_NUMEROTEE.match(ligne_avant):
        return False

    if _RE_ABREV_COMPOSEE.search(texte[: pos_ponct + 1]):
        return False

    match_mot = _RE_MOT_AVANT_POINT.search(prefixe)
    if match_mot:
        mot = match_mot.group(1)
        if mot.lower() in _ABREVIATIONS:
            return False
        if len(mot) == 1 and mot.isupper():
            pos_debut = match_mot.start(1)
            if pos_debut == 0 or prefixe[pos_debut - 1] in " \t\n\r(\"'«-":
                return False

    return True


def _trouver_prochaine_balise_code(texte: str) -> tuple[int, str] | None:
    """Trouve la première ouverture de bloc de code Markdown (```` ``` ```` ou ``~~~``)."""
    idx_bt = texte.find("```")
    idx_ti = texte.find("~~~")
    if idx_bt == -1 and idx_ti == -1:
        return None
    if idx_bt != -1 and (idx_ti == -1 or idx_bt < idx_ti):
        return idx_bt, "```"
    return idx_ti, "~~~"


def _longueur_suffixe_potentiel_balise(texte: str) -> int:
    """Retourne 1 ou 2 si ``texte`` se termine par un début de balise code possible."""
    for taille in (2, 1):
        if texte.endswith("`" * taille) or texte.endswith("~" * taille):
            return taille
    return 0


class DecoupeurPhrases:
    """Découpeur incrémental pur de deltas LLM en phrases propres prêtes pour le TTS.

    Ignore les blocs de code Markdown, nettoie le balisage via
    ``tts.nettoyer_markdown_pour_tts``, préserve les nombres et abréviations courantes,
    et regroupe les phrases de moins de 25 caractères avec la suivante.
    """

    def __init__(self, longueur_min: int = LONGUEUR_MIN_PHRASE) -> None:
        """Initialise un découpeur incrémental vide.

        Args:
            longueur_min: Longueur minimale d'une phrase émise seule avant regroupement (25).
        """
        self._longueur_min = int(longueur_min)
        self._brut = ""
        self._attente = ""
        self._dans_bloc_code = False
        self._marqueur_bloc = ""

    def reinitialiser(self) -> None:
        """Efface tout texte accumulé dans le découpeur."""
        self._brut = ""
        self._attente = ""
        self._dans_bloc_code = False
        self._marqueur_bloc = ""

    def _pousser_segment(self, segment_brut: str, sortie: list[str]) -> None:
        """Nettoie un segment et l'émet ou le met en attente s'il est trop court."""
        propre = tts.nettoyer_markdown_pour_tts(segment_brut)
        if not propre:
            return
        combine = f"{self._attente} {propre}".strip() if self._attente else propre
        if len(combine) < self._longueur_min:
            self._attente = combine
            return
        self._attente = ""
        sortie.append(combine)

    def _extraire_prose(self, texte: str, *, forcer_fin: bool, sortie: list[str]) -> str:
        """Extrait les phrases complètes d'un bloc hors code et retourne le reliquat."""
        debut = 0
        taille = len(texte)
        for i, caractere in enumerate(texte):
            if i < debut:
                continue
            if caractere == "\n":
                self._pousser_segment(texte[debut:i], sortie)
                debut = i + 1
            elif caractere in (".", "!", "?", "…", ":"):
                if i + 1 < taille and texte[i + 1].isspace() and _est_fin_phrase(texte, i):
                    self._pousser_segment(texte[debut : i + 1], sortie)
                    debut = i + 1

        reliquat = texte[debut:]
        if forcer_fin:
            if reliquat.strip():
                self._pousser_segment(reliquat, sortie)
            return ""
        return reliquat

    def alimenter(self, delta: str) -> list[str]:
        """Ajoute un fragment de texte streamé et retourne les phrases prêtes à lire.

        Args:
            delta: Morceau de texte reçu du LLM.

        Returns:
            Liste des phrases nettoyées complètes (éventuellement vide).
        """
        if not delta:
            return []

        self._brut += delta
        sortie: list[str] = []

        while self._brut:
            if self._dans_bloc_code:
                idx_fin = self._brut.find(self._marqueur_bloc)
                if idx_fin == -1:
                    if len(self._brut) > 2:
                        self._brut = self._brut[-2:]
                    break
                self._brut = self._brut[idx_fin + 3 :]
                self._dans_bloc_code = False
                self._marqueur_bloc = ""
                continue

            balise = _trouver_prochaine_balise_code(self._brut)
            if balise is not None:
                idx_ouv, marqueur = balise
                avant = self._brut[:idx_ouv]
                if avant:
                    self._extraire_prose(avant, forcer_fin=True, sortie=sortie)
                self._dans_bloc_code = True
                self._marqueur_bloc = marqueur
                self._brut = self._brut[idx_ouv + 3 :]
                continue

            garde = _longueur_suffixe_potentiel_balise(self._brut)
            tranche = self._brut[: len(self._brut) - garde] if garde else self._brut
            suffixe = self._brut[len(self._brut) - garde :] if garde else ""
            reliquat = self._extraire_prose(tranche, forcer_fin=False, sortie=sortie)
            self._brut = reliquat + suffixe
            break

        return sortie

    def terminer(self) -> list[str]:
        """Vide le reliquat en fin de génération LLM et retourne les dernières phrases.

        Returns:
            Liste des phrases restantes nettoyées (y compris si < 25 caractères).
        """
        sortie: list[str] = []
        if not self._dans_bloc_code and self._brut:
            self._extraire_prose(self._brut, forcer_fin=True, sortie=sortie)
        if self._attente:
            sortie.append(self._attente)
        self.reinitialiser()
        return sortie


def _lire_profil_utilisateur_tronque(limite: int = MAX_PROFIL_RAPIDE_CHARS) -> str:
    """Lit ``BRAIN_DIR/00-Profil/profil-utilisateur.md`` et le tronque à ``limite`` caractères."""
    chemin = config.BRAIN_DIR / "00-Profil" / "profil-utilisateur.md"
    if not chemin.is_file():
        return ""
    try:
        texte = chemin.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(texte) > limite:
        return texte[:limite].rstrip()
    return texte


def _construire_premier_message_rapide(texte_utilisateur: str) -> str:
    """Construit le premier message envoyé à ``SessionAgyChaude`` (consigne vocale + souvenirs + profil)."""
    sections: list[str] = [
        (
            "CONSIGNE VOCALE :\n"
            "Tu es Jarvis, l'assistant personnel vocal de l'utilisateur.\n"
            "Réponds uniquement en français oral naturel, avec des phrases courtes et claires.\n"
            "N'utilise JAMAIS de Markdown, d'astérisques, de titres, de listes à puces ni de blocs de code.\n"
            "Limite ta réponse à 1 à 4 phrases concises, sauf demande explicite de détails.\n"
            f"ACTION REQUISE : si la demande nécessite d'agir concrètement (PC, fichiers, second cerveau, "
            f"skills, agents, tâches ou routines), réponds UNIQUEMENT par une ligne unique commençant par "
            f"« {MARQUEUR_AGIR} <consigne reformulée> », sans aucun autre texte."
        )
    ]

    try:
        souvenirs = memories.lister_souvenirs()
    except Exception:
        souvenirs = []

    lignes_souvenirs: list[str] = []
    for item in souvenirs[-20:]:
        texte_s = str(item.get("texte") or "").strip()
        type_s = str(item.get("type") or "fait").strip()
        if texte_s:
            lignes_souvenirs.append(f"- [{type_s}] {texte_s}")
    if lignes_souvenirs:
        sections.append("SOUVENIRS UTILISATEUR :\n" + "\n".join(lignes_souvenirs))

    profil = _lire_profil_utilisateur_tronque()
    if profil:
        sections.append("PROFIL UTILISATEUR :\n" + profil)

    sections.append(f"DEMANDE UTILISATEUR :\n{texte_utilisateur}")
    return "\n\n".join(sections)


class SessionVocale:
    """Orchestrateur d'une session vocale bidirectionnelle sur WebSocket ``/ws/voix``.

    Coordonne la détection de parole (VAD), les transcriptions partielles et finales
    en mémoire, la voie rapide via ``SessionAgyChaude`` (avec repli automatique sur
    ``demarrer_chat``), l'écoute du hub, la synthèse TTS ordonnée et le barge-in.
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        agent_id: str = "jarvis",
        voix: str | None = None,
        vad: Any | None = None,
        intervalle_partiel_s: float = INTERVALLE_PARTIEL_S_DEFAUT,
        session_agy: SessionAgyChaude | None = None,
    ) -> None:
        """Initialise une session vocale liée à une socket cliente acceptée.

        Args:
            websocket: Instance ``WebSocket`` FastAPI déjà acceptée.
            agent_id: Agent cible par défaut (``"jarvis"``).
            voix: Voix TTS forcée ou ``None`` pour lire ``settings_store``.
            vad: Modèle VAD optionnel (utile pour les tests unitaires).
            intervalle_partiel_s: Délai minimal entre deux transcriptions partielles.
            session_agy: Instance ``SessionAgyChaude`` injectée optionnelle (tests).
        """
        self._ws = websocket
        self.agent_id = agent_id
        self.voix = voix
        self._intervalle_partiel_s = float(intervalle_partiel_s)

        self._detecteur = DetecteurParole(vad=vad)
        self._decoupeur = DecoupeurPhrases()
        self._ws_send_lock = asyncio.Lock()
        self._vad_lock = asyncio.Lock()

        self._file_tts: asyncio.Queue[tuple[int, str | None]] = asyncio.Queue()
        self._file_hub: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        self._tache_tts: asyncio.Task[None] | None = None
        self._tache_hub: asyncio.Task[None] | None = None
        self._tache_partiel: asyncio.Task[None] | None = None
        self._tache_synth: asyncio.Task[Path] | None = None
        self._tache_prechauffe_agy: asyncio.Task[None] | None = None
        self._tache_kyutai: asyncio.Task[bool] | None = None
        self._tache_rapide: asyncio.Task[None] | None = None

        self._session_agy: Any = session_agy
        self._voie_rapide_bloquee = False

        self._fermee = False
        self._ecouteur_enregistre = False
        self._generation_enonce = 0
        self._dernier_partiel = ""
        self._dernier_temps_partiel = 0.0

        self._turn_id = 0
        self._seq = 0
        self._tour_llm_actif = False
        self._tour_rapide_actif = False
        self._reponse_audio_en_cours = False
        self._reponse_ignoree = False
        self._deltas_recus_pour_tour = False

    def _voix_config(self) -> dict[str, Any]:
        """Retourne le sous-bloc ``voix`` des paramètres courants."""
        try:
            voix_cfg = settings_store.lire().get("voix")
        except Exception:
            return {}
        return voix_cfg if isinstance(voix_cfg, dict) else {}

    def _voie_rapide_active(self) -> bool:
        """Indique si la voie rapide ``agy`` doit être tentée pour le prochain tour."""
        if self._voie_rapide_bloquee:
            return False
        return bool(self._voix_config().get("rapide", True))

    def _creer_session_rapide(self) -> Any:
        """Crée la session de voie rapide : LLM local (LM Studio) ou ``agy`` chaud selon les réglages."""
        cfg = self._voix_config()
        if str(cfg.get("llm_rapide") or "agy") == "local":
            return SessionLLMLocale(
                modele=str(cfg.get("llm_local_modele") or ""),
                url=str(cfg.get("llm_local_url") or ""),
            )
        modele = str(cfg.get("modele_rapide") or MODELE_RAPIDE_DEFAUT).strip() or MODELE_RAPIDE_DEFAUT
        return SessionAgyChaude(modele=modele)

    def _resoudre_voix(self) -> str:
        """Retourne la voix TTS configurée pour la session ou celle des paramètres globaux."""
        if self.voix:
            return self.voix
        nom = str(self._voix_config().get("nom") or "").strip()
        if nom:
            return nom
        return tts.VOIX_DEFAUT

    async def _envoyer_json(self, payload: dict[str, Any]) -> None:
        """Envoie un message JSON au client sous verrou d'envoi atomique."""
        if self._fermee:
            return
        async with self._ws_send_lock:
            if self._fermee:
                return
            try:
                await self._ws.send_json(payload)
            except (WebSocketDisconnect, RuntimeError):
                self._fermee = True

    async def _envoyer_audio_phrase(
        self,
        turn_id: int,
        mime: str,
        phrase: str,
        octets: bytes,
    ) -> bool:
        """Envoie ``audio_debut`` immédiatement suivi de sa trame binaire sans entrelacement."""
        if self._fermee:
            return False
        async with self._ws_send_lock:
            if self._fermee or turn_id != self._turn_id or self._reponse_ignoree:
                return False
            seq = self._seq
            self._seq += 1
            try:
                await self._ws.send_json(
                    {
                        "type": "audio_debut",
                        "seq": seq,
                        "mime": mime,
                        "phrase": phrase,
                    }
                )
                await self._ws.send_bytes(octets)
            except (WebSocketDisconnect, RuntimeError):
                self._fermee = True
                return False
            return True

    async def _diffuser_etat(self, etat: str) -> None:
        """Diffuse ``etat_jarvis`` sur le hub global sans faire échouer la session."""
        try:
            await hub.broadcast("etat_jarvis", {"etat": etat})
        except Exception as exc:
            logger.debug("Diffusion etat_jarvis '%s' ignorée : %s", etat, exc)

    def _sur_evenement_hub(self, msg_type: str, data: dict[str, Any]) -> None:
        """Écouteur synchrone enregistré sur le hub ; filtre les événements du tour courant."""
        if self._fermee or msg_type not in ("chat_debut", "chat_delta", "chat_fin", "chat_erreur"):
            return
        conv_cible = f"agent:{self.agent_id}"
        if data.get("conversation_id") != conv_cible:
            return
        self._file_hub.put_nowait((msg_type, data))

    async def _prechauffer_agy(self) -> None:
        """Démarre en arrière-plan la session ``agy`` chaude si la voie rapide est active."""
        if not self._voie_rapide_active() or self._fermee:
            return
        if self._session_agy is None:
            self._session_agy = self._creer_session_rapide()
        try:
            await self._session_agy.demarrer()
            if isinstance(self._session_agy, SessionLLMLocale):
                await self._session_agy.prechauffer()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info("Préchauffage voie rapide agy indisponible (%s), repli sur voie complète.", exc)
            self._voie_rapide_bloquee = True
            with contextlib.suppress(Exception):
                await self._session_agy.arreter()

    async def demarrer(self) -> None:
        """Précharge Whisper, préchauffe ``agy`` si actif, et envoie ``pret`` au client.

        Effets de bord:
            Enregistre un écouteur sur ``hub``, crée les tâches asyncio TTS/hub,
            précharge faster-whisper dans un thread, lance le préchauffage ``agy``
            si ``voix.rapide`` est vrai, et diffuse ``etat_jarvis=ecoute``.
        """
        hub.ajouter_ecouteur(self._sur_evenement_hub)
        self._ecouteur_enregistre = True
        self._tache_hub = asyncio.create_task(self._boucle_evenements_hub())
        self._tache_tts = asyncio.create_task(self._boucle_tts())

        if str(self._voix_config().get("moteur_tts") or "") == "kyutai":
            self._tache_kyutai = asyncio.create_task(kyutai.assurer_demarre())

        if self._voie_rapide_active():
            self._tache_prechauffe_agy = asyncio.create_task(self._prechauffer_agy())

        try:
            await asyncio.to_thread(stt.precharger)
        except Exception as exc:
            logger.exception("Échec du préchargement Whisper pour /ws/voix")
            await self._envoyer_json(
                {
                    "type": "erreur",
                    "message": f"Impossible de charger la reconnaissance vocale : {exc}",
                    "code": None,
                }
            )
            return

        await self._envoyer_json({"type": "pret"})
        await self._diffuser_etat("ecoute")

    async def fermer(self) -> None:
        """Libère toutes les ressources de la session lors de la déconnexion."""
        if self._fermee:
            return
        self._fermee = True
        self._turn_id += 1
        self._reponse_ignoree = True
        self._tour_rapide_actif = False

        if self._ecouteur_enregistre:
            hub.retirer_ecouteur(self._sur_evenement_hub)
            self._ecouteur_enregistre = False

        for tache in (
            self._tache_prechauffe_agy,
            self._tache_rapide,
            self._tache_partiel,
            self._tache_synth,
            self._tache_hub,
            self._tache_tts,
        ):
            if tache is not None and not tache.done():
                tache.cancel()
        for tache in (
            self._tache_prechauffe_agy,
            self._tache_rapide,
            self._tache_partiel,
            self._tache_synth,
            self._tache_hub,
            self._tache_tts,
        ):
            if tache is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tache

        if self._session_agy is not None:
            with contextlib.suppress(Exception):
                await self._session_agy.arreter()

        self._vider_file_tts()
        await self._diffuser_etat("repos")

    def _vider_file_tts(self) -> None:
        """Vide immédiatement tous les éléments en attente dans la file TTS."""
        while not self._file_tts.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._file_tts.get_nowait()
                self._file_tts.task_done()

    async def _annuler_reponse_audio(self, *, envoyer_stop: bool) -> None:
        """Annule la lecture/file TTS du tour en cours et envoie ``stop_audio`` si demandé."""
        self._turn_id += 1
        self._reponse_ignoree = True
        self._tour_llm_actif = False
        self._tour_rapide_actif = False
        self._reponse_audio_en_cours = False
        self._seq = 0
        self._decoupeur.reinitialiser()
        self._vider_file_tts()

        tache_synth = self._tache_synth
        if tache_synth is not None and not tache_synth.done():
            tache_synth.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tache_synth
        self._tache_synth = None

        if envoyer_stop:
            await self._envoyer_json({"type": "stop_audio"})

    async def interrompre(self) -> None:
        """Coupe immédiatement la réponse vocale en cours à la demande du client."""
        await self._annuler_reponse_audio(envoyer_stop=True)
        await self._diffuser_etat("ecoute")

    async def traiter_message_texte(self, texte_brut: str) -> bool:
        """Traite une trame texte JSON envoyée par le client.

        Args:
            texte_brut: Charge JSON brute.

        Returns:
            ``False`` si le client demande la fermeture (``{"type": "fin"}``), sinon ``True``.
        """
        try:
            data = json.loads(texte_brut)
        except json.JSONDecodeError:
            await self._envoyer_json({"type": "erreur", "message": "Message JSON invalide.", "code": None})
            return True

        if not isinstance(data, dict):
            return True

        msg_type = str(data.get("type") or "").strip()
        if msg_type == "config":
            agent = data.get("agent")
            if isinstance(agent, str) and agent.strip():
                self.agent_id = agent.strip()
            voix = data.get("voix")
            if isinstance(voix, str) and voix.strip():
                self.voix = voix.strip()
        elif msg_type == "interrompre":
            await self.interrompre()
        elif msg_type == "fin":
            return False

        return True

    async def traiter_audio(self, pcm_bytes: bytes) -> None:
        """Traite une trame PCM 16 kHz mono int16 LE reçue du client.

        Args:
            pcm_bytes: Octets PCM int16 little-endian.
        """
        if self._fermee or not pcm_bytes:
            return

        try:
            async with self._vad_lock:
                en_reponse = (
                    self._reponse_audio_en_cours
                    or self._tour_llm_actif
                    or self._tour_rapide_actif
                    or not self._file_tts.empty()
                )
                self._detecteur.debut_ms = (
                    DUREE_BARGE_IN_MS_DEFAUT if en_reponse else DUREE_DEBUT_PAROLE_MS_DEFAUT
                )
                evenements = await asyncio.to_thread(self._detecteur.alimenter, pcm_bytes)
                parole_active = self._detecteur.parole_active
                audio_partiel = self._detecteur.audio_courant() if parole_active else None

            for evt in evenements:
                if evt.type == "debut":
                    await self._gerer_parole_debut()
                elif evt.type == "fin" and evt.audio is not None:
                    await self._gerer_parole_fin(evt.audio)

            if parole_active and audio_partiel is not None and audio_partiel.size > 0:
                await self._planifier_partiel_si_pret(audio_partiel)
        except Exception as exc:
            logger.exception("Erreur lors du traitement d'une trame audio")
            await self._envoyer_json({"type": "erreur", "message": str(exc), "code": None})

    async def _gerer_parole_debut(self) -> None:
        """Gère le passage en parole détectée et déclenche le barge-in si une réponse joue."""
        etait_en_reponse = (
            self._reponse_audio_en_cours
            or self._tour_llm_actif
            or self._tour_rapide_actif
            or not self._file_tts.empty()
        )
        if etait_en_reponse:
            await self._annuler_reponse_audio(envoyer_stop=True)

        self._dernier_partiel = ""
        self._dernier_temps_partiel = 0.0
        await self._envoyer_json({"type": "parole_debut"})
        await self._diffuser_etat("ecoute")

    async def _planifier_partiel_si_pret(self, audio: np.ndarray) -> None:
        """Lance une transcription partielle rapide dans un thread si aucune n'est active."""
        if self._tache_partiel is not None and not self._tache_partiel.done():
            return
        maintenant = time.monotonic()
        if (
            self._dernier_temps_partiel > 0.0
            and (maintenant - self._dernier_temps_partiel) < self._intervalle_partiel_s
        ):
            return

        self._dernier_temps_partiel = maintenant
        generation = self._generation_enonce
        self._tache_partiel = asyncio.create_task(self._executer_partiel(audio, generation))

    async def _executer_partiel(self, audio: np.ndarray, generation: int) -> None:
        """Exécute la transcription partielle rapide et l'envoie si l'énoncé est toujours actif."""
        try:
            texte = (await asyncio.to_thread(stt.transcrire_tableau, audio, True)).strip()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Erreur transcription partielle : %s", exc)
            return

        if (
            self._fermee
            or generation != self._generation_enonce
            or not self._detecteur.parole_active
            or not texte
            or texte == self._dernier_partiel
        ):
            return

        self._dernier_partiel = texte
        await self._envoyer_json({"type": "partiel", "texte": texte})

    async def _gerer_parole_fin(self, audio: np.ndarray) -> None:
        """Finalise l'énoncé détecté, transcrit en haute précision et démarre le tour LLM."""
        self._generation_enonce += 1
        if self._tache_partiel is not None and not self._tache_partiel.done():
            self._tache_partiel.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._tache_partiel
        self._tache_partiel = None

        await self._diffuser_etat("reflexion")
        try:
            texte = (await asyncio.to_thread(stt.transcrire_tableau, audio, False)).strip()
        except Exception as exc:
            logger.exception("Erreur lors de la transcription finale")
            await self._envoyer_json({"type": "erreur", "message": str(exc), "code": None})
            await self._diffuser_etat("ecoute")
            return

        if not texte:
            await self._envoyer_json({"type": "rien"})
            await self._diffuser_etat("ecoute")
            return

        await self._envoyer_json({"type": "final", "texte": texte})
        if self._voie_rapide_active():
            await self._demarrer_tour_rapide(texte)
        else:
            await self._demarrer_tour_chat(texte)

    async def _enregistrer_tour_rapide_db(
        self,
        question: str,
        reponse: str,
        modele: str,
    ) -> None:
        """Persiste un échange de voie rapide dans la conversation ``agent:<agent_id>``."""
        conv_id = f"agent:{self.agent_id}"
        horodatage = db.maintenant_iso()
        meta_json = json.dumps(
            {
                "moteur": "agy",
                "modele": modele,
                "voie": "rapide",
            },
            ensure_ascii=False,
        )
        try:
            await db.async_execute(
                """
                INSERT OR IGNORE INTO conversations (id, type, cible_id, titre, cree_le)
                VALUES (?, 'agent', ?, ?, ?)
                """,
                (conv_id, self.agent_id, self.agent_id, horodatage),
            )
            await db.async_execute(
                """
                INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
                VALUES (?, 'user', 'utilisateur', ?, '{}', ?)
                """,
                (conv_id, question, horodatage),
            )
            await db.async_execute(
                """
                INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
                VALUES (?, 'assistant', ?, ?, ?, ?)
                """,
                (conv_id, self.agent_id, reponse, meta_json, db.maintenant_iso()),
            )
        except Exception as exc:
            logger.warning("Impossible d'enregistrer le tour vocal rapide dans SQLite : %s", exc)

    async def _demarrer_tour_rapide(self, texte: str) -> None:
        """Démarre un tour vocal via la voie rapide ``SessionAgyChaude``."""
        etait_en_reponse = (
            self._reponse_audio_en_cours
            or self._tour_llm_actif
            or self._tour_rapide_actif
            or not self._file_tts.empty()
        )
        await self._annuler_reponse_audio(envoyer_stop=etait_en_reponse)

        self._turn_id += 1
        turn_id = self._turn_id
        self._reponse_ignoree = False
        self._tour_rapide_actif = True
        self._seq = 0
        self._decoupeur.reinitialiser()

        self._tache_rapide = asyncio.create_task(self._executer_tour_rapide(turn_id, texte))

    async def _executer_tour_rapide(self, turn_id: int, texte: str) -> None:
        """Streame la réponse ``agy`` chaude, détecte ``[[AGIR]]`` et gère le repli éventuel."""
        if self._tache_prechauffe_agy is not None and not self._tache_prechauffe_agy.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._tache_prechauffe_agy

        if self._fermee or turn_id != self._turn_id or self._reponse_ignoree:
            return

        if self._voie_rapide_bloquee:
            self._tour_rapide_actif = False
            await self._demarrer_tour_chat(texte)
            return

        if self._session_agy is None:
            self._session_agy = self._creer_session_rapide()

        session_agy = self._session_agy
        phrases_envoyees = False
        tampon_brut: list[str] = []
        tampon_attente = ""
        etat_prefixe: Literal["indetermine", "direct", "agir"] = "indetermine"

        try:
            if not session_agy.est_active:
                await session_agy.demarrer()

            if session_agy.tours_sur_processus == 0:
                prompt_envoi = await asyncio.to_thread(_construire_premier_message_rapide, texte)
            else:
                prompt_envoi = texte

            async for delta in session_agy.envoyer(prompt_envoi):
                if not delta:
                    continue
                tampon_brut.append(delta)

                if self._fermee or turn_id != self._turn_id or self._reponse_ignoree:
                    # Vide le tour côté processus pour garder le flux NDJSON synchronisé.
                    continue

                if etat_prefixe == "indetermine":
                    tampon_attente += delta
                    stripped = tampon_attente.lstrip()
                    if stripped.startswith(MARQUEUR_AGIR):
                        etat_prefixe = "agir"
                        tampon_attente = ""
                    elif stripped and not MARQUEUR_AGIR.startswith(stripped):
                        etat_prefixe = "direct"
                        await self._envoyer_json({"type": "reponse_delta", "texte": tampon_attente})
                        for phrase in self._decoupeur.alimenter(tampon_attente):
                            phrases_envoyees = True
                            self._file_tts.put_nowait((turn_id, phrase))
                        tampon_attente = ""
                elif etat_prefixe == "direct":
                    await self._envoyer_json({"type": "reponse_delta", "texte": delta})
                    for phrase in self._decoupeur.alimenter(delta):
                        phrases_envoyees = True
                        self._file_tts.put_nowait((turn_id, phrase))

            if self._fermee or turn_id != self._turn_id or self._reponse_ignoree:
                return

            if etat_prefixe == "indetermine" and tampon_attente:
                stripped = tampon_attente.lstrip()
                if stripped.startswith(MARQUEUR_AGIR):
                    etat_prefixe = "agir"
                else:
                    etat_prefixe = "direct"
                    await self._envoyer_json({"type": "reponse_delta", "texte": tampon_attente})
                    for phrase in self._decoupeur.alimenter(tampon_attente):
                        phrases_envoyees = True
                        self._file_tts.put_nowait((turn_id, phrase))
                tampon_attente = ""

            reponse_complete = "".join(tampon_brut).strip()

            if etat_prefixe == "agir":
                apres_marqueur = reponse_complete.lstrip()[len(MARQUEUR_AGIR) :].strip()
                consigne = apres_marqueur.splitlines()[0].strip() if apres_marqueur else ""
                if not consigne:
                    consigne = texte

                self._tour_rapide_actif = False
                self._file_tts.put_nowait((turn_id, PHRASE_ACTION_EN_COURS))
                self._tour_llm_actif = True
                self._deltas_recus_pour_tour = False
                self._decoupeur.reinitialiser()
                try:
                    await chat.demarrer_chat(
                        ChatRequest(cible="agent", id=self.agent_id, message=consigne)
                    )
                except Exception as exc:
                    logger.exception("Impossible de démarrer le pipeline complet après [[AGIR]]")
                    self._tour_llm_actif = False
                    await self._annuler_reponse_audio(envoyer_stop=False)
                    await self._envoyer_json(
                        {
                            "type": "erreur",
                            "message": str(exc),
                            "code": getattr(exc, "code", None),
                        }
                    )
                    await self._diffuser_etat("ecoute")
                return

            for phrase in self._decoupeur.terminer():
                phrases_envoyees = True
                self._file_tts.put_nowait((turn_id, phrase))

            # Persiste le tour en base avant la sentinelle de fin TTS (reponse_fin), sinon une
            # déconnexion immédiate du client annule _tache_rapide avant la fin des écritures SQLite.
            if reponse_complete:
                await self._enregistrer_tour_rapide_db(
                    texte,
                    reponse_complete,
                    session_agy.modele,
                )

            if not self._fermee and turn_id == self._turn_id and not self._reponse_ignoree:
                self._file_tts.put_nowait((turn_id, None))
            self._tour_rapide_actif = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Voie rapide agy indisponible (%s), bascule sur le pipeline complet.", exc)
            self._tour_rapide_actif = False
            self._voie_rapide_bloquee = True
            with contextlib.suppress(Exception):
                await session_agy.arreter()

            if self._fermee or turn_id != self._turn_id or self._reponse_ignoree:
                return

            if phrases_envoyees:
                await self._annuler_reponse_audio(envoyer_stop=True)
            await self._demarrer_tour_chat(texte)
        finally:
            if turn_id == self._turn_id:
                self._tour_rapide_actif = False

    async def _demarrer_tour_chat(self, texte: str) -> None:
        """Démarre un tour de conversation via ``demarrer_chat`` pour ``agent:<agent_id>``."""
        etait_en_reponse = (
            self._reponse_audio_en_cours
            or self._tour_llm_actif
            or self._tour_rapide_actif
            or not self._file_tts.empty()
        )
        await self._annuler_reponse_audio(envoyer_stop=etait_en_reponse)

        self._turn_id += 1
        self._reponse_ignoree = False
        self._tour_llm_actif = True
        self._deltas_recus_pour_tour = False
        self._seq = 0
        self._decoupeur.reinitialiser()

        try:
            await chat.demarrer_chat(ChatRequest(cible="agent", id=self.agent_id, message=texte))
        except Exception as exc:
            logger.exception("Impossible de démarrer le tour de chat vocal")
            self._tour_llm_actif = False
            await self._envoyer_json(
                {
                    "type": "erreur",
                    "message": str(exc),
                    "code": getattr(exc, "code", None),
                }
            )
            await self._diffuser_etat("ecoute")

    async def _boucle_evenements_hub(self) -> None:
        """Consomme les événements ``chat_*`` du hub pour l'agent actif."""
        while not self._fermee:
            msg_type, data = await self._file_hub.get()
            try:
                if not self._tour_llm_actif or self._reponse_ignoree:
                    continue

                if msg_type == "chat_delta":
                    delta = str(data.get("delta") or "")
                    if not delta:
                        continue
                    self._deltas_recus_pour_tour = True
                    await self._envoyer_json({"type": "reponse_delta", "texte": delta})
                    for phrase in self._decoupeur.alimenter(delta):
                        self._file_tts.put_nowait((self._turn_id, phrase))

                elif msg_type == "chat_fin":
                    if not self._deltas_recus_pour_tour and data.get("contenu"):
                        contenu = str(data.get("contenu") or "")
                        if contenu:
                            await self._envoyer_json({"type": "reponse_delta", "texte": contenu})
                            for phrase in self._decoupeur.alimenter(contenu):
                                self._file_tts.put_nowait((self._turn_id, phrase))
                    for phrase in self._decoupeur.terminer():
                        self._file_tts.put_nowait((self._turn_id, phrase))
                    self._file_tts.put_nowait((self._turn_id, None))
                    self._tour_llm_actif = False

                elif msg_type == "chat_erreur":
                    message_err = str(data.get("erreur") or "Erreur du moteur de chat.")
                    code_err = data.get("code")
                    await self._annuler_reponse_audio(envoyer_stop=False)
                    await self._envoyer_json(
                        {
                            "type": "erreur",
                            "message": message_err,
                            "code": str(code_err) if code_err is not None else None,
                        }
                    )
                    await self._diffuser_etat("ecoute")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Erreur lors du traitement d'un événement hub vocal")
                await self._envoyer_json({"type": "erreur", "message": str(exc), "code": None})
            finally:
                self._file_hub.task_done()

    async def _boucle_tts(self) -> None:
        """Synthétise et envoie les phrases dans l'ordre strict pour chaque tour."""
        while not self._fermee:
            turn_id, phrase = await self._file_tts.get()
            try:
                if turn_id != self._turn_id or self._reponse_ignoree:
                    continue

                if phrase is None:
                    self._reponse_audio_en_cours = False
                    await self._envoyer_json({"type": "reponse_fin"})
                    await self._diffuser_etat("ecoute")
                    continue

                self._reponse_audio_en_cours = True
                await self._diffuser_etat("parole")

                voix_active = self._resoudre_voix()
                tache_synth = asyncio.create_task(tts.synthetiser(phrase, voix=voix_active))
                self._tache_synth = tache_synth
                try:
                    chemin = await tache_synth
                except asyncio.CancelledError:
                    if self._fermee:
                        raise
                    continue
                finally:
                    if self._tache_synth is tache_synth:
                        self._tache_synth = None

                if turn_id != self._turn_id or self._reponse_ignoree:
                    continue

                octets = await asyncio.to_thread(chemin.read_bytes)
                mime = "audio/wav" if chemin.suffix.lower() == ".wav" else "audio/mpeg"
                await self._envoyer_audio_phrase(turn_id, mime, phrase, octets)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Erreur lors de la synthèse vocale en streaming")
                await self._envoyer_json(
                    {
                        "type": "erreur",
                        "message": str(exc),
                        "code": getattr(exc, "code", None),
                    }
                )
            finally:
                self._file_tts.task_done()
