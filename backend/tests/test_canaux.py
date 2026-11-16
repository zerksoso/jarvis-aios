"""Tests de l'orchestrateur de canaux multi-agents : ordre, synthèse, @mentions et plafonds."""

import json
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.agents import agent_runtime, channel_orchestrator
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, LLMRequest, LLMResult


class _MoteurCanalSimule:
    """Moteur simulé aiguillant les réponses selon le prompt et le système du canal."""

    nom = "agy"

    def __init__(self, decideur: Callable[[LLMRequest], str]) -> None:
        self._decideur = decideur
        self.requetes: list[LLMRequest] = []

    async def disponible(self) -> tuple[bool, str]:
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del session_id
        self.requetes.append(req)
        texte = self._decideur(req)
        if on_delta is not None:
            await on_delta(texte)
        return LLMResult(texte=texte, moteur="agy", modele=modele_id, session_id="sess-c")


def _activer(moteur: _MoteurCanalSimule) -> None:
    """Active le moteur simulé avec des quotas confortables."""
    quota.set_quotas_for_tests(
        {
            "buckets": {
                "gemini-5h": {"group": "gemini", "remaining": 0.95},
                "3p-5h": {"group": "3p", "remaining": 0.95},
                "gemini-argon-wtu-weekly": {"group": "argon", "remaining": 0.95},
            }
        }
    )
    router.set_engines_for_tests({"agy": moteur, "claude_cli": moteur})


async def test_coordinateur_choisit_3_agents_puis_synthese_dans_ordre(jarvis_root: Path) -> None:
    """Le coordinateur sélectionne 3 agents qui répondent dans l'ordre, puis produit la synthèse."""
    del jarvis_root
    agent_runtime.initialiser_equipe_initiale()
    channel_orchestrator.initialiser_canaux_initiaux()

    def _decideur(req: LLMRequest) -> str:
        if "Choisis les membres les plus pertinents" in req.systeme:
            return json.dumps(
                {
                    "ordre": ["atlas", "remi", "nova"],
                    "consignes": {
                        "atlas": "Cherche les faits marché.",
                        "remi": "Chiffre la rentabilité.",
                        "nova": "Rédige le pitch.",
                    },
                }
            )
        if "Rédige la synthèse finale structurée" in req.systeme:
            return json.dumps(
                {
                    "reponse": "Synthèse finale de Jarvis : marché porteur, ROI 18 %, pitch prêt.",
                    "memoire": ["Marché validé à +12 %", "ROI cible : 18 %"],
                }
            )
        if "Tu es Atlas" in req.systeme:
            return json.dumps(
                {
                    "pensee": "Veille",
                    "actions": [],
                    "reponse": "Analyse d'Atlas sur le marché.",
                    "termine": True,
                }
            )
        if "Tu es Rémi" in req.systeme:
            return json.dumps(
                {
                    "pensee": "Calcul",
                    "actions": [],
                    "reponse": "Analyse chiffrée de Rémi.",
                    "termine": True,
                }
            )
        if "Tu es Nova" in req.systeme:
            return json.dumps(
                {
                    "pensee": "Création",
                    "actions": [],
                    "reponse": "Proposition créative de Nova.",
                    "termine": True,
                }
            )
        return '{"pensee":"ok","actions":[],"reponse":"Intervention","termine":true}'

    _activer(_MoteurCanalSimule(_decideur))

    resultat = await channel_orchestrator.traiter_message_canal(
        "general",
        "Préparez le lancement de notre nouvelle offre.",
    )
    auteurs_reponses = [r["auteur"] for r in resultat["reponses"]]
    assert auteurs_reponses == ["atlas", "remi", "nova"]
    assert resultat["synthese"]["auteur"] == "jarvis"
    assert resultat["synthese"]["synthese"] is True
    assert "Synthèse finale de Jarvis" in resultat["synthese"]["contenu"]

    # Vérifie l'ordre en base SQLite (utilisateur -> atlas -> remi -> nova -> synthèse jarvis)
    lignes = db.query(
        "SELECT role, auteur, contenu, meta FROM messages WHERE conversation_id = 'canal:general' ORDER BY id ASC"
    )
    assert [r["auteur"] for r in lignes] == [
        "utilisateur",
        "atlas",
        "remi",
        "nova",
        "jarvis",
    ]
    meta_synth = json.loads(lignes[-1]["meta"])
    assert meta_synth["synthese"] is True

    # Vérifie que la mémoire du canal a été enrichie
    memoire_md = channel_orchestrator.lire_memoire_canal("general")
    assert "Marché validé à +12 %" in memoire_md
    assert "ROI cible : 18 %" in memoire_md


async def test_mention_arobase_declenche_un_tour_et_respecte_plafond(jarvis_root: Path) -> None:
    """Une mention @autre déclenche un tour supplémentaire sans jamais dépasser le plafond."""
    del jarvis_root
    agent_runtime.initialiser_equipe_initiale()
    channel_orchestrator.initialiser_canaux_initiaux()

    # Le coordinateur choisit uniquement 'atlas', mais atlas et remi se mentionnent mutuellement en boucle
    def _decideur_mentions(req: LLMRequest) -> str:
        if "Choisis les membres les plus pertinents" in req.systeme:
            return json.dumps({"ordre": ["atlas"], "consignes": {"atlas": "Commence"}})
        if "Rédige la synthèse finale structurée" in req.systeme:
            return json.dumps({"reponse": "Synthèse après mentions.", "memoire": []})
        if "Tu es Atlas" in req.systeme:
            return json.dumps(
                {
                    "pensee": "Interpeller Rémi",
                    "actions": [],
                    "reponse": "Voici mes sources, qu'en penses-tu @remi ?",
                    "termine": True,
                }
            )
        if "Tu es Rémi" in req.systeme:
            return json.dumps(
                {
                    "pensee": "Renvoyer à Atlas",
                    "actions": [],
                    "reponse": "Mes chiffres confirment, merci @atlas !",
                    "termine": True,
                }
            )
        return '{"pensee":"ok","actions":[],"reponse":"OK","termine":true}'

    _activer(_MoteurCanalSimule(_decideur_mentions))

    # Dans #general, max_tours_par_agent = 2 : atlas (1) -> @remi (1) -> @atlas (2) -> @remi (2) -> stop !
    res = await channel_orchestrator.traiter_message_canal("general", "Débattez entre vous")
    auteurs = [r["auteur"] for r in res["reponses"]]
    assert auteurs == ["atlas", "remi", "atlas", "remi"]
    assert auteurs.count("atlas") == 2
    assert auteurs.count("remi") == 2


def test_api_canaux_crud_et_messages(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vérifie GET/POST/PUT/DELETE /api/canaux et GET /api/canaux/{id}/messages."""
    liste = client.get("/api/canaux")
    assert liste.status_code == 200
    ids = [c["id"] for c in liste.json()]
    assert "general" in ids
    assert "veille" in ids

    cree = client.post(
        "/api/canaux",
        headers=auth_headers,
        json={
            "id": "produit",
            "nom": "produit",
            "description": "Canal produit",
            "membres": ["jarvis", "nova", "maya"],
            "coordinateur": "jarvis",
            "max_tours_par_agent": 2,
        },
    )
    assert cree.status_code == 200
    assert cree.json()["id"] == "produit"

    maj = client.put(
        "/api/canaux/produit",
        headers=auth_headers,
        json={"description": "Canal produit & roadmap", "max_tours_par_agent": 3},
    )
    assert maj.status_code == 200
    assert maj.json()["max_tours_par_agent"] == 3

    msgs = client.get("/api/canaux/produit/messages")
    assert msgs.status_code == 200
    assert msgs.json() == []

    suppr = client.delete("/api/canaux/produit", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/canaux/produit").status_code == 404
