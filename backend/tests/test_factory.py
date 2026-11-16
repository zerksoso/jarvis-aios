"""Tests de la fabrique d'agents en langage naturel (factory.py et outil agent.creer)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.agents import factory
from backend.aios import skills_engine, tools_registry
from backend.aios.tools_registry import ContexteOutil
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult


class _MoteurFactorySimule:
    """Moteur simulé retournant une fiche JSON d'agent avec outils/skills mixtes."""

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
        """Retourne un JSON d'agent incluant un outil et un skill inexistants à filtrer."""
        del req, session_id, on_delta
        payload = {
            "id": "Plume Juridique",
            "nom": "Plume",
            "titre": "Juriste & Contrats",
            "avatar": {
                "forme": "diamant",
                "couleur": "#22d3ee",
                "yeux": "fentes",
            },
            "instructions": "Tu es Plume, juriste spécialisée dans la relecture de contrats.",
            "modele_principal": "hydrogen",
            "effort_raisonnement": "high",
            "modele_secours": "claude-sonnet",
            "skills_assignes": ["audit-contrat", "skill-imaginaire"],
            "acces_second_cerveau": True,
            "outils": ["cerveau.chercher", "cerveau.lire", "outil.inexistant"],
        }
        return LLMResult(
            texte=json.dumps(payload, ensure_ascii=False),
            moteur="agy",
            modele=modele_id,
        )


async def test_creer_agent_depuis_texte_fichier_valide_et_id_unique(jarvis_root: Path) -> None:
    """Vérifie la génération d'un fichier JSON valide, le filtrage outils/skills et l'unicité d'id."""
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.9}}})
    engines: dict[str, Engine] = {"agy": _MoteurFactorySimule()}
    router.set_engines_for_tests(engines)

    skills_engine.creer_skill(
        nom="audit-contrat",
        description="Relit un contrat",
        declencheurs=["contrat"],
        outils=["cerveau.lire"],
        etapes=["Lire le contrat", "Lister les clauses à risque"],
    )

    a1 = await factory.creer_agent_depuis_texte("Un juriste nommé Plume qui relit mes contrats")
    assert a1["id"] == "plume-juridique"
    assert a1["nom"] == "Plume"
    assert a1["avatar"]["forme"] == "diamant"
    assert a1["avatar"]["etat"] == "libre"
    # Seuls les skills et outils existants sont conservés
    assert a1["skills_assignes"] == ["audit-contrat"]
    assert a1["outils"] == ["cerveau.chercher", "cerveau.lire"]

    fichier_1 = jarvis_root / "agents" / "plume-juridique.json"
    assert fichier_1.is_file()

    # Un second appel avec le même nom/id génère un slug unique suffixé (-2)
    a2 = await factory.creer_agent_depuis_texte("Encore Plume")
    assert a2["id"] == "plume-juridique-2"
    assert (jarvis_root / "agents" / "plume-juridique-2.json").is_file()

    # Vérifie aussi l'outil AIOS agent.creer
    async def _accord_oui(r: str, a: dict[str, object], s: str) -> bool:
        del r, a, s
        return True

    ctx = ContexteOutil(
        agent_id="jarvis",
        conversation_id="agent:jarvis",
        acces_cerveau=True,
        demander_accord=_accord_oui,
    )
    res_outil = await tools_registry.executer(
        "agent.creer",
        {"description": "Troisième Plume"},
        ctx,
    )
    assert res_outil["ok"] is True
    assert res_outil["resultat"]["id"] == "plume-juridique-3"


def test_api_creer_agent_depuis_texte(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /api/agents/creer-depuis-texte crée et retourne la fiche de l'agent."""
    quota.set_quotas_for_tests({"buckets": {"gemini-5h": {"group": "gemini", "remaining": 0.9}}})
    router.set_engines_for_tests({"agy": _MoteurFactorySimule()})

    resp = client.post(
        "/api/agents/creer-depuis-texte",
        headers=auth_headers,
        json={"description": "Un juriste pour mes contrats"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "plume-juridique"
    assert data["nom"] == "Plume"
