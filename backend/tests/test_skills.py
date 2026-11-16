"""Tests du moteur de skills Markdown et des routes /api/skills."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.aios import skills_engine


def test_creer_charger_lister_et_trouver_skills(jarvis_root: Path) -> None:
    """Valide la création sécurisée, le parsing des étapes et la détection par déclencheurs."""
    del jarvis_root
    slug = skills_engine.creer_skill(
        nom="Résumé Hebdomadaire",
        description="Génère un résumé hebdomadaire des notes.",
        declencheurs=["résumé hebdo", "bilan de la semaine"],
        outils=["cerveau.chercher", "cerveau.lire"],
        etapes=["Chercher les notes récentes", "Rédiger la synthèse en 5 points"],
    )
    assert slug == "resume-hebdomadaire"

    liste = skills_engine.lister_skills()
    assert len(liste) == 1
    assert liste[0]["nom"] == "resume-hebdomadaire"
    assert liste[0]["nb_etapes"] == 2

    detail = skills_engine.charger_skill("resume-hebdomadaire")
    assert detail is not None
    assert detail["etapes"] == [
        "Chercher les notes récentes",
        "Rédiger la synthèse en 5 points",
    ]
    assert "## Étapes" in detail["contenu_md"]

    # Insensible à la casse et aux accents
    assert skills_engine.trouver_skills("Peux-tu me faire un RESUME HEBDO ?") == [
        "resume-hebdomadaire"
    ]

    with pytest.raises(FileExistsError):
        skills_engine.creer_skill(
            nom="Résumé Hebdomadaire",
            description="Doublon",
            declencheurs=[],
            outils=[],
            etapes=["Étape 1"],
            ecraser=False,
        )

    with pytest.raises(ValueError):
        skills_engine.creer_skill(
            nom="../traversee",
            description="Interdit",
            declencheurs=[],
            outils=[],
            etapes=[],
        )


def test_api_skills_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    """POST, GET et PUT /api/skills gèrent les fichiers SKILL.md."""
    create = client.post(
        "/api/skills",
        headers=auth_headers,
        json={
            "nom": "Veille Rapide",
            "description": "Recherche web rapide",
            "declencheurs": ["veille rapide"],
            "outils": ["web.chercher"],
            "etapes": ["Lancer web.chercher", "Résumer 3 liens"],
        },
    )
    assert create.status_code == 200
    assert create.json()["nom"] == "veille-rapide"
    assert create.json()["nb_etapes"] == 2

    liste = client.get("/api/skills")
    assert liste.status_code == 200
    assert len(liste.json()) == 1

    maj = client.put(
        "/api/skills/veille-rapide",
        headers=auth_headers,
        json={
            "description": "Recherche web rapide mise à jour",
            "declencheurs": ["veille rapide", "actus"],
            "outils": ["web.chercher", "web.lire"],
            "etapes": ["Chercher", "Lire", "Synthétiser"],
        },
    )
    assert maj.status_code == 200
    assert maj.json()["nb_etapes"] == 3
    assert "web.lire" in maj.json()["outils"]
