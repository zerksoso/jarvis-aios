"""Validation et résolution sécurisée des chemins de notes sous ``BRAIN_DIR``."""

from pathlib import Path, PurePosixPath

from backend import config


def normaliser_id_note(note_id: str) -> str:
    """Normalise un identifiant de note en chemin POSIX relatif sans extension ``.md``.

    Refuse tout chemin vide, absolu, avec lecteur Windows ou traversée ``..``.

    Args:
        note_id: Identifiant brut fourni par l'API ou un outil.

    Returns:
        Identifiant relatif POSIX nettoyé sans ``.md``.

    Raises:
        ValueError: Si l'identifiant est vide, absolu ou tente de sortir du coffre.
    """
    if not isinstance(note_id, str):
        raise ValueError("Identifiant de note invalide.")

    brut = note_id.strip()
    if not brut or "\x00" in brut:
        raise ValueError("Identifiant de note vide ou invalide.")

    if ":" in brut or brut.startswith("/") or brut.startswith("\\"):
        raise ValueError("Les chemins absolus ou lecteurs sont interdits.")

    posix = brut.replace("\\", "/")
    if posix.lower().endswith(".md"):
        posix = posix[:-3]

    if not posix or posix.startswith("/"):
        raise ValueError("Identifiant de note invalide.")

    parties = PurePosixPath(posix).parts
    if not parties:
        raise ValueError("Identifiant de note invalide.")

    for part in parties:
        if part in ("", ".", "..") or ":" in part:
            raise ValueError(f"Segment de chemin interdit dans l'identifiant : {part!r}")

    return "/".join(parties)


def chemin_sur(note_id: str) -> Path:
    """Résout un identifiant de note vers son fichier ``.md`` sous ``BRAIN_DIR``.

    Garantit qu'aucune traversée de répertoire ni lien symbolique ne sort du coffre.

    Args:
        note_id: Identifiant relatif de note (avec ou sans ``.md``).

    Returns:
        Chemin absolu résolu du fichier ``.md`` à l'intérieur de ``BRAIN_DIR``.

    Raises:
        ValueError: Si l'identifiant est invalide ou sort de ``BRAIN_DIR``.
    """
    propre = normaliser_id_note(note_id)
    base = config.BRAIN_DIR.resolve()
    cible = (base / f"{propre}.md").resolve()

    if cible == base or not cible.is_relative_to(base):
        raise ValueError("Le chemin résolu sort du second cerveau.")
    return cible


def id_depuis_chemin(chemin: Path, brain_dir: Path | None = None) -> str:
    """Calcule l'identifiant POSIX sans ``.md`` à partir d'un chemin sous ``BRAIN_DIR``.

    Args:
        chemin: Chemin du fichier ``.md``.
        brain_dir: Racine du coffre (par défaut ``config.BRAIN_DIR``).

    Returns:
        Identifiant POSIX relatif sans extension ``.md``.

    Raises:
        ValueError: Si le chemin n'est pas situé dans ``brain_dir``.
    """
    base = (brain_dir or config.BRAIN_DIR).resolve()
    rel = chemin.resolve().relative_to(base)
    posix = rel.as_posix()
    if posix.lower().endswith(".md"):
        return posix[:-3]
    return posix
