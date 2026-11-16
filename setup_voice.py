"""Assistant console de configuration vocale au premier démarrage de Jarvis AIOS."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from backend import config, settings_store
from backend.voice import stt


def resoudre_dossier_donnees(app_dir: Path | None = None) -> Path:
    """Résout le dossier des données utilisateur selon la logique de ``backend.config``.

    Args:
        app_dir: Dossier applicatif de référence optionnel.

    Returns:
        Chemin ``Path`` du dossier racine des données (contenant ``runtime/``).
    """
    return config.resoudre_root(app_dir)


def defauts_voix() -> dict[str, Any]:
    """Construit le bloc ``voix`` par défaut adapté au matériel GPU/CPU détecté.

    Returns:
        Dictionnaire complet prêt à être enregistré sous ``parametres.json["voix"]``.
    """
    cuda_ok = stt.cuda_disponible()
    stt_modele = "large-v3-turbo" if cuda_ok else "small"
    return {
        "active": True,
        "nom": "fr-FR-HenriNeural",
        "moteur_tts": "edge",
        "tts_url": None,
        "stt_modele": stt_modele,
        "stt_beam_final": 1 if stt_modele == "large-v3-turbo" else 5,
        "declenchement": "push",
        "micro": None,
        "rapide": True,
        "modele_rapide": "gemini-4-hydrogen-low",
    }


def precharger_modele_whisper(modele: str) -> bool:
    """Télécharge et met en cache le modèle Whisper choisi avec affichage de progression.

    Args:
        modele: Identifiant Whisper (``large-v3-turbo``, ``medium`` ou ``small``).

    Returns:
        ``True`` si le préchargement a réussi, ``False`` sinon.
    """
    print(f"\nPréchargement du modèle Whisper '{modele}' en cours…")
    try:
        try:
            from faster_whisper.utils import download_model

            download_model(modele)
        except ImportError:
            from faster_whisper import WhisperModel

            periph = "cuda" if stt.cuda_disponible() else "cpu"
            calcul = "float16" if periph == "cuda" else "int8"
            WhisperModel(modele, device=periph, compute_type=calcul)
        print(f"Modèle '{modele}' prêt dans le cache local.")
        return True
    except Exception as exc:
        print(f"Avertissement : impossible de précharger '{modele}' ({exc}).")
        print("Il sera téléchargé automatiquement lors de la première utilisation vocale.")
        return False


def enregistrer_configuration_voix(
    bloc_voix: dict[str, Any],
    dossier_donnees: Path | None = None,
) -> tuple[Path, Path]:
    """Persiste ``bloc_voix`` dans ``runtime/parametres.json`` et écrit ``voix_configuree.flag``.

    Args:
        bloc_voix: Sous-dictionnaire ``voix`` à fusionner dans ``parametres.json``.
        dossier_donnees: Dossier racine cible ; si ``None``, utilise ``resoudre_dossier_donnees()``.

    Returns:
        Tuple ``(chemin_parametres_json, chemin_flag)``.

    Effets de bord:
        Met à jour les chemins ``backend.config``, crée/modifie ``runtime/parametres.json``
        et écrit ``runtime/voix_configuree.flag``.
    """
    racine = resoudre_dossier_donnees() if dossier_donnees is None else Path(dossier_donnees)
    config.refresh_paths(racine)
    config.RUNTIME.mkdir(parents=True, exist_ok=True)

    settings_store.ecrire({"voix": bloc_voix})
    flag_path = config.RUNTIME / "voix_configuree.flag"
    flag_path.write_text("ok\n", encoding="utf-8")
    return config.SETTINGS_PATH, flag_path


def _demander_choix(
    invite: str,
    valides: set[str],
    defaut: str,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Lit un choix console parmi ``valides`` avec valeur par défaut sur Entrée."""
    while True:
        try:
            brut = input_fn(f"{invite} [{defaut}] : ").strip()
        except EOFError:
            return defaut
        if not brut:
            return defaut
        if brut in valides:
            return brut
        print(f"Choix invalide ({', '.join(sorted(valides))}).")


def executer_assistant(
    *,
    non_interactif: bool = False,
    precharger: bool = False,
    dossier_donnees: Path | None = None,
    input_fn: Callable[[str], str] = input,
) -> dict[str, Any]:
    """Exécute la configuration vocale (interactive ou ``--non-interactif``).

    Args:
        non_interactif: Applique les valeurs par défaut sans poser de questions.
        precharger: Force le préchargement du modèle Whisper choisi même en mode non interactif.
        dossier_donnees: Racine des données utilisateur (tests / surcharges).
        input_fn: Fonction de lecture clavier (injectable pour les tests).

    Returns:
        Dictionnaire ``voix`` enregistré.
    """
    bloc = defauts_voix()

    if non_interactif:
        if precharger:
            precharger_modele_whisper(str(bloc["stt_modele"]))
        enregistrer_configuration_voix(bloc, dossier_donnees=dossier_donnees)
        return bloc

    print("=== Configuration vocale de Jarvis AIOS ===\n")

    print("1. Mode vocal :")
    print("  [1] Pont web (non disponible pour l'instant)")
    print("  [2] Pipeline local sur cet ordinateur (défaut)")
    mode = _demander_choix("Votre choix", {"1", "2"}, "2", input_fn=input_fn)
    if mode == "1":
        print("-> Le mode Pont web n'est pas encore disponible ; utilisation du Pipeline local.\n")
    else:
        print("-> Pipeline local sélectionné.\n")

    cuda_ok = stt.cuda_disponible()
    defaut_whisper = "1" if cuda_ok else "3"
    print("2. Modèle de transcription Whisper :")
    print(
        f"  [1] large-v3-turbo (~1,6 Go) — très précis, recommandé avec GPU CUDA"
        f"{' (défaut)' if cuda_ok else ''}"
    )
    print("  [2] medium (~1,5 Go) — haute précision, plus lourd sur CPU")
    print(
        f"  [3] small (~500 Mo) — rapide et léger sur CPU"
        f"{' (défaut)' if not cuda_ok else ''}"
    )
    choix_stt = _demander_choix("Votre choix", {"1", "2", "3"}, defaut_whisper, input_fn=input_fn)
    carte_stt = {"1": "large-v3-turbo", "2": "medium", "3": "small"}
    bloc["stt_modele"] = carte_stt[choix_stt]
    bloc["stt_beam_final"] = 1 if bloc["stt_modele"] == "large-v3-turbo" else 5
    print()

    print("3. Voix de synthèse :")
    print("  [1] Henri — fr-FR-HenriNeural (Edge-TTS, défaut)")
    print("  [2] Denise — fr-FR-DeniseNeural (Edge-TTS)")
    print("  [3] Voix locale Windows (hors ligne)")
    print("  [4] Serveur local HTTP (compatible OpenAI)")
    choix_voix = _demander_choix("Votre choix", {"1", "2", "3", "4"}, "1", input_fn=input_fn)
    if choix_voix == "1":
        bloc["moteur_tts"] = "edge"
        bloc["nom"] = "fr-FR-HenriNeural"
    elif choix_voix == "2":
        bloc["moteur_tts"] = "edge"
        bloc["nom"] = "fr-FR-DeniseNeural"
    elif choix_voix == "3":
        bloc["moteur_tts"] = "windows"
    else:
        bloc["moteur_tts"] = "http"
        try:
            url = input_fn("URL du serveur TTS local [http://127.0.0.1:8880] : ").strip()
        except EOFError:
            url = ""
        bloc["tts_url"] = (url or "http://127.0.0.1:8880").rstrip("/")
    print()

    print("4. Mode de déclenchement :")
    print("  [1] Push-to-talk (Ctrl+Espace ou bouton micro, défaut)")
    print("  [2] Conversation continue (mains libres)")
    choix_decl = _demander_choix("Votre choix", {"1", "2"}, "1", input_fn=input_fn)
    bloc["declenchement"] = "push" if choix_decl == "1" else "continu"
    print()

    try:
        rep_dl = input_fn(
            f"Précharger le modèle Whisper '{bloc['stt_modele']}' maintenant ? [o/N] : "
        ).strip().lower()
    except EOFError:
        rep_dl = ""
    if rep_dl in ("o", "oui", "y", "yes"):
        precharger_modele_whisper(str(bloc["stt_modele"]))

    params_path, flag_path = enregistrer_configuration_voix(bloc, dossier_donnees=dossier_donnees)
    print(f"\nConfiguration enregistrée dans {params_path}")
    print(f"Marqueur écrit dans {flag_path}")
    return bloc


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée CLI de ``setup_voice.py``.

    Args:
        argv: Arguments de ligne de commande optionnels.

    Returns:
        Code de sortie ``0``.
    """
    parser = argparse.ArgumentParser(
        description="Assistant de configuration vocale au premier démarrage de Jarvis AIOS."
    )
    parser.add_argument(
        "--non-interactif",
        action="store_true",
        help="Applique la configuration vocale par défaut sans interaction (installeur).",
    )
    parser.add_argument(
        "--precharger",
        action="store_true",
        help="Précharge le modèle Whisper sélectionné dans le cache local.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    executer_assistant(
        non_interactif=bool(args.non_interactif),
        precharger=bool(args.precharger),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
