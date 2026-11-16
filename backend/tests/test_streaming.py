"""Tests unitaires de ``ExtracteurReponse`` (streaming JSON, échappements et texte brut)."""

import pytest

from backend.agents.streaming import ExtracteurReponse


@pytest.mark.parametrize("taille_morceau", [1, 2, 3, 5, 11, 64])
def test_extraction_json_decoupe_en_morceaux_arbitraires(taille_morceau: int) -> None:
    """Valide l'extraction exacte de 'reponse' quelle que soit la taille des morceaux."""
    flux_json = (
        '{"pensee": "Analyse interne avec \\"reponse\\": fausse", '
        '"actions": [{"outil": "cerveau.chercher", "args": {"reponse": "ignore"}}], '
        '"reponse": "Bonjour ! Voici le résumé :\\n- Point 1 (éàç)\\n- Citation : \\"Jarvis\\"", '
        '"termine": true}'
    )
    attendu = 'Bonjour ! Voici le résumé :\n- Point 1 (éàç)\n- Citation : "Jarvis"'

    extracteur = ExtracteurReponse()
    morceaux_emis: list[str] = []
    for i in range(0, len(flux_json), taille_morceau):
        delta = flux_json[i : i + taille_morceau]
        extrait = extracteur.alimenter(delta)
        if extrait:
            morceaux_emis.append(extrait)

    assert "".join(morceaux_emis) == attendu
    assert extracteur.reponse_extraite == attendu
    assert extracteur.est_json is True


def test_echappements_json_et_unicode_fractionnes() -> None:
    """Gère les séquences d'échappement coupées au milieu (\\n, \\", \\\\, \\u00e9)."""
    fragments = [
        '{"pensee":"ok","reponse":"Caf',
        "\\",
        "u0",
        "0e",
        "9 ",
        "\\",
        '"chaud\\',
        '"\\',
        "nLigne 2 \\",
        "\\ fin",
        '","termine":true}',
    ]
    extracteur = ExtracteurReponse()
    sortie = "".join(extracteur.alimenter(f) for f in fragments)
    assert sortie == 'Café "chaud"\nLigne 2 \\ fin'


def test_bloc_markdown_fence_json() -> None:
    """Extrait correctement la clé 'reponse' lorsque le modèle entoure le JSON de ```json."""
    fragments = [
        "  ```",
        "json\n",
        '{"pensee": "test", "actions": [], "reponse": "Réponse en bloc code", "termine": true}\n',
        "```",
    ]
    extracteur = ExtracteurReponse()
    sortie = "".join(extracteur.alimenter(f) for f in fragments)
    assert sortie == "Réponse en bloc code"
    assert extracteur.est_json is True


def test_texte_non_json_transmis_tel_quel() -> None:
    """Si le flux ne commence pas par { ni ```json, les deltas bruts sont transmis tels quels."""
    deltas = ["Bonjour ", "à tous, ", "voici une réponse directe avec é et {accolades} !"]
    extracteur = ExtracteurReponse()
    recus = [extracteur.alimenter(d) for d in deltas]

    assert recus == deltas
    assert "".join(recus) == "".join(deltas)
    assert extracteur.est_json is False
