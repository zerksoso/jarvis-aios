"""Découverte multi-chemins des binaires CLI externes (PATH et dossiers Windows connus)."""

import os
import shutil
import sys
from pathlib import Path

BINAIRES_CONNUS: tuple[str, ...] = ("agy", "claude", "codex", "node", "npm", "git")


def dossiers_connus() -> list[Path]:
    """Retourne les dossiers d'installation Windows connus pour les CLI et Node.js.

    Lit les variables d'environnement à chaque appel (``LOCALAPPDATA``, ``APPDATA``,
    ``USERPROFILE``, ``ProgramFiles``) afin de rester compatible avec les tests isolés.

    Returns:
        Liste ordonnée et dédupliquée de chemins ``Path`` candidats.
    """
    local_app = (os.environ.get("LOCALAPPDATA") or "").strip()
    app_data = (os.environ.get("APPDATA") or "").strip()
    userprofile = (os.environ.get("USERPROFILE") or "").strip()
    program_files = (os.environ.get("ProgramFiles") or os.environ.get("PROGRAMFILES") or "").strip()

    bruts: list[Path] = []
    if local_app:
        bruts.append(Path(local_app) / "agy" / "bin")
        bruts.append(Path(local_app) / "Programs" / "Antigravity" / "bin")

    base_home = Path(userprofile) if userprofile else Path.home()
    bruts.append(base_home / ".local" / "bin")

    if app_data:
        bruts.append(Path(app_data) / "npm")
    if local_app:
        bruts.append(Path(local_app) / "Programs" / "nodejs")
    if program_files:
        bruts.append(Path(program_files) / "nodejs")

    uniques: list[Path] = []
    vus: set[str] = set()
    for dossier in bruts:
        cle = os.path.normcase(str(dossier))
        if cle not in vus:
            vus.add(cle)
            uniques.append(dossier)
    return uniques


def trouver(nom: str) -> str | None:
    """Localise un binaire CLI via ``PATH`` puis dans les dossiers Windows connus.

    Cherche d'abord via ``shutil.which`` (avec ``.exe`` et ``.cmd`` sous Windows),
    puis inspecte les emplacements standards (Antigravity, ``.local\\bin``, ``npm``,
    ``nodejs``) pour les binaires ``agy``, ``claude``, ``codex``, ``node``, ``npm``, ``git``.

    Args:
        nom: Nom du binaire recherché (ex. ``"agy"``, ``"claude"``, ``"codex"``, ``"node"``).

    Returns:
        Chemin complet vers l'exécutable trouvé, ou ``None`` s'il est absent.
    """
    propre = (nom or "").strip()
    if not propre:
        return None

    a_extension = Path(propre).suffix.lower() in {".exe", ".cmd", ".bat"}
    noms_which = (propre,) if a_extension else (propre, f"{propre}.exe", f"{propre}.cmd")
    noms_fichiers = (propre,) if a_extension else (f"{propre}.exe", f"{propre}.cmd", propre)

    for candidat_which in noms_which:
        trouve = shutil.which(candidat_which)
        if trouve:
            chemin = Path(trouve)
            if sys.platform == "win32" and not chemin.suffix:
                for ext in (".exe", ".cmd"):
                    alt = chemin.with_name(chemin.name + ext)
                    if alt.is_file():
                        return str(alt)
            return trouve

    for dossier in dossiers_connus():
        if not dossier.is_dir():
            continue
        for nom_fichier in noms_fichiers:
            cible = dossier / nom_fichier
            if cible.is_file():
                return str(cible)

    return None
