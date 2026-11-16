"""Prépare une copie propre du dépôt pour publication avec un historique Git vierge."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

RACINE_DEPOT: Path = Path(__file__).resolve().parents[1]
SCRIPT_SCAN_SECRETS: Path = Path(__file__).resolve().with_name("scan_secrets.py")

DOSSIERS_AUTORISES: tuple[str, ...] = (
    "backend",
    "overlay",
    "frontend",
    "scripts",
    "launcher",
    "distrib",
    "assets",
    ".github",
)

FICHIERS_AUTORISES: tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Lancer_Jarvis.bat",
    "Arreter_Jarvis.bat",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "ARCHITECTURE.md",
    "ARCHITECTURE-DOMAINES.md",
    "ARCHITECTURE-DISTRIB.md",
)

DOSSIERS_EXCLUS_PARTOUT: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".agent-runs",
        "build",
        "dist-release",
        "secrets",
        "memory",
    }
)

EXTENSIONS_EXCLUES_PARTOUT: frozenset[str] = frozenset({".pyc", ".db"})
EXCLUSIONS_FRONTEND: frozenset[str] = frozenset({"node_modules", "dist", ".vite"})
FICHIERS_EXCLUS_LAUNCHER: frozenset[str] = frozenset({"jarvis.exe"})
EXTENSIONS_EXCLUES_LAUNCHER: frozenset[str] = frozenset({".exe", ".pdb", ".ilk", ".obj"})
DOSSIERS_EXCLUS_LAUNCHER: frozenset[str] = frozenset({"bin", "obj"})

GIT_USER_NAME: str = "Jarvis AIOS"
GIT_USER_EMAIL: str = "noreply@" + "jarvis-aios.invalid"
GIT_COMMIT_MESSAGE: str = "Initial commit: Jarvis AIOS"


def _forcer_suppression_lecture_seule(
    fonction: Callable[[str], object],
    chemin: str,
    _exc: BaseException,
) -> None:
    """Retire l'attribut lecture seule (ex. objets Git sous Windows) avant réessai."""
    os.chmod(chemin, stat.S_IWRITE)
    fonction(chemin)


def preparer_dossier_sortie(racine: Path, sortie: Path, force: bool) -> Path:
    """Valide que ``sortie`` est dans ``racine/build/`` et prépare un dossier vide.

    Args:
        racine: Racine du dépôt source.
        sortie: Chemin cible, relatif à ``racine`` ou absolu.
        force: Si ``True``, supprime uniquement le contenu existant de ``sortie``.

    Returns:
        Chemin résolu du dossier de sortie prêt à recevoir les fichiers.

    Raises:
        ValueError: Si ``sortie`` pointe hors de ``racine/build/``, vise ``build/`` lui-même,
            correspond à un fichier existant, ou est non vide sans ``force=True``.

    Effets de bord:
        Crée ``sortie`` s'il n'existe pas, ou vide son contenu si ``force=True``.
    """
    racine_resolue = racine.resolve()
    dossier_build = (racine_resolue / "build").resolve()
    sortie_resolue = (sortie if sortie.is_absolute() else racine_resolue / sortie).resolve()

    if sortie_resolue == dossier_build or not sortie_resolue.is_relative_to(dossier_build):
        raise ValueError(
            f"Le dossier de sortie doit être un sous-dossier de {dossier_build} : {sortie_resolue}"
        )

    if sortie_resolue.exists():
        if not sortie_resolue.is_dir():
            raise ValueError(f"Le chemin de sortie existe et n'est pas un dossier : {sortie_resolue}")
        contenu = list(sortie_resolue.iterdir())
        if contenu:
            if not force:
                raise ValueError(
                    f"Le dossier de sortie n'est pas vide : {sortie_resolue}. "
                    "Ajoutez --force pour vider son contenu."
                )
            for enfant in contenu:
                if enfant.is_dir() and not enfant.is_symlink():
                    shutil.rmtree(enfant, onexc=_forcer_suppression_lecture_seule)
                else:
                    try:
                        enfant.unlink()
                    except PermissionError:
                        os.chmod(enfant, stat.S_IWRITE)
                        enfant.unlink()
    else:
        sortie_resolue.mkdir(parents=True, exist_ok=True)

    return sortie_resolue


def _est_exclu(chemin: Path, *, est_dossier: bool, dossier_racine: str) -> bool:
    """Indique si une entrée située dans un dossier autorisé doit être exclue."""
    nom = chemin.name
    nom_min = nom.lower()

    if est_dossier:
        if nom in DOSSIERS_EXCLUS_PARTOUT:
            return True
        if dossier_racine == "frontend" and nom in EXCLUSIONS_FRONTEND:
            return True
        if dossier_racine == "launcher" and nom_min in DOSSIERS_EXCLUS_LAUNCHER:
            return True
        return False

    if nom_min == ".env":
        return True
    if chemin.suffix.lower() in EXTENSIONS_EXCLUES_PARTOUT:
        return True
    if dossier_racine == "frontend" and nom in EXCLUSIONS_FRONTEND:
        return True
    if dossier_racine == "launcher" and (
        nom_min in FICHIERS_EXCLUS_LAUNCHER or chemin.suffix.lower() in EXTENSIONS_EXCLUES_LAUNCHER
    ):
        return True
    return False


def _copier_arborescence(source: Path, destination: Path, dossier_racine: str) -> None:
    """Copie récursivement ``source`` vers ``destination`` en filtrant les exclusions."""
    destination.mkdir(parents=True, exist_ok=True)
    for entree in sorted(source.iterdir(), key=lambda p: p.name):
        if entree.is_symlink():
            continue
        est_dossier = entree.is_dir()
        if _est_exclu(entree, est_dossier=est_dossier, dossier_racine=dossier_racine):
            continue
        cible = destination / entree.name
        if est_dossier:
            _copier_arborescence(entree, cible, dossier_racine)
        elif entree.is_file():
            shutil.copy2(entree, cible)


def copier_depot_propre(racine: Path, sortie: Path) -> tuple[int, int]:
    """Copie uniquement la liste blanche explicite depuis ``racine`` vers ``sortie``.

    Args:
        racine: Racine du dépôt source.
        sortie: Dossier cible vidé et validé.

    Returns:
        Tuple ``(nb_fichiers, taille_totale_octets)`` effectivement présents dans ``sortie``.

    Effets de bord:
        Copie les dossiers/fichiers autorisés, transforme ``README.GITHUB.md`` en ``README.md``
        et recopie ``.gitignore`` dans ``sortie``.
    """
    racine_resolue = racine.resolve()

    for dossier in DOSSIERS_AUTORISES:
        source_dir = racine_resolue / dossier
        if source_dir.is_dir() and not source_dir.is_symlink():
            _copier_arborescence(source_dir, sortie / dossier, dossier)

    for fichier in FICHIERS_AUTORISES:
        source_file = racine_resolue / fichier
        if source_file.is_file() and not source_file.is_symlink():
            shutil.copy2(source_file, sortie / fichier)

    readme_github = racine_resolue / "README.GITHUB.md"
    if readme_github.is_file() and not readme_github.is_symlink():
        shutil.copy2(readme_github, sortie / "README.md")

    gitignore_src = racine_resolue / ".gitignore"
    if gitignore_src.is_file() and not gitignore_src.is_symlink():
        (sortie / ".gitignore").write_bytes(gitignore_src.read_bytes())

    nb_fichiers = 0
    taille_totale = 0
    for chemin in sortie.rglob("*"):
        if chemin.is_file():
            nb_fichiers += 1
            taille_totale += chemin.stat().st_size

    return nb_fichiers, taille_totale


def executer_scan_secrets(sortie: Path, racine: Path) -> tuple[bool, str]:
    """Exécute ``distrib/scan_secrets.py`` en sous-processus sur le dossier exporté.

    Args:
        sortie: Dossier cible contenant la copie à vérifier.
        racine: Racine du dépôt source (prioritaire pour localiser ``scan_secrets.py``).

    Returns:
        Tuple ``(succes, sortie_texte)`` indiquant si le scan a retourné 0 et les messages produits.
    """
    candidat = racine.resolve() / "distrib" / "scan_secrets.py"
    script_scan = candidat if candidat.is_file() else SCRIPT_SCAN_SECRETS

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    resultat = subprocess.run(
        [sys.executable, str(script_scan), str(sortie)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if resultat.returncode != 0:
        detail = (resultat.stderr or resultat.stdout or "Échec du scan de secrets.").strip()
        return False, detail
    return True, (resultat.stdout or "").strip()


def _executer_git(args: list[str], cwd: Path, git_bin: str) -> str:
    """Exécute une commande Git dans ``cwd`` et lève ``RuntimeError`` en cas d'échec."""
    resultat = subprocess.run(
        [git_bin, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if resultat.returncode != 0:
        detail = (resultat.stderr or resultat.stdout or "").strip()
        raise RuntimeError(f"Échec git {' '.join(args)} (code {resultat.returncode}) : {detail}")
    return resultat.stdout.strip()


def initialiser_depot_git(sortie: Path) -> str:
    """Initialise un dépôt Git vierge sur ``main`` avec un unique commit local.

    Args:
        sortie: Dossier cible déjà vérifié par ``scan_secrets.py``.

    Returns:
        Empreinte SHA du commit initial créé.

    Raises:
        RuntimeError: Si ``git`` est absent ou si une commande Git échoue.

    Effets de bord:
        Crée ``.git/`` dans ``sortie``, configure ``user.name`` et ``user.email`` en local
        uniquement, indexe tous les fichiers et crée le commit initial sans configurer de remote.
    """
    git_bin = shutil.which("git")
    if not git_bin:
        raise RuntimeError("Exécutable 'git' introuvable dans le PATH.")

    _executer_git(["init", "-b", "main"], sortie, git_bin)
    _executer_git(["config", "--local", "user.name", GIT_USER_NAME], sortie, git_bin)
    _executer_git(["config", "--local", "user.email", GIT_USER_EMAIL], sortie, git_bin)
    _executer_git(["add", "-A"], sortie, git_bin)
    _executer_git(["commit", "--no-gpg-sign", "-m", GIT_COMMIT_MESSAGE], sortie, git_bin)
    return _executer_git(["rev-parse", "HEAD"], sortie, git_bin)


def _formater_taille(octets: int) -> str:
    """Formate une taille en octets de manière lisible en français."""
    if octets < 1024:
        return f"{octets} octets"
    if octets < 1024 * 1024:
        return f"{octets / 1024:.1f} Ko ({octets} octets)"
    return f"{octets / (1024 * 1024):.2f} Mo ({octets} octets)"


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI pour préparer un export Git propre prêt à publier.

    Args:
        argv: Arguments de ligne de commande optionnels.

    Returns:
        ``0`` si l'export, le scan de secrets et le commit initial ont réussi, ``1`` sinon.
    """
    parseur = argparse.ArgumentParser(
        description="Prépare une copie propre du dépôt avec historique Git vierge (sans push).",
    )
    parseur.add_argument(
        "--sortie",
        default="build/publish",
        help="Dossier de sortie à l'intérieur de build/ (par défaut : build/publish).",
    )
    parseur.add_argument(
        "--force",
        action="store_true",
        help="Vide le contenu du dossier de sortie s'il existe et n'est pas vide.",
    )
    parseur.add_argument(
        "--racine",
        default=None,
        help="Racine du dépôt source (par défaut : racine du projet courant).",
    )
    args = parseur.parse_args(argv)

    racine = Path(args.racine) if args.racine else RACINE_DEPOT
    if not racine.is_dir():
        print(f"Erreur : racine du dépôt introuvable : {racine}", file=sys.stderr)
        return 1

    try:
        sortie = preparer_dossier_sortie(racine=racine, sortie=Path(args.sortie), force=bool(args.force))
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    nb_fichiers, taille_totale = copier_depot_propre(racine=racine, sortie=sortie)
    if nb_fichiers == 0:
        print(f"Erreur : aucun fichier autorisé trouvé dans {racine}", file=sys.stderr)
        return 1

    scan_ok, detail_scan = executer_scan_secrets(sortie=sortie, racine=racine)
    if not scan_ok:
        print(detail_scan, file=sys.stderr)
        return 1

    try:
        commit_hash = initialiser_depot_git(sortie)
    except RuntimeError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    print(f"Export propre prêt dans : {sortie}")
    print(f"  - Fichiers exportés : {nb_fichiers}")
    print(f"  - Taille totale     : {_formater_taille(taille_totale)}")
    print(f"  - Commit initial    : {commit_hash}")
    print("Rappel : aucun dépôt distant (remote) configuré et rien n'a été poussé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
