"""Tests du second cerveau : indexation FTS5, liens wiki, graphe, sécurité chemins et API."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.brain import graph, indexer, search
from backend.brain.paths import chemin_sur


def _creer_mini_coffre(brain_dir: Path) -> None:
    """Peuple un mini-coffre temporaire avec des notes, liens valides et liens cassés."""
    (brain_dir / "00-Profil").mkdir(parents=True, exist_ok=True)
    (brain_dir / "01-Business-Projets").mkdir(parents=True, exist_ok=True)
    (brain_dir / "02-Connaissances-Methodes").mkdir(parents=True, exist_ok=True)
    (brain_dir / ".obsidian").mkdir(parents=True, exist_ok=True)

    (brain_dir / ".obsidian" / "workspace.md").write_text("# Ignoré", encoding="utf-8")
    (brain_dir / "~brouillon.md").write_text("# Ignoré", encoding="utf-8")

    (brain_dir / "00-Profil" / "profil.md").write_text(
        "---\n"
        "titre: Profil Utilisateur\n"
        "tags: [profil, identite]\n"
        "branche: 00-Profil\n"
        "statut: actif\n"
        "date: 2026-11-15\n"
        "---\n\n"
        "Objectif principal : piloter [[projet-alpha|Alpha]] et [[Méthode Prospection#Étapes]].\n",
        encoding="utf-8",
    )
    (brain_dir / "01-Business-Projets" / "projet-alpha.md").write_text(
        "---\n"
        "titre: Projet Alpha\n"
        "tags: [business, priorite]\n"
        "branche: 01-Business-Projets\n"
        "statut: actif\n"
        "date: 2026-11-10\n"
        "---\n\n"
        "Stratégie de développement avec référence manquante [[note-fantome]].\n",
        encoding="utf-8",
    )
    (brain_dir / "02-Connaissances-Methodes" / "methode-prospection.md").write_text(
        "---\n"
        "titre: Méthode Prospection\n"
        "tags: [methode, commercial]\n"
        "branche: 02-Connaissances-Methodes\n"
        "statut: archive\n"
        "date: 2024-01-10\n"
        "---\n\n"
        "## Étapes\n\n"
        "Préparation avancée pour la prospection téléphonique.\n",
        encoding="utf-8",
    )


def test_indexation_liens_graphe_et_recherche(jarvis_root: Path) -> None:
    """Vérifie l'indexation, la résolution de liens (casse/accents), le graphe et FTS5."""
    brain_dir = jarvis_root / "second-brain"
    _creer_mini_coffre(brain_dir)

    nb = indexer.reindexer_tout()
    assert nb == 3

    graphe = graph.construire_graphe()
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    assert set(noeuds.keys()) == {
        "00-Profil/profil",
        "01-Business-Projets/projet-alpha",
        "02-Connaissances-Methodes/methode-prospection",
    }

    assert {"source": "00-Profil/profil", "cible": "01-Business-Projets/projet-alpha"} in graphe["liens"]
    assert {
        "source": "00-Profil/profil",
        "cible": "02-Connaissances-Methodes/methode-prospection",
    } in graphe["liens"]
    assert graphe["liens_casses"] == [
        {"source": "01-Business-Projets/projet-alpha", "cible": "note-fantome"}
    ]

    # Poids = 1 + entrants + sortants
    assert noeuds["00-Profil/profil"]["poids"] == 3
    assert noeuds["01-Business-Projets/projet-alpha"]["poids"] == 2
    assert noeuds["01-Business-Projets/projet-alpha"]["actif"] is True
    assert noeuds["02-Connaissances-Methodes/methode-prospection"]["actif"] is False

    # Recherche insensible aux accents + préfixe
    res_accent = search.rechercher("methode prospect", limite=10)
    # Deux notes contiennent ces mots (la note de méthode et une note qui la cite).
    par_id = {r["id"]: r for r in res_accent}
    assert "02-Connaissances-Methodes/methode-prospection" in par_id
    assert "<mark>" in par_id["02-Connaissances-Methodes/methode-prospection"]["extrait"]


def test_chemin_sur_refuse_traversee_et_absolu(jarvis_root: Path) -> None:
    """chemin_sur bloque toute tentative de sortie du coffre (.., absolu, lecteur)."""
    valide = chemin_sur("00-Profil/test-note")
    assert valide == (jarvis_root / "second-brain" / "00-Profil" / "test-note.md").resolve()

    for mauvais in (
        "",
        "../secret",
        "00-Profil/../../secret",
        "/etc/passwd",
        "\\Windows\\System32\\cmd",
        "C:\\temp\\note",
        "C:note",
    ):
        with pytest.raises(ValueError):
            chemin_sur(mauvais)


def test_api_cerveau_put_note_reindexation_et_notes(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
) -> None:
    """PUT /api/cerveau/note écrit la note, la réindexe et l'expose via GET note/notes/graphe."""
    _creer_mini_coffre(jarvis_root / "second-brain")
    indexer.reindexer_tout()

    # PUT crée et réindexe une nouvelle note
    put_resp = client.put(
        "/api/cerveau/note",
        headers=auth_headers,
        json={
            "id": "03-Outils-Workflows/nouveau-guide",
            "contenu_md": (
                "---\n"
                "titre: Nouveau Guide\n"
                "tags: [outils]\n"
                "statut: actif\n"
                "date: 2026-11-16\n"
                "---\n\n"
                "Documentation reliée à [[profil]].\n"
            ),
        },
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["id"] == "03-Outils-Workflows/nouveau-guide"

    # GET /note exige le jeton
    assert client.get("/api/cerveau/note?id=03-Outils-Workflows/nouveau-guide").status_code == 403

    detail = client.get(
        "/api/cerveau/note?id=03-Outils-Workflows/nouveau-guide",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    d_json = detail.json()
    assert d_json["titre"] == "Nouveau Guide"
    assert d_json["branche"] == "03-Outils-Workflows"
    assert "00-Profil/profil" in d_json["liens_sortants"]

    # Chemin malveillant -> 400
    bad = client.get("/api/cerveau/note?id=../runtime/token.txt", headers=auth_headers)
    assert bad.status_code == 400

    # GET /notes filtré + tri décroissant
    notes_resp = client.get("/api/cerveau/notes?statut=actif")
    assert notes_resp.status_code == 200
    ids = [n["id"] for n in notes_resp.json()]
    assert ids[0] == "03-Outils-Workflows/nouveau-guide"
    assert "02-Connaissances-Methodes/methode-prospection" not in ids

    # Recherche trouve la note fraîchement écrite
    rech = client.get("/api/cerveau/recherche?q=Documentation")
    assert rech.status_code == 200
    assert any(r["id"] == "03-Outils-Workflows/nouveau-guide" for r in rech.json())
