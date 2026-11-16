"""Scanner statique de secrets et données personnelles avant publication ou packaging."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DOSSIERS_EXCLUS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "build",
        "dist-release",
        "runtime",
        ".agent-runs",
        "secrets",
    }
)

FICHIERS_EXCLUS: frozenset[str] = frozenset(
    {
        "distrib/scan_secrets.py",
        "backend/tests/test_scan_secrets.py",
    }
)

EXTENSIONS_BINAIRES: frozenset[str] = frozenset(
    {
        ".exe",
        ".dll",
        ".pyd",
        ".so",
        ".dylib",
        ".ico",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".zip",
        ".7z",
        ".tar",
        ".gz",
        ".db",
        ".db-journal",
        ".sqlite",
        ".onnx",
        ".bin",
        ".pyc",
        ".pyo",
        ".pdf",
        ".wav",
        ".mp3",
    }
)

DOMAINES_EMAIL_GENERIQUES: frozenset[str] = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "localhost",
        "invalid",
    }
)

TLD_EXTENSIONS_ASSETS: frozenset[str] = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "webp",
        "gif",
        "svg",
        "ico",
        "avif",
        "css",
        "js",
        "ts",
        "tsx",
        "py",
        "ps1",
        "md",
        "json",
        "map",
        "woff",
        "woff2",
    }
)

UTILISATEURS_WINDOWS_GENERIQUES: frozenset[str] = frozenset(
    {
        "<nom>",
        "<user>",
        "<utilisateur>",
        "user",
        "utilisateur",
        "public",
        "default",
        "all users",
        "runneradmin",
    }
)

MOTIF_MOT_CLE_INTERDIT: re.Pattern[str] = re.compile(
    rf"\b{re.escape('mi' + 'mia')}\b",
    re.IGNORECASE,
)

MOTIFS_SECRETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Clé API Google (AIza...)", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("Clé secrète API (sk-...)", re.compile(r"\bsk-(?:proj-|ant-[A-Za-z0-9-]+)?[A-Za-z0-9]{20,}")),
    ("Jeton GitHub (ghp_...)", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("Jeton Slack (xox...)", re.compile(r"\bxox[bap]-")),
    ("Clé privée PEM", re.compile(r"-----BEGIN .*PRIVATE KEY-----")),
)

MOTIF_EMAIL: re.Pattern[str] = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.([A-Za-z]{2,}))\b")
MOTIF_CHEMIN_USERS: re.Pattern[str] = re.compile(
    r"[A-Za-z]:[/\\]+Users[/\\]+([^/\\\s\"'`<>|]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AlerteSecret:
    """Représente une détection de secret ou donnée personnelle dans un fichier."""

    fichier: str
    ligne: int
    regle: str


def _est_exclu(chemin: Path, racine: Path) -> bool:
    """Indique si un chemin doit être ignoré d'après les listes d'exclusions."""
    try:
        rel_path = chemin.relative_to(racine)
    except ValueError:
        return chemin.name in {"scan_secrets.py", "test_scan_secrets.py"} or (
            chemin.suffix.lower() in EXTENSIONS_BINAIRES
        )

    relatif = rel_path.as_posix()
    if relatif in FICHIERS_EXCLUS or chemin.name in {"scan_secrets.py", "test_scan_secrets.py"}:
        return True

    if any(part in DOSSIERS_EXCLUS for part in rel_path.parts[:-1]):
        return True

    return chemin.suffix.lower() in EXTENSIONS_BINAIRES


def scanner_ligne(ligne: str, numero_ligne: int, fichier: str) -> list[AlerteSecret]:
    """Analyse une ligne de texte et retourne les alertes détectées sans exposer le secret.

    Args:
        ligne: Contenu brut d'une ligne de fichier texte.
        numero_ligne: Numéro de ligne base 1.
        fichier: Chemin relatif lisible du fichier inspecté.

    Returns:
        Liste des ``AlerteSecret`` trouvées sur la ligne.
    """
    alertes: list[AlerteSecret] = []

    for libelle, motif in MOTIFS_SECRETS:
        if motif.search(ligne):
            alertes.append(AlerteSecret(fichier=fichier, ligne=numero_ligne, regle=libelle))

    if MOTIF_MOT_CLE_INTERDIT.search(ligne):
        alertes.append(AlerteSecret(fichier=fichier, ligne=numero_ligne, regle="Identifiant personnel interdit"))

    for match in MOTIF_EMAIL.finditer(ligne):
        domaine = match.group(1).lower()
        tld = match.group(2).lower()
        if domaine in DOMAINES_EMAIL_GENERIQUES or tld in TLD_EXTENSIONS_ASSETS:
            continue
        alertes.append(AlerteSecret(fichier=fichier, ligne=numero_ligne, regle="Adresse e-mail réelle"))

    for match in MOTIF_CHEMIN_USERS.finditer(ligne):
        utilisateur = match.group(1).strip().lower()
        if utilisateur in UTILISATEURS_WINDOWS_GENERIQUES:
            continue
        alertes.append(AlerteSecret(fichier=fichier, ligne=numero_ligne, regle="Chemin personnel C:\\Users\\<nom>"))

    return alertes


def scanner_fichier(chemin: Path, racine: Path) -> list[AlerteSecret]:
    """Lit un fichier UTF-8 et scanne toutes ses lignes.

    Args:
        chemin: Chemin du fichier à inspecter.
        racine: Dossier racine de référence pour l'affichage relatif.

    Returns:
        Liste des alertes détectées dans ce fichier (vide si binaire non UTF-8).
    """
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        relatif = chemin.relative_to(racine).as_posix()
    except ValueError:
        relatif = str(chemin)

    alertes: list[AlerteSecret] = []
    for idx, ligne in enumerate(contenu.splitlines(), start=1):
        alertes.extend(scanner_ligne(ligne, idx, relatif))
    return alertes


def scanner_dossier(cible: Path) -> list[AlerteSecret]:
    """Parcourt récursivement un dossier (ou fichier) et retourne toutes les alertes.

    Args:
        cible: Dossier ou fichier à analyser.

    Returns:
        Liste triée des ``AlerteSecret`` détectées.
    """
    cible_resolue = cible.resolve()
    if cible_resolue.is_file():
        return scanner_fichier(cible_resolue, cible_resolue.parent)

    alertes: list[AlerteSecret] = []
    for chemin in sorted(cible_resolue.rglob("*")):
        if not chemin.is_file() or _est_exclu(chemin, cible_resolue):
            continue
        alertes.extend(scanner_fichier(chemin, cible_resolue))
    return alertes


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée CLI du scanner de secrets.

    Args:
        argv: Arguments de ligne de commande optionnels.

    Returns:
        Code de sortie : ``0`` si aucune anomalie, ``1`` si au moins un secret/donnée personnelle
        est détecté ou si le chemin cible est introuvable.
    """
    parseur = argparse.ArgumentParser(
        description="Vérifie l'absence de secrets et de données personnelles avant publication.",
    )
    parseur.add_argument(
        "cible",
        nargs="?",
        default=".",
        help="Dossier ou fichier à analyser (par défaut : dossier courant).",
    )
    args = parseur.parse_args(argv)

    cible = Path(args.cible)
    if not cible.exists():
        print(f"Erreur : chemin introuvable : {cible}", file=sys.stderr)
        return 1

    alertes = scanner_dossier(cible)
    if not alertes:
        print(f"Scan propre : aucun secret ni donnée personnelle dans {cible}.")
        return 0

    print(f"Échec du scan ({len(alertes)} alerte(s)) :", file=sys.stderr)
    for alerte in alertes:
        print(f"  - {alerte.fichier}:{alerte.ligne}: {alerte.regle}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
