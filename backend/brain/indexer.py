"""Indexation SQLite + FTS5 et résolution des liens wiki du second cerveau."""

import json
import logging
from pathlib import Path, PurePosixPath
from typing import Any

from backend import config, db
from backend.brain.parser import lire_note, normaliser_texte
from backend.brain.paths import id_depuis_chemin

logger = logging.getLogger(__name__)

_DOSSIERS_IGNORES = {".obsidian", ".trash", ".git"}


def est_chemin_ignore(chemin: Path, brain_dir: Path | None = None) -> bool:
    """Indique si un chemin sous ``BRAIN_DIR`` doit être ignoré par l'indexeur.

    Ignore les dossiers ``.obsidian/``, ``.trash/``, ``.git/``, les fichiers temporaires
    (``~*``, ``*.tmp``, ``*.swp``, ``.*``) et tout fichier non ``.md``.

    Args:
        chemin: Chemin du fichier candidat.
        brain_dir: Racine du coffre (par défaut ``config.BRAIN_DIR``).

    Returns:
        ``True`` si le fichier doit être ignoré.
    """
    nom = chemin.name
    if not nom or nom.startswith(("~", ".")) or nom.endswith((".tmp", ".swp", "~")):
        return True
    if chemin.suffix.lower() != ".md":
        return True

    base = (brain_dir or config.BRAIN_DIR).resolve()
    try:
        rel = chemin.resolve().relative_to(base)
    except ValueError:
        return True

    for part in rel.parts[:-1]:
        if part in _DOSSIERS_IGNORES or part.startswith("."):
            return True
    return False


def _inserer_note_curseur(conn: Any, note: dict[str, Any]) -> None:
    """Insère ou met à jour une note dans ``notes`` et ``notes_fts`` sur une connexion ouverte."""
    tags_json = json.dumps(note["tags"], ensure_ascii=False)
    liens_json = json.dumps(note["liens"], ensure_ascii=False)
    tags_texte = " ".join(note["tags"])

    conn.execute(
        """
        INSERT INTO notes (id, titre, branche, tags, statut, date, mtime, taille, liens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            titre = excluded.titre,
            branche = excluded.branche,
            tags = excluded.tags,
            statut = excluded.statut,
            date = excluded.date,
            mtime = excluded.mtime,
            taille = excluded.taille,
            liens = excluded.liens
        """,
        (
            note["id"],
            note["titre"],
            note["branche"],
            tags_json,
            note["statut"],
            note["date"],
            note["mtime"],
            note["taille"],
            liens_json,
        ),
    )
    conn.execute("DELETE FROM notes_fts WHERE id = ?", (note["id"],))
    conn.execute(
        "INSERT INTO notes_fts (id, titre, contenu, tags) VALUES (?, ?, ?, ?)",
        (note["id"], note["titre"], note["contenu"], tags_texte),
    )


def reindexer_tout() -> int:
    """Réindexe intégralement toutes les notes ``.md`` présentes sous ``BRAIN_DIR``.

    Effets de bord:
        Remplace le contenu des tables ``notes`` et ``notes_fts`` dans SQLite.

    Returns:
        Nombre total de notes indexées.
    """
    brain_dir = config.BRAIN_DIR
    brain_dir.mkdir(parents=True, exist_ok=True)

    notes_parsees: list[dict[str, Any]] = []
    for fichier in sorted(brain_dir.rglob("*.md")):
        if not fichier.is_file() or est_chemin_ignore(fichier, brain_dir=brain_dir):
            continue
        try:
            notes_parsees.append(lire_note(fichier, brain_dir=brain_dir))
        except Exception as exc:
            logger.warning("Impossible de parser la note %s : %s", fichier, exc)

    with db._write_lock:
        conn = db.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM notes")
                conn.execute("DELETE FROM notes_fts")
                for note in notes_parsees:
                    _inserer_note_curseur(conn, note)
        finally:
            conn.close()

    return len(notes_parsees)


def indexer_fichier(chemin: Path) -> bool:
    """Indexe ou met à jour une note unique dans ``notes`` et ``notes_fts``.

    Si le fichier n'existe plus ou est ignoré, tente de le retirer de l'index.

    Args:
        chemin: Chemin du fichier Markdown modifié ou créé.

    Returns:
        ``True`` si le fichier a été indexé, ``False`` sinon.
    """
    brain_dir = config.BRAIN_DIR
    if not chemin.is_file() or est_chemin_ignore(chemin, brain_dir=brain_dir):
        try:
            note_id = id_depuis_chemin(chemin, brain_dir=brain_dir)
            retirer(note_id)
        except ValueError:
            pass
        return False

    note = lire_note(chemin, brain_dir=brain_dir)
    with db._write_lock:
        conn = db.get_connection()
        try:
            with conn:
                _inserer_note_curseur(conn, note)
        finally:
            conn.close()
    return True


def retirer(note_id: str) -> None:
    """Supprime une note de ``notes`` et ``notes_fts``.

    Args:
        note_id: Identifiant POSIX relatif sans ``.md``.
    """
    with db._write_lock:
        conn = db.get_connection()
        try:
            with conn:
                conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
                conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
        finally:
            conn.close()


def resoudre_liens(
    notes_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Résout les liens wiki entre notes par identifiant, nom de fichier puis titre.

    La comparaison est insensible à la casse et aux accents.

    Args:
        notes_rows: Lignes lues depuis la table ``notes`` (avec ``id``, ``titre``, ``liens``).

    Returns:
        Tuple ``(liens_resolus, liens_casses)`` contenant des dicts ``{"source", "cible"}``.
    """
    par_id: dict[str, str] = {}
    par_nom: dict[str, str] = {}
    par_titre: dict[str, str] = {}

    for row in notes_rows:
        note_id = str(row["id"])
        titre = str(row.get("titre") or "")
        nom = PurePosixPath(note_id).name

        par_id.setdefault(normaliser_texte(note_id), note_id)
        par_nom.setdefault(normaliser_texte(nom), note_id)
        if titre:
            par_titre.setdefault(normaliser_texte(titre), note_id)

    resolus: list[dict[str, str]] = []
    casses: list[dict[str, str]] = []
    vus_resolus: set[tuple[str, str]] = set()
    vus_casses: set[tuple[str, str]] = set()

    for row in notes_rows:
        source_id = str(row["id"])
        liens_raw = row.get("liens")
        if isinstance(liens_raw, str):
            try:
                liens = json.loads(liens_raw)
            except json.JSONDecodeError:
                liens = []
        elif isinstance(liens_raw, list):
            liens = liens_raw
        else:
            liens = []

        for cible_brute in liens:
            if not isinstance(cible_brute, str):
                continue
            cle = normaliser_texte(cible_brute.replace("\\", "/"))
            if not cle:
                continue

            cible_id = par_id.get(cle) or par_nom.get(cle) or par_titre.get(cle)
            if cible_id is not None:
                paire = (source_id, cible_id)
                if paire not in vus_resolus:
                    vus_resolus.add(paire)
                    resolus.append({"source": source_id, "cible": cible_id})
            else:
                paire_cassee = (source_id, cible_brute.strip())
                if paire_cassee not in vus_casses:
                    vus_casses.add(paire_cassee)
                    casses.append({"source": source_id, "cible": cible_brute.strip()})

    return resolus, casses
