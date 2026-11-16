"""Tests des endpoints REST /api/taches, /api/cartes et /api/stats."""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.brain import indexer, memories


def test_api_taches_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Vérifie GET, POST, PATCH et DELETE /api/taches."""
    cree = client.post(
        "/api/taches",
        headers=auth_headers,
        json={"titre": "Préparer la démo"},
    )
    assert cree.status_code == 200
    tache = cree.json()
    assert tache["titre"] == "Préparer la démo"
    assert tache["fait"] is False
    tache_id = tache["id"]

    liste = client.get("/api/taches")
    assert liste.status_code == 200
    assert len(liste.json()) == 1

    patch = client.patch(
        f"/api/taches/{tache_id}",
        headers=auth_headers,
        json={"fait": True, "titre": "Préparer et tester la démo"},
    )
    assert patch.status_code == 200
    assert patch.json()["fait"] is True
    assert patch.json()["titre"] == "Préparer et tester la démo"

    suppr = client.delete(f"/api/taches/{tache_id}", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/taches").json() == []


def test_api_cartes_crud_et_epinglage(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Vérifie GET, POST, POST /epingler et DELETE /api/cartes."""
    cree = client.post(
        "/api/cartes",
        headers=auth_headers,
        json={"titre": "Alerte Quota", "contenu_md": "Gemini à 85 %", "type": "info"},
    )
    assert cree.status_code == 200
    carte_id = cree.json()["id"]
    assert cree.json()["epinglee"] is True

    assert len(client.get("/api/cartes?epinglees=1").json()) == 1

    desepingle = client.post(
        f"/api/cartes/{carte_id}/epingler",
        headers=auth_headers,
        json={"epinglee": False},
    )
    assert desepingle.status_code == 200
    assert desepingle.json()["epinglee"] is False
    assert client.get("/api/cartes?epinglees=1").json() == []
    assert len(client.get("/api/cartes").json()) == 1

    suppr = client.delete(f"/api/cartes/{carte_id}", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/cartes").json() == []


def test_api_stats_agregation(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
) -> None:
    """GET /api/stats agrège correctement notes, liens, routines, tâches, messages, LLM et souvenirs."""
    brain_dir = jarvis_root / "second-brain"
    (brain_dir / "01-Business-Projets").mkdir(parents=True, exist_ok=True)
    (brain_dir / "00-Profil").mkdir(parents=True, exist_ok=True)
    (brain_dir / "01-Business-Projets" / "p1.md").write_text(
        "---\ntitre: Projet 1\nstatut: actif\ndate: 2026-11-16\n---\nVoir [[profil]].\n",
        encoding="utf-8",
    )
    (brain_dir / "00-Profil" / "profil.md").write_text(
        "---\ntitre: Profil\nstatut: actif\ndate: 2026-11-16\n---\nProfil.\n",
        encoding="utf-8",
    )
    indexer.reindexer_tout()

    memories.ajouter_souvenir("fait", "Test statistique", source="test", agent="jarvis")
    # Réindexe après création de SOUVENIRS.md pour vérifier que le compte reflète tout le coffre
    indexer.reindexer_tout()

    (jarvis_root / "aios" / "routines" / "r1.md").write_text(
        "---\nid: r1\nnom: R1\ncron: '0 7 * * *'\nagent: jarvis\nactif: true\n---\nGo\n",
        encoding="utf-8",
    )

    aujourd_hui = date.today().isoformat()
    maintenant = db.maintenant_iso()
    db.execute(
        "INSERT INTO taches (titre, fait, date, source, ordre) VALUES ('T1', 1, ?, 'manuel', 0)",
        (aujourd_hui,),
    )
    db.execute(
        "INSERT INTO taches (titre, fait, date, source, ordre) VALUES ('T2', 0, ?, 'manuel', 1)",
        (aujourd_hui,),
    )
    db.execute(
        "INSERT INTO runs_routines (routine_id, debut, fin, statut) VALUES ('r1', ?, ?, 'ok')",
        (maintenant, maintenant),
    )
    db.execute(
        "INSERT INTO messages (conversation_id, role, auteur, contenu, cree_le) VALUES ('agent:jarvis', 'user', 'utilisateur', 'Salut', ?)",
        (maintenant,),
    )
    db.execute(
        "INSERT INTO usage_llm (ts, moteur, modele, input_tokens, output_tokens, duree, ok) VALUES (?, 'agy', 'hydrogen', 120, 30, 1.2, 1)",
        (maintenant,),
    )

    resp = client.get("/api/stats", headers=auth_headers)
    assert resp.status_code == 200
    s = resp.json()

    assert s["notes"] == 3  # p1 + profil + SOUVENIRS.md
    assert s["liens"] == 1
    assert s["branches"]["01-Business-Projets"] == 1
    assert s["branches"]["00-Profil"] == 1
    assert s["projets_actifs"] == 1
    assert s["routines_actives"] == 1
    assert s["runs_7j"] == {"ok": 1, "erreur": 0}
    assert s["taches_jour"] == {"faites": 1, "total": 2}
    assert s["messages_24h"] == 1
    assert s["usage_llm_24h"] == {
        "appels": 1,
        "tokens": 150,
        "par_modele": {"hydrogen": 1},
    }
    assert s["souvenirs"] == 1
