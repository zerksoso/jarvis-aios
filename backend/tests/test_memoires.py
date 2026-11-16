"""Tests de la mémoire persistante : déduplication, SOUVENIRS.md, API et extraction auto."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend import db, settings_store
from backend.brain import memories
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult


def test_deduplication_et_regeneration_souvenirs_md(jarvis_root: Path) -> None:
    """Un souvenir identique (casse/accents/ponctuation) est dédupliqué et SOUVENIRS.md est à jour."""
    s1 = memories.ajouter_souvenir(
        "preference",
        "Répondre toujours en français !",
        source="test",
        agent="jarvis",
    )
    assert s1 is not None

    # Doublon normalisé ignoré
    doublon = memories.ajouter_souvenir(
        "preference",
        "repondre toujours en francais",
        source="test",
        agent="jarvis",
    )
    assert doublon is None

    liste = memories.lister_souvenirs()
    assert len(liste) == 1

    souvenirs_md = jarvis_root / "second-brain" / "SOUVENIRS.md"
    assert souvenirs_md.is_file()
    contenu = souvenirs_md.read_text(encoding="utf-8")
    assert "tags: [souvenirs]" in contenu
    assert "Répondre toujours en français !" in contenu

    assert memories.supprimer_souvenir(s1["id"]) is True
    assert memories.lister_souvenirs() == []


def test_api_memoires_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    """GET, POST et DELETE /api/memoires manipulent memories.json et SOUVENIRS.md."""
    post = client.post(
        "/api/memoires",
        headers=auth_headers,
        json={"type": "regle", "texte": "Ne jamais supprimer sans accord"},
    )
    assert post.status_code == 200
    mem_id = post.json()["id"]

    get_resp = client.get("/api/memoires")
    assert get_resp.status_code == 200
    assert len(get_resp.json()) == 1

    suppr = client.delete(f"/api/memoires/{mem_id}", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/memoires").json() == []


class _MoteurExtractionSimule:
    """Moteur simulé retournant un JSON de souvenirs extraits."""

    nom = "agy"

    async def disponible(self) -> tuple[bool, str]:
        """Toujours disponible."""
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        """Retourne deux souvenirs JSON dont un doublon potentiel."""
        del req, session_id, on_delta
        return LLMResult(
            texte='{"souvenirs":[{"type":"preference","texte":"Utiliser des listes courtes"},{"type":"fait","texte":"Travaille sur Jarvis AIOS"}]}',
            moteur="agy",
            modele=modele_id,
        )


async def test_extraction_souvenirs_avec_moteur_simule(jarvis_root: Path) -> None:
    """extraire_souvenirs lit les 12 derniers messages et ajoute les souvenirs dédupliqués."""
    del jarvis_root
    settings_store.ecrire({"memoire_auto": True})
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.9}}})
    engines: dict[str, Engine] = {"agy": _MoteurExtractionSimule()}
    router.set_engines_for_tests(engines)

    db.execute(
        "INSERT INTO conversations (id, type, cible_id, titre, cree_le) VALUES ('agent:jarvis', 'agent', 'jarvis', 'jarvis', '2026-11-16T09:00:00')"
    )
    db.execute(
        "INSERT INTO messages (conversation_id, role, auteur, contenu, cree_le) VALUES ('agent:jarvis', 'user', 'utilisateur', 'Fais des listes courtes stp', '2026-11-16T09:01:00')"
    )

    ajoutes = await memories.extraire_souvenirs("agent:jarvis")
    assert len(ajoutes) == 2

    # Une seconde exécution déduplique automatiquement
    ajoutes_bis = await memories.extraire_souvenirs("agent:jarvis")
    assert ajoutes_bis == []
