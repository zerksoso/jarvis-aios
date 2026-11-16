"""Tests unitaires du parseur JSON tolérant ``extraire_json``."""

from backend.llm.jsonparse import extraire_json


def test_json_pur() -> None:
    """Parse un objet JSON standard sans bruit."""
    assert extraire_json('{"action": "repondre", "ok": true}') == {
        "action": "repondre",
        "ok": True,
    }


def test_bloc_markdown_json() -> None:
    """Extrait l'objet contenu dans un bloc ```json ... ``` entouré de texte."""
    texte = (
        "Voici la réponse demandée :\n"
        "```json\n"
        '{"titre": "Note", "tags": ["ia", "jarvis"]}\n'
        "```\n"
        "Dis-moi si tu veux ajuster."
    )
    assert extraire_json(texte) == {"titre": "Note", "tags": ["ia", "jarvis"]}


def test_premier_objet_equilibre_avec_accolades_dans_chaine() -> None:
    """Ignore les accolades dans les chaînes et extrait le premier objet équilibré."""
    texte = (
        'Analyse préalable {non valide}. '
        'Résultat : {"message": "accolade } interne {", "meta": {"n": 2}} et suite.'
    )
    assert extraire_json(texte) == {
        "message": "accolade } interne {",
        "meta": {"n": 2},
    }


def test_virgules_finales() -> None:
    """Accepte les virgules finales avant } ou ] hors chaînes."""
    texte = '{"liste": [1, 2, 3,], "objet": {"cle": "a,}",},}'
    assert extraire_json(texte) == {
        "liste": [1, 2, 3],
        "objet": {"cle": "a,}"},
    }


def test_texte_sans_json_retourne_none() -> None:
    """Retourne None si aucun objet JSON valide n'est présent."""
    assert extraire_json("") is None
    assert extraire_json("Bonjour, voici juste du texte.") is None
    assert extraire_json("[1, 2, 3]") is None
    assert extraire_json("{clé sans guillemets: 1}") is None
