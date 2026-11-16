"""Parseur JSON tolérant pour extraire des objets structurés depuis du texte LLM."""

import json
import re
from typing import Any

_BLOC_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _supprimer_virgules_finales(texte: str) -> str:
    """Supprime les virgules finales avant ``}`` ou ``]`` hors littéraux de chaîne JSON."""
    resultat: list[str] = []
    dans_chaine = False
    echappe = False
    n = len(texte)
    i = 0

    while i < n:
        ch = texte[i]
        if dans_chaine:
            resultat.append(ch)
            if echappe:
                echappe = False
            elif ch == "\\":
                echappe = True
            elif ch == '"':
                dans_chaine = False
            i += 1
            continue

        if ch == '"':
            dans_chaine = True
            resultat.append(ch)
            i += 1
            continue

        if ch == ",":
            # Regarde en avant en sautant les espaces pour voir si } ou ] suit immédiatement.
            j = i + 1
            while j < n and texte[j] in " \t\r\n":
                j += 1
            if j < n and texte[j] in "}]":
                i += 1
                continue

        resultat.append(ch)
        i += 1

    return "".join(resultat)


def _tenter_parse_dict(candidat: str) -> dict[str, Any] | None:
    """Tente de parser ``candidat`` en dictionnaire JSON, avec tolérance aux virgules finales."""
    propre = candidat.strip()
    if not propre:
        return None
    for essai in (propre, _supprimer_virgules_finales(propre)):
        try:
            valeur = json.loads(essai)
        except json.JSONDecodeError:
            continue
        if isinstance(valeur, dict):
            return valeur
    return None


def _extraire_objets_equilibres(texte: str) -> list[str]:
    """Extrait les sous-chaînes ``{...}`` équilibrées en ignorant les chaînes JSON."""
    objets: list[str] = []
    dans_chaine = False
    echappe = False
    profondeur = 0
    debut = -1

    for idx, ch in enumerate(texte):
        if dans_chaine:
            if echappe:
                echappe = False
            elif ch == "\\":
                echappe = True
            elif ch == '"':
                dans_chaine = False
            continue

        if ch == '"':
            dans_chaine = True
        elif ch == "{":
            if profondeur == 0:
                debut = idx
            profondeur += 1
        elif ch == "}" and profondeur > 0:
            profondeur -= 1
            if profondeur == 0 and debut >= 0:
                objets.append(texte[debut : idx + 1])
                debut = -1

    return objets


def extraire_json(texte: str) -> dict[str, Any] | None:
    """Extrait le premier objet JSON valide depuis une réponse LLM brute.

    Gère le JSON pur, les blocs Markdown ```` ```json ````, les objets entourés
    de prose et les virgules finales devant ``}`` ou ``]``.

    Args:
        texte: Réponse textuelle brute du modèle.

    Returns:
        Dictionnaire décodé ou ``None`` si aucun objet JSON valide n'est trouvé.
    """
    if not isinstance(texte, str) or not texte.strip():
        return None

    direct = _tenter_parse_dict(texte)
    if direct is not None:
        return direct

    for match in _BLOC_FENCE_RE.finditer(texte):
        contenu = match.group(1)
        parse = _tenter_parse_dict(contenu)
        if parse is not None:
            return parse
        for obj in _extraire_objets_equilibres(contenu):
            parse = _tenter_parse_dict(obj)
            if parse is not None:
                return parse

    for obj in _extraire_objets_equilibres(texte):
        parse = _tenter_parse_dict(obj)
        if parse is not None:
            return parse

    return None
