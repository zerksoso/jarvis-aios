"""Gestion persistante des paramètres utilisateur dans ``RUNTIME/parametres.json``."""

import copy
import json
import logging
import threading
from typing import Any

from backend import config

logger = logging.getLogger(__name__)

DEFAUTS: dict[str, Any] = {
    "moteur": "auto",
    "modele": "hydrogen",
    "effort": "medium",
    "verbosite": "normal",
    "accent": "#22d3ee",
    "voix": {
        "active": True,
        "nom": "fr-FR-HenriNeural",
        "moteur_tts": "edge",
        "tts_url": None,
        "stt_modele": "small",
        "stt_beam_final": 5,
        "declenchement": "push",
        "micro": None,
        "rapide": True,
        "modele_rapide": "gemini-4-hydrogen-low",
        "llm_rapide": "agy",
        "llm_local_url": "http://127.0.0.1:1234/v1",
        "llm_local_modele": "",
        "mode": "local",
        "web_service": "chatgpt",
        "web_navigateur": "defaut",
    },
    "raccourci": "ctrl+space",
    "memoire_auto": True,
    "maj": {
        "mode": "demander",
        "depot": "",
    },
}

_MOTEURS_TTS = ("edge", "windows", "http", "kyutai")
_MODELES_STT = ("large-v3-turbo", "medium", "small")
_DECLENCHEMENTS_VOIX = ("push", "continu")
_MODES_VOIX = ("local", "web")
_LLM_RAPIDES = ("agy", "local")
_WEB_SERVICES = ("chatgpt", "gemini")
_WEB_NAVIGATEURS = ("defaut", "chrome", "edge")

_lock = threading.Lock()


def _cuda_disponible_sur() -> bool:
    """Interroge prudemment la disponibilité CUDA sans créer d'import circulaire."""
    try:
        from backend.voice import stt

        return bool(stt.cuda_disponible())
    except Exception:
        return False


def _defauts_dynamiques() -> dict[str, Any]:
    """Construit une copie des défauts avec ``voix.stt_modele`` et ``stt_beam_final`` adaptés au GPU."""
    donnees = copy.deepcopy(DEFAUTS)
    if _cuda_disponible_sur():
        donnees["voix"]["stt_modele"] = "large-v3-turbo"
        donnees["voix"]["stt_beam_final"] = 1
    else:
        donnees["voix"]["stt_modele"] = "small"
        donnees["voix"]["stt_beam_final"] = 5
    return donnees


def _normaliser_bloc_voix(voix: dict[str, Any], defauts_voix: dict[str, Any]) -> dict[str, Any]:
    """Assainit les valeurs du sous-bloc ``voix`` tout en préservant les clés additionnelles."""
    res = copy.deepcopy(voix)

    res["active"] = bool(res.get("active", defauts_voix["active"]))

    nom = res.get("nom")
    res["nom"] = str(nom).strip() if isinstance(nom, str) and nom.strip() else defauts_voix["nom"]

    moteur_tts = str(res.get("moteur_tts") or "").strip().lower()
    res["moteur_tts"] = moteur_tts if moteur_tts in _MOTEURS_TTS else defauts_voix["moteur_tts"]

    tts_url = res.get("tts_url")
    res["tts_url"] = str(tts_url).strip() if isinstance(tts_url, str) and tts_url.strip() else None

    stt_modele = str(res.get("stt_modele") or "").strip()
    res["stt_modele"] = stt_modele if stt_modele in _MODELES_STT else defauts_voix["stt_modele"]

    beam = res.get("stt_beam_final")
    if isinstance(beam, int) and not isinstance(beam, bool) and 1 <= beam <= 10:
        res["stt_beam_final"] = beam
    else:
        res["stt_beam_final"] = 1 if res["stt_modele"] == "large-v3-turbo" else 5

    declenchement = str(res.get("declenchement") or "").strip().lower()
    res["declenchement"] = (
        declenchement if declenchement in _DECLENCHEMENTS_VOIX else defauts_voix["declenchement"]
    )

    micro = res.get("micro")
    res["micro"] = str(micro).strip() if isinstance(micro, str) and micro.strip() else None

    res["rapide"] = bool(res.get("rapide", defauts_voix["rapide"]))

    modele_rapide = res.get("modele_rapide")
    res["modele_rapide"] = (
        str(modele_rapide).strip()
        if isinstance(modele_rapide, str) and modele_rapide.strip()
        else defauts_voix["modele_rapide"]
    )

    llm_rapide = str(res.get("llm_rapide") or "").strip().lower()
    res["llm_rapide"] = llm_rapide if llm_rapide in _LLM_RAPIDES else defauts_voix["llm_rapide"]

    llm_url = res.get("llm_local_url")
    res["llm_local_url"] = (
        str(llm_url).strip().rstrip("/")
        if isinstance(llm_url, str) and llm_url.strip().startswith(("http://", "https://"))
        else defauts_voix["llm_local_url"]
    )
    llm_modele = res.get("llm_local_modele")
    res["llm_local_modele"] = str(llm_modele).strip() if isinstance(llm_modele, str) else ""

    mode = str(res.get("mode") or "").strip().lower()
    res["mode"] = mode if mode in _MODES_VOIX else defauts_voix["mode"]

    web_service = str(res.get("web_service") or "").strip().lower()
    res["web_service"] = web_service if web_service in _WEB_SERVICES else defauts_voix["web_service"]

    web_navigateur = str(res.get("web_navigateur") or "").strip().lower()
    res["web_navigateur"] = (
        web_navigateur if web_navigateur in _WEB_NAVIGATEURS else defauts_voix["web_navigateur"]
    )
    return res


def _fusion_profonde(base: dict[str, Any], maj: dict[str, Any]) -> dict[str, Any]:
    """Fusionne récursivement ``maj`` dans une copie profonde de ``base``.

    Args:
        base: Dictionnaire de référence.
        maj: Modifications partielles à appliquer.

    Returns:
        Nouveau dictionnaire résultant de la fusion profonde.
    """
    resultat = copy.deepcopy(base)
    for cle, valeur in maj.items():
        if isinstance(valeur, dict) and isinstance(resultat.get(cle), dict):
            resultat[cle] = _fusion_profonde(resultat[cle], valeur)
        else:
            resultat[cle] = copy.deepcopy(valeur)
    return resultat


def lire() -> dict[str, Any]:
    """Lit les paramètres utilisateur, en créant le fichier par défaut s'il est absent.

    Complète automatiquement toute clé manquante à partir des défauts dynamiques.

    Returns:
        Dictionnaire complet des paramètres courants.

    Effets de bord:
        Crée ``RUNTIME/parametres.json`` au premier appel s'il n'existe pas.
    """
    with _lock:
        defauts = _defauts_dynamiques()
        chemin = config.SETTINGS_PATH
        if not chemin.exists():
            chemin.parent.mkdir(parents=True, exist_ok=True)
            chemin.write_text(json.dumps(defauts, indent=2, ensure_ascii=False), encoding="utf-8")
            return defauts

        try:
            brut = json.loads(chemin.read_text(encoding="utf-8"))
            if not isinstance(brut, dict):
                raise ValueError("Le fichier de paramètres doit contenir un objet JSON.")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Paramètres illisibles (%s), retour aux valeurs par défaut.", exc)
            return defauts

        fusionne = _fusion_profonde(defauts, brut)
        # Si stt_modele est fixé par l'utilisateur sans stt_beam_final, adapte le beam par défaut.
        voix_brut = brut.get("voix") if isinstance(brut.get("voix"), dict) else {}
        if "stt_modele" in voix_brut and "stt_beam_final" not in voix_brut:
            fusionne["voix"].pop("stt_beam_final", None)
        if isinstance(fusionne.get("voix"), dict):
            fusionne["voix"] = _normaliser_bloc_voix(fusionne["voix"], defauts["voix"])
        return fusionne


def ecrire(partiel: dict[str, Any]) -> dict[str, Any]:
    """Met à jour les paramètres par fusion profonde et persiste le résultat sur disque.

    Args:
        partiel: Dictionnaire partiel des clés à modifier.

    Returns:
        Dictionnaire complet des paramètres après mise à jour.

    Effets de bord:
        Lit et réécrit ``RUNTIME/parametres.json`` sous verrou thread.
    """
    with _lock:
        defauts = _defauts_dynamiques()
        chemin = config.SETTINGS_PATH
        chemin.parent.mkdir(parents=True, exist_ok=True)

        courant = copy.deepcopy(defauts)
        if chemin.exists():
            try:
                brut = json.loads(chemin.read_text(encoding="utf-8"))
                if isinstance(brut, dict):
                    courant = _fusion_profonde(defauts, brut)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("Paramètres existants corrompus (%s), réinitialisation avant fusion.", exc)

        mis_a_jour = _fusion_profonde(courant, partiel)
        if isinstance(mis_a_jour.get("voix"), dict):
            mis_a_jour["voix"] = _normaliser_bloc_voix(mis_a_jour["voix"], defauts["voix"])
        chemin.write_text(json.dumps(mis_a_jour, indent=2, ensure_ascii=False), encoding="utf-8")
        return mis_a_jour
