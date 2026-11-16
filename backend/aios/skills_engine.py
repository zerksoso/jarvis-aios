"""Moteur de gestion, correspondance et création de skills Markdown (``aios/skills/*/SKILL.md``)."""

import re
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from backend import config
from backend.brain.parser import normaliser_texte

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ETAPE_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_TITRE_SECTION_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$")


def slugifier_nom(nom: str) -> str:
    """Transforme un nom de skill en slug sûr utilisable comme nom de dossier.

    Args:
        nom: Nom brut fourni par l'utilisateur ou un agent.

    Returns:
        Slug alphanumérique en minuscules séparé par des tirets.

    Raises:
        ValueError: Si le slug résultant est vide ou contient une séquence invalide.
    """
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError("Nom de skill vide.")
    if ".." in nom or "/" in nom or "\\" in nom or ":" in nom:
        raise ValueError(f"Nom de skill interdit : {nom!r}")

    norm = normaliser_texte(nom)
    slug = _NON_ALNUM_RE.sub("-", norm).strip("-")
    if not slug:
        raise ValueError(f"Impossible de générer un slug valide depuis {nom!r}")
    return slug


def _chemin_skill(slug: str) -> Path:
    """Résout le chemin ``SKILLS_DIR/<slug>/SKILL.md`` avec vérification de confinement."""
    base = config.SKILLS_DIR.resolve()
    cible = (base / slug / "SKILL.md").resolve()
    if not cible.is_relative_to(base):
        raise ValueError("Chemin de skill hors de SKILLS_DIR.")
    return cible


def _extraire_etapes(contenu: str) -> list[str]:
    """Extrait les étapes numérotées situées sous la section ``## Étapes``."""
    etapes: list[str] = []
    dans_etapes = False

    for ligne in contenu.splitlines():
        titre_match = _TITRE_SECTION_RE.match(ligne)
        if titre_match:
            titre_norm = normaliser_texte(titre_match.group(1))
            if titre_norm == "etapes":
                dans_etapes = True
                continue
            if dans_etapes:
                break

        if not dans_etapes:
            continue

        etape_match = _ETAPE_RE.match(ligne)
        if etape_match:
            etapes.append(etape_match.group(1).strip())

    # Repli si le skill contient une liste numérotée sans titre "## Étapes" explicite
    if not etapes:
        for ligne in contenu.splitlines():
            etape_match = _ETAPE_RE.match(ligne)
            if etape_match:
                etapes.append(etape_match.group(1).strip())

    return etapes


def _normaliser_liste(valeur: Any) -> list[str]:
    """Normalise un champ frontmatter de type liste de chaînes."""
    if isinstance(valeur, list):
        return [str(v).strip() for v in valeur if str(v).strip()]
    if isinstance(valeur, str):
        return [p.strip() for p in valeur.split(",") if p.strip()]
    return []


def _parser_fichier_skill(chemin: Path) -> dict[str, Any]:
    """Lit un fichier ``SKILL.md`` et retourne ses métadonnées, son corps et ses étapes."""
    brut = chemin.read_text(encoding="utf-8", errors="replace")
    meta, contenu = frontmatter.parse(brut)
    etapes = _extraire_etapes(contenu)

    nom = str(meta.get("nom") or chemin.parent.name).strip() or chemin.parent.name
    description = str(meta.get("description") or "").strip()
    declencheurs = _normaliser_liste(meta.get("declencheurs"))
    outils = _normaliser_liste(meta.get("outils"))
    agent_defaut = str(meta.get("agent_par_defaut") or "jarvis").strip() or "jarvis"

    return {
        "nom": nom,
        "slug": chemin.parent.name,
        "description": description,
        "declencheurs": declencheurs,
        "outils": outils,
        "nb_etapes": len(etapes),
        "agent_par_defaut": agent_defaut,
        "contenu_md": brut,
        "etapes": etapes,
    }


def lister_skills() -> list[dict[str, Any]]:
    """Liste tous les skills disponibles sous ``aios/skills/*/SKILL.md``.

    Returns:
        Liste triée de dictionnaires résumés (``nom``, ``description``, ``declencheurs``,
        ``outils``, ``nb_etapes``, ``agent_par_defaut``).
    """
    skills_dir = config.SKILLS_DIR
    if not skills_dir.exists():
        return []

    resultat: list[dict[str, Any]] = []
    for dossier in sorted(skills_dir.iterdir()):
        if not dossier.is_dir():
            continue
        fichier = dossier / "SKILL.md"
        if not fichier.is_file():
            continue
        parsed = _parser_fichier_skill(fichier)
        resultat.append(
            {
                "nom": parsed["nom"],
                "description": parsed["description"],
                "declencheurs": parsed["declencheurs"],
                "outils": parsed["outils"],
                "nb_etapes": parsed["nb_etapes"],
                "agent_par_defaut": parsed["agent_par_defaut"],
            }
        )
    return resultat


def charger_skill(nom: str) -> dict[str, Any] | None:
    """Charge la définition complète d'un skill par son nom ou slug.

    Args:
        nom: Nom ou slug du skill.

    Returns:
        Dictionnaire complet incluant ``contenu_md`` et ``etapes``, ou ``None`` si absent.

    Raises:
        ValueError: Si ``nom`` contient des caractères ou séquences interdits.
    """
    slug = slugifier_nom(nom)
    chemin = _chemin_skill(slug)
    if not chemin.is_file():
        return None
    parsed = _parser_fichier_skill(chemin)
    parsed.pop("slug", None)
    return parsed


def trouver_skills(texte: str) -> list[str]:
    """Identifie les skills dont au moins un déclencheur apparaît dans ``texte``.

    La recherche est insensible à la casse et aux accents.

    Args:
        texte: Message ou consigne utilisateur.

    Returns:
        Liste des noms de skills correspondants.
    """
    if not texte or not texte.strip():
        return []

    texte_norm = normaliser_texte(texte)
    correspondances: list[str] = []
    for skill in lister_skills():
        for declencheur in skill["declencheurs"]:
            dec_norm = normaliser_texte(declencheur)
            if dec_norm and dec_norm in texte_norm:
                correspondances.append(skill["nom"])
                break
    return correspondances


def creer_skill(
    nom: str,
    description: str,
    declencheurs: list[str],
    outils: list[str],
    etapes: list[str],
    corps_md: str | None = None,
    ecraser: bool = False,
    agent_par_defaut: str = "jarvis",
) -> str:
    """Crée ou met à jour un skill ``aios/skills/<slug>/SKILL.md``.

    Args:
        nom: Nom du skill (converti en slug sûr pour le dossier).
        description: Description courte de l'usage du skill.
        declencheurs: Liste de mots-clés ou expressions déclencheuses.
        outils: Liste des noms d'outils autorisés pour ce skill.
        etapes: Liste ordonnée des étapes du workflow.
        corps_md: Corps Markdown complet optionnel (remplace la section auto-générée).
        ecraser: Autorise l'écrasement d'un skill existant.
        agent_par_defaut: Identifiant de l'agent recommandé.

    Returns:
        Slug du skill créé ou mis à jour.

    Raises:
        ValueError: Si le nom est invalide.
        FileExistsError: Si le skill existe déjà et ``ecraser`` est ``False``.
    """
    slug = slugifier_nom(nom)
    chemin = _chemin_skill(slug)

    if chemin.exists() and not ecraser:
        raise FileExistsError(f"Le skill '{slug}' existe déjà.")

    meta = {
        "nom": slug,
        "description": description.strip(),
        "declencheurs": [str(d).strip() for d in declencheurs if str(d).strip()],
        "outils": [str(o).strip() for o in outils if str(o).strip()],
        "agent_par_defaut": agent_par_defaut.strip() or "jarvis",
    }
    yaml_txt = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()

    if corps_md is not None and corps_md.strip():
        # Si l'appelant passe déjà un document avec frontmatter, on extrait juste son corps.
        _, corps_extrait = frontmatter.parse(corps_md)
        corps = corps_extrait.strip()
    else:
        lignes_etapes = "\n".join(
            f"{idx}. {str(etape).strip()}" for idx, etape in enumerate(etapes, start=1) if str(etape).strip()
        )
        corps = f"# {slug}\n\n{description.strip()}\n\n## Étapes\n\n{lignes_etapes}".strip()

    contenu_final = f"---\n{yaml_txt}\n---\n\n{corps}\n"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu_final, encoding="utf-8")
    return slug
