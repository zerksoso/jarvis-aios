"""Tests du planificateur de routines, cron, exécution manuelle et historique API."""

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.aios import scheduler
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult


class _MoteurRoutineSimule:
    """Moteur simulé générant un brief structuré avec section Tâches du jour."""

    nom = "agy"

    async def disponible(self) -> tuple[bool, str]:
        """Toujours disponible en test."""
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Retourne un Markdown réaliste avec 2 cases à cocher."""
        del req, session_id, on_delta
        texte = (
            "# Brief du matin\n\n"
            "Synthèse des priorités et métriques.\n\n"
            "## Tâches du jour\n\n"
            "- [ ] Relancer le client Alpha\n"
            "- [ ] Relire la proposition Q4\n"
        )
        return LLMResult(texte=texte, moteur="agy", modele=modele_id)


def _ecrire_routines_exemple(routines_dir: Path) -> None:
    """Écrit deux fichiers de routines dans un dossier temporaire."""
    routines_dir.mkdir(parents=True, exist_ok=True)
    (routines_dir / "brief-du-matin.md").write_text(
        "---\n"
        "id: brief-du-matin\n"
        "nom: Brief du matin\n"
        'cron: "0 6 * * *"\n'
        "agent: jarvis\n"
        "sortie: 04-Analytics-Resultats/briefs/{date}-brief-du-matin.md\n"
        "actif: true\n"
        "description: Brief matinal quotidien\n"
        "---\n\n"
        "Prépare le brief du matin avec la section Tâches du jour.\n",
        encoding="utf-8",
    )
    (routines_dir / "veille-hebdo.md").write_text(
        "---\n"
        "id: veille-hebdo\n"
        "nom: Veille hebdomadaire\n"
        'cron: "0 8 * * mon"\n'
        "agent: atlas\n"
        "sortie: 04-Analytics-Resultats/veilles/{date}-veille-hebdo.md\n"
        "actif: false\n"
        "description: Veille du lundi\n"
        "---\n\n"
        "Fais la veille de la semaine.\n",
        encoding="utf-8",
    )


def test_chargement_routines_et_prochaine_execution(jarvis_root: Path) -> None:
    """Vérifie la lecture des fichiers et le calcul ISO de la prochaine exécution."""
    _ecrire_routines_exemple(jarvis_root / "aios" / "routines")

    routines = scheduler.lister_routines()
    assert len(routines) == 2

    par_id = {r["id"]: r for r in routines}
    assert par_id["brief-du-matin"]["actif"] is True
    assert isinstance(par_id["brief-du-matin"]["prochaine_execution"], str)
    assert "T06:00:00" in par_id["brief-du-matin"]["prochaine_execution"]

    assert par_id["veille-hebdo"]["actif"] is False
    assert par_id["veille-hebdo"]["prochaine_execution"] is None

    # Vérifie aussi la fonction utilitaire directement pour une cron active
    prochaine = scheduler.prochaine_execution("0 8 * * mon")
    assert prochaine is not None
    assert "T08:00:00" in prochaine


async def test_lancer_routine_ecrit_sortie_extrait_taches_et_trace_run(jarvis_root: Path) -> None:
    """lancer_routine appelle le LLM, écrit dans le cerveau, extrait les tâches et met à jour la DB."""
    _ecrire_routines_exemple(jarvis_root / "aios" / "routines")
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.95}}})
    engines: dict[str, Engine] = {"agy": _MoteurRoutineSimule()}
    router.set_engines_for_tests(engines)

    run_id = await scheduler.lancer_routine("brief-du-matin")
    assert isinstance(run_id, int) and run_id > 0

    jour = date.today().isoformat()
    sortie = jarvis_root / "second-brain" / "04-Analytics-Resultats" / "briefs" / f"{jour}-brief-du-matin.md"
    assert sortie.is_file()
    contenu = sortie.read_text(encoding="utf-8")
    assert "tags: [routine, brief-du-matin]" in contenu or "- routine" in contenu
    assert "Relancer le client Alpha" in contenu

    # Tâches extraites en base sans doublon au 2e lancement
    taches = db.query("SELECT titre, source FROM taches WHERE date = ? ORDER BY id ASC", (jour,))
    assert len(taches) == 2
    assert taches[0] == {"titre": "Relancer le client Alpha", "source": "brief"}
    assert taches[1] == {"titre": "Relire la proposition Q4", "source": "brief"}

    await scheduler.lancer_routine("brief-du-matin")
    taches_apres = db.query("SELECT titre FROM taches WHERE date = ?", (jour,))
    assert len(taches_apres) == 2

    run = db.query_one("SELECT statut, sortie_path, resume FROM runs_routines WHERE id = ?", (run_id,))
    assert run is not None
    assert run["statut"] == "ok"
    assert run["sortie_path"] == f"04-Analytics-Resultats/briefs/{jour}-brief-du-matin"
    assert "Brief du matin" in str(run["resume"])


def test_api_routines_crud_lancer_et_historique(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Valide le cycle CRUD + POST /lancer + GET /historique via l'API REST."""
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.9}}})
    router.set_engines_for_tests({"agy": _MoteurRoutineSimule()})

    cree = client.post(
        "/api/routines",
        headers=auth_headers,
        json={
            "id": "brief-du-matin",
            "nom": "Brief Test",
            "cron": "0 23 * * *",
            "agent": "jarvis",
            "sortie": "04-Analytics-Resultats/briefs/{date}-brief-du-matin.md",
            "actif": True,
            "description": "Routine test",
            "consigne": "Génère un brief.",
        },
    )
    assert cree.status_code == 200
    assert cree.json()["id"] == "brief-du-matin"

    lance = client.post("/api/routines/brief-du-matin/lancer", headers=auth_headers)
    assert lance.status_code == 200
    run_id = lance.json()["run_id"]
    assert isinstance(run_id, int)

    hist = client.get("/api/routines/brief-du-matin/historique?limite=5")
    assert hist.status_code == 200
    runs = hist.json()
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["statut"] == "ok"
    assert "Relancer le client Alpha" in str(runs[0]["contenu_md"])

    maj = client.put(
        "/api/routines/brief-du-matin",
        headers=auth_headers,
        json={"actif": False, "nom": "Brief Modifié"},
    )
    assert maj.status_code == 200
    assert maj.json()["actif"] is False
    assert maj.json()["nom"] == "Brief Modifié"

    suppr = client.delete("/api/routines/brief-du-matin", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/routines/brief-du-matin").status_code == 404
