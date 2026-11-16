"""Lecture et extraction de métadonnées YAML et liens wiki des notes Markdown."""

import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

import frontmatter

from backend.brain.paths import id_depuis_chemin

BRANCHES_META: list[dict[str, str]] = [
    {"id": "00-Profil", "libelle": "Profil", "couleur": "#f59e0b"},
    {"id": "01-Business-Projets", "libelle": "Business & Projets", "couleur": "#22d3ee"},
    {"id": "02-Connaissances-Methodes", "libelle": "Connaissances & Méthodes", "couleur": "#a78bfa"},
    {"id": "03-Outils-Workflows", "libelle": "Outils & Workflows", "couleur": "#34d399"},
    {"id": "04-Analytics-Resultats", "libelle": "Analytics & Résultats", "couleur": "#f472b6"},
    {"id": "05-Erreurs-Lecons", "libelle": "Erreurs & Leçons", "couleur": "#f87171"},
    {"id": "autres", "libelle": "Autres", "couleur": "#94a3b8"},
]

BRANCHES_IDS: set[str] = {b["id"] for b in BRANCHES_META}
STATUTS_VALIDES: set[str] = {"actif", "archive", "brouillon"}

_WIKI_LINK_RE = re.compile(r"\[\[([^\]\n]+)\]\]")


def normaliser_texte(texte: str) -> str:
    """Normalise un texte en minuscules sans accents ni espaces superflus.

    Args:
        texte: Chaîne à normaliser.

    Returns:
        Chaîne normalisée (NFKD sans diacritiques, espaces réduits).
    """
    sans_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", texte) if not unicodedata.combining(ch)
    )
    return " ".join(sans_accents.lower().split())


def extraire_liens_wiki(contenu: str) -> list[str]:
    """Extrait les cibles uniques des liens wiki ``[[Cible]]``, ``[[Cible|alias]]``, ``[[Cible#titre]]``.

    Args:
        contenu: Contenu Markdown brut ou sans frontmatter.

    Returns:
        Liste ordonnée et dédupliquée des cibles extraites.
    """
    cibles: list[str] = []
    vus: set[str] = set()
    for match in _WIKI_LINK_RE.finditer(contenu):
        brut = match.group(1).strip()
        if not brut:
            continue
        cible = brut.split("|", 1)[0].split("#", 1)[0].strip()
        if cible.lower().endswith(".md"):
            cible = cible[:-3].strip()
        if cible and cible not in vus:
            vus.add(cible)
            cibles.append(cible)
    return cibles


def _normaliser_tags(valeur: Any) -> list[str]:
    """Convertit le champ frontmatter ``tags`` en liste propre de chaînes."""
    if isinstance(valeur, list):
        return [str(item).strip() for item in valeur if str(item).strip()]
    if isinstance(valeur, str):
        return [part.strip() for part in valeur.split(",") if part.strip()]
    return []


def _normaliser_date(valeur: Any, mtime: float) -> str:
    """Normalise une date YAML (date, datetime ou chaîne) au format ``AAAA-MM-JJ``."""
    if isinstance(valeur, datetime):
        return valeur.date().isoformat()
    if isinstance(valeur, date):
        return valeur.isoformat()
    if isinstance(valeur, str):
        propre = valeur.strip()
        if len(propre) >= 10:
            return propre[:10]
        if propre:
            return propre
    return datetime.fromtimestamp(mtime).date().isoformat()


def _determiner_branche(note_id: str, meta_branche: Any) -> str:
    """Détermine la branche canonique d'une note depuis son frontmatter ou son dossier."""
    dossier = note_id.split("/", 1)[0] if "/" in note_id else ""
    if isinstance(meta_branche, str):
        candidat = meta_branche.strip()
        if candidat in BRANCHES_IDS:
            return candidat
    if dossier in BRANCHES_IDS:
        return dossier
    return "autres"


def lire_note(chemin: Path, brain_dir: Path | None = None) -> dict[str, Any]:
    """Lit un fichier ``.md`` du second cerveau et en extrait métadonnées, texte et liens.

    Args:
        chemin: Chemin absolu du fichier Markdown.
        brain_dir: Racine du coffre (par défaut ``config.BRAIN_DIR``).

    Returns:
        Dictionnaire complet prêt pour l'indexation SQLite et les réponses API.
    """
    stat = chemin.stat()
    contenu_brut = chemin.read_text(encoding="utf-8", errors="replace")
    meta, contenu = frontmatter.parse(contenu_brut)

    note_id = id_depuis_chemin(chemin, brain_dir=brain_dir)
    titre = str(meta.get("titre") or "").strip() or chemin.stem
    tags = _normaliser_tags(meta.get("tags"))
    branche = _determiner_branche(note_id, meta.get("branche"))

    statut_brut = str(meta.get("statut") or "actif").strip().lower()
    statut = statut_brut if statut_brut in STATUTS_VALIDES else "actif"

    date_str = _normaliser_date(meta.get("date"), stat.st_mtime)
    liens = extraire_liens_wiki(contenu)

    return {
        "id": note_id,
        "titre": titre,
        "branche": branche,
        "tags": tags,
        "statut": statut,
        "date": date_str,
        "mtime": float(stat.st_mtime),
        "taille": int(stat.st_size),
        "liens": liens,
        "contenu": contenu,
        "contenu_md": contenu_brut,
    }
