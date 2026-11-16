"""Tests du runtime des agents : boucle outils, états, approbations, persistance et souvenirs."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import db, settings_store
from backend.agents import agent_runtime
from backend.agents.models import Agent
from backend.brain import indexer
from backend.jarvis_engine import approvals
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult
from backend.ws_hub import hub


class _MoteurProgrammable:
    """Moteur simulé appelant une fonction de rappel par tour pour piloter les tests."""

    nom = "agy"

    def __init__(
        self,
        fn: Callable[[int, LLMRequest, DeltaCallback], str | Awaitable[str]],
    ) -> None:
        self._fn = fn
        self.prompts: list[str] = []

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
        self.prompts.append(req.prompt)
        idx = len(self.prompts)
        res = self._fn(idx, req, on_delta)
        texte = await res if asyncio.iscoroutine(res) else str(res)
        return LLMResult(texte=texte, moteur="agy", modele=modele_id, session_id="sess-ag")


def _activer_moteur(moteur: Engine) -> None:
    """Configure les quotas et injecte le moteur simulé dans le routeur."""
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


async def test_reponse_simple_et_etats_diffuses_travaille_fini(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie une réponse JSON simple, le streaming de 'reponse' et les états travaille -> fini."""
    del jarvis_root
    etats_recus: list[tuple[str, str]] = []
    broadcast_orig = hub.broadcast

    async def _espion_broadcast(
        msg_type: str,
        data: dict[str, Any] | None = None,
        cible: str = "tous",
    ) -> None:
        if msg_type == "agent_etat" and isinstance(data, dict):
            etats_recus.append((str(data.get("id")), str(data.get("etat"))))
        await broadcast_orig(msg_type, data, cible=cible)

    monkeypatch.setattr(hub, "broadcast", _espion_broadcast)

    async def _scenario(idx: int, req: LLMRequest, on_delta: DeltaCallback) -> str:
        del idx, req
        morceaux = [
            '{"pensee": "Saluer", "actions": [], "reponse": "Bonjour ',
            'depuis Jarvis !", "termine": true}',
        ]
        for m in morceaux:
            if on_delta is not None:
                await on_delta(m)
        return "".join(morceaux)

    _activer_moteur(_MoteurProgrammable(_scenario))
    deltas: list[str] = []

    async def _sur_delta(d: str) -> None:
        deltas.append(d)

    rep = await agent_runtime.repondre(
        "jarvis",
        "agent:jarvis",
        "Bonjour",
        on_delta=_sur_delta,
    )
    assert rep == "Bonjour depuis Jarvis !"
    assert "".join(deltas) == "Bonjour depuis Jarvis !"
    assert ("jarvis", "travaille") in etats_recus
    assert ("jarvis", "fini") in etats_recus
    idx_travaille = etats_recus.index(("jarvis", "travaille"))
    idx_fini = etats_recus.index(("jarvis", "fini"))
    assert idx_travaille < idx_fini


async def test_action_outil_cerveau_chercher_puis_reponse_finale(jarvis_root: Path) -> None:
    """L'agent appelle cerveau.chercher au tour 1, reçoit les résultats et répond au tour 2."""
    note_dir = jarvis_root / "second-brain" / "01-Business-Projets"
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / "alpha.md").write_text(
        "---\ntitre: Projet Alpha\nstatut: actif\ndate: 2026-11-16\n---\n"
        "Le budget validé du Projet Alpha est de 48 000 euros.\n",
        encoding="utf-8",
    )
    indexer.reindexer_tout()

    def _scenario(idx: int, req: LLMRequest, on_delta: DeltaCallback) -> str:
        del on_delta
        if idx == 1:
            return json.dumps(
                {
                    "pensee": "Je cherche la note Projet Alpha dans le cerveau.",
                    "actions": [{"outil": "cerveau.chercher", "args": {"q": "Alpha"}}],
                    "reponse": "",
                    "termine": False,
                }
            )
        assert "Résultats des outils :" in req.prompt
        assert "Projet Alpha" in req.prompt
        return json.dumps(
            {
                "pensee": "J'ai trouvé la note.",
                "actions": [],
                "reponse": "Le budget du Projet Alpha est de 48 000 euros.",
                "termine": True,
            }
        )

    moteur = _MoteurProgrammable(_scenario)
    _activer_moteur(moteur)

    rep = await agent_runtime.repondre(
        "jarvis",
        "agent:jarvis",
        "Quel est le budget du projet Alpha ?",
    )
    assert rep == "Le budget du Projet Alpha est de 48 000 euros."
    assert len(moteur.prompts) == 2


async def test_json_invalide_retourne_texte_brut_et_persiste_messages(jarvis_root: Path) -> None:
    """Un texte non-JSON renvoyé par le modèle est utilisé tel quel et persisté dans messages."""
    del jarvis_root
    _activer_moteur(
        _MoteurProgrammable(lambda idx, req, cb: "Réponse brute sans JSON valide.")
    )

    rep = await agent_runtime.repondre("jarvis", "agent:jarvis", "Question directe")
    assert rep == "Réponse brute sans JSON valide."

    lignes = db.query(
        "SELECT role, auteur, contenu FROM messages WHERE conversation_id = 'agent:jarvis' ORDER BY id ASC"
    )
    assert len(lignes) == 2
    assert lignes[0] == {
        "role": "user",
        "auteur": "utilisateur",
        "contenu": "Question directe",
    }
    assert lignes[1] == {
        "role": "assistant",
        "auteur": "jarvis",
        "contenu": "Réponse brute sans JSON valide.",
    }


async def test_limite_8_tours_max_par_message(jarvis_root: Path) -> None:
    """Si le modèle demande indéfiniment des outils, la boucle s'arrête exactement à 8 tours."""
    del jarvis_root

    def _boucle_infinie(idx: int, req: LLMRequest, on_delta: DeltaCallback) -> str:
        del req, on_delta
        return json.dumps(
            {
                "pensee": f"Tour {idx}",
                "actions": [{"outil": "taches.lister", "args": {}}],
                "reponse": f"Réponse partielle tour {idx}",
                "termine": False,
            }
        )

    moteur = _MoteurProgrammable(_boucle_infinie)
    _activer_moteur(moteur)

    rep = await agent_runtime.repondre("jarvis", "agent:jarvis", "Boucle infinie")
    assert len(moteur.prompts) == 8
    assert rep == "Réponse partielle tour 8"


async def test_outil_irreversible_approbation_acceptee_refusee_expiree(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valide l'exécution d'un outil irréversible selon que l'approbation est acceptée, refusée ou expirée."""
    scripts_dir = jarvis_root / "aios" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "demo.py").write_text("print('EXECUTION_OK_99')", encoding="utf-8")

    def _scenario_script(idx: int, req: LLMRequest, on_delta: DeltaCallback) -> str:
        del on_delta
        if idx == 1:
            return json.dumps(
                {
                    "pensee": "Lancer demo.py",
                    "actions": [{"outil": "script.executer", "args": {"nom": "demo.py"}}],
                    "reponse": "",
                    "termine": False,
                }
            )
        return json.dumps(
            {
                "pensee": "Terminé",
                "actions": [],
                "reponse": f"Bilan : {req.prompt}",
                "termine": True,
            }
        )

    # 1. Approbation acceptée
    moteur_oui = _MoteurProgrammable(_scenario_script)
    _activer_moteur(moteur_oui)

    async def _repondre_oui_auto() -> None:
        # Attente large (CI lente) : l'approbation peut être listée juste avant le passage à « attend_accord ».
        for _ in range(500):
            await asyncio.sleep(0.01)
            att = approvals.lister_approbations("en_attente")
            if att and agent_runtime.etat_agent("jarvis") == "attend_accord":
                await approvals.repondre_approbation(att[0]["id"], "oui")
                return

    tache_dec_oui = asyncio.create_task(_repondre_oui_auto())
    rep_oui = await agent_runtime.repondre("jarvis", "agent:jarvis", "Lance demo.py")
    await tache_dec_oui
    assert "EXECUTION_OK_99" in rep_oui

    # 2. Approbation refusée
    moteur_non = _MoteurProgrammable(_scenario_script)
    _activer_moteur(moteur_non)

    async def _repondre_non_auto() -> None:
        for _ in range(500):
            await asyncio.sleep(0.01)
            att = approvals.lister_approbations("en_attente")
            if att:
                await approvals.repondre_approbation(att[0]["id"], "non")
                return

    tache_dec_non = asyncio.create_task(_repondre_non_auto())
    rep_non = await agent_runtime.repondre("jarvis", "agent:jarvis", "Lance demo.py")
    await tache_dec_non
    assert "Refusé par l'utilisateur" in rep_non

    # 3. Approbation expirée
    moteur_exp = _MoteurProgrammable(_scenario_script)
    _activer_moteur(moteur_exp)
    demander_orig = approvals.demander_accord

    async def _demander_court(
        source: str,
        resume: str,
        action: dict[str, Any],
        risque: str,
        timeout_s: float = 0.02,
    ) -> bool:
        del timeout_s
        return await demander_orig(source, resume, action, risque, timeout_s=0.02)

    monkeypatch.setattr(approvals, "demander_accord", _demander_court)
    rep_exp = await agent_runtime.repondre("jarvis", "agent:jarvis", "Lance demo.py")
    assert "Refusé par l'utilisateur" in rep_exp
    assert len(approvals.lister_approbations("expiree")) >= 1


async def test_extraction_souvenirs_declenchee_au_6e_message(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'extraction automatique de souvenirs est déclenchée en tâche de fond au 6e message utilisateur."""
    del jarvis_root
    settings_store.ecrire({"memoire_auto": True})

    appels_extraction: list[str] = []

    async def _fausse_extraction(conv_id: str) -> list[dict[str, Any]]:
        appels_extraction.append(conv_id)
        return []

    monkeypatch.setattr(agent_runtime, "extraire_souvenirs", _fausse_extraction)

    _activer_moteur(
        _MoteurProgrammable(
            lambda idx, req, cb: '{"pensee":"ok","actions":[],"reponse":"Noté.","termine":true}'
        )
    )

    for i in range(1, 7):
        await agent_runtime.repondre("jarvis", "agent:jarvis", f"Message utilisateur {i}")
        await asyncio.sleep(0.01)
        if i < 6:
            assert appels_extraction == []

    await agent_runtime.attendre_taches_fond()
    assert appels_extraction == ["agent:jarvis"]


def test_api_agents_crud_et_protection_jarvis(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vérifie GET/POST/PUT/DELETE /api/agents et l'interdiction de supprimer jarvis."""
    liste_init = client.get("/api/agents")
    assert liste_init.status_code == 200
    ids_init = [a["id"] for a in liste_init.json()]
    assert {"jarvis", "atlas", "nova", "maya", "remi"}.issubset(set(ids_init))

    # Jarvis est protégé contre la suppression
    del_jarvis = client.delete("/api/agents/jarvis", headers=auth_headers)
    assert del_jarvis.status_code == 403

    cree = client.post(
        "/api/agents",
        headers=auth_headers,
        json={
            "id": "echo",
            "nom": "Écho",
            "titre": "Synthétiseur",
            "instructions": "Tu résumes les réunions.",
        },
    )
    assert cree.status_code == 200
    assert cree.json()["id"] == "echo"

    maj = client.put(
        "/api/agents/echo",
        headers=auth_headers,
        json={"titre": "Synthétiseur Senior"},
    )
    assert maj.status_code == 200
    assert maj.json()["titre"] == "Synthétiseur Senior"

    suppr = client.delete("/api/agents/echo", headers=auth_headers)
    assert suppr.status_code == 200
    assert client.get("/api/agents/echo").status_code == 404


def test_empreinte_agent_stable_et_sensible_aux_changements() -> None:
    """Vérifie que l'empreinte est stable hors champs volatils et varie sur les champs stables."""
    base = Agent(
        id="demo",
        nom="Démo",
        titre="Analyste",
        instructions="Réponds avec précision.",
        modele_principal="hydrogen",
        effort_raisonnement="medium",
        skills_assignes=["veille-web"],
        outils=["cerveau.chercher"],
        acces_second_cerveau=True,
        souvenirs=["Préfère les tableaux"],
    )

    emp_init = agent_runtime.empreinte_agent(base, nb_souvenirs_globaux=4)
    assert len(emp_init) == 8
    assert all(c in "0123456789abcdef" for c in emp_init)
    assert emp_init == agent_runtime.empreinte_agent(base, nb_souvenirs_globaux=4)

    # Les champs volatils (avatar.etat, fichiers_generes) ne doivent pas changer l'empreinte.
    volatil = base.model_copy(deep=True)
    volatil.avatar.etat = "travaille"
    volatil.fichiers_generes = ["rapport.md"]
    assert agent_runtime.empreinte_agent(volatil, nb_souvenirs_globaux=4) == emp_init

    # Un changement d'instructions ou de nb_souvenirs_globaux doit produire une autre empreinte.
    modif_instr = base.model_copy(deep=True)
    modif_instr.instructions = "Réponds en trois points."
    assert agent_runtime.empreinte_agent(modif_instr, nb_souvenirs_globaux=4) != emp_init

    assert agent_runtime.empreinte_agent(base, nb_souvenirs_globaux=5) != emp_init

    modif_souv = base.model_copy(deep=True)
    modif_souv.souvenirs = ["Préfère les tableaux", "Toujours citer les sources"]
    assert agent_runtime.empreinte_agent(modif_souv, nb_souvenirs_globaux=4) != emp_init


async def test_recapitulatif_nouvelle_session_et_session_existante(jarvis_root: Path) -> None:
    """Injecte le récap des 10 derniers messages si pas de session, puis l'omet si elle existe."""
    del jarvis_root
    agent_runtime.initialiser_equipe_initiale()

    db.execute(
        """
        INSERT INTO conversations (id, type, cible_id, titre, cree_le)
        VALUES ('agent:jarvis', 'agent', 'jarvis', 'jarvis', '2026-11-16T09:00:00')
        """
    )
    long_texte = "Long-" + ("A" * 550)
    for idx in range(1, 13):
        role = "user" if idx % 2 == 1 else "assistant"
        auteur = "utilisateur" if role == "user" else "jarvis"
        contenu = long_texte if idx == 3 else f"Message historique {idx}"
        db.execute(
            """
            INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
            VALUES ('agent:jarvis', ?, ?, ?, '{}', '2026-11-16T09:01:00')
            """,
            (role, auteur, contenu),
        )

    requetes: list[LLMRequest] = []

    def _scenario(idx: int, req: LLMRequest, on_delta: DeltaCallback) -> str:
        del idx, on_delta
        requetes.append(req)
        return json.dumps(
            {
                "pensee": "ok",
                "actions": [],
                "reponse": f"Réponse {len(requetes)}",
                "termine": True,
            }
        )

    moteur = _MoteurProgrammable(_scenario)
    _activer_moteur(moteur)

    # 1. Aucune ligne dans sessions_moteur -> le récapitulatif des 10 derniers échanges est injecté.
    rep1 = await agent_runtime.repondre(
        "jarvis",
        "agent:jarvis",
        "Question après nouvelle empreinte",
    )
    assert rep1 == "Réponse 1"

    agent_init = agent_runtime.charger_agent("jarvis")
    assert agent_init is not None
    emp1 = agent_runtime.empreinte_agent(agent_init, 0)
    assert requetes[0].session_cle == f"agent:jarvis#{emp1}"

    prompt1 = requetes[0].prompt
    assert prompt1.startswith("Contexte des derniers échanges (nouvelle session) :\n")
    assert "- utilisateur : Message historique 1" not in prompt1.splitlines()
    assert "- jarvis : Message historique 2" not in prompt1
    assert f"- utilisateur : {'Long-' + ('A' * 494)}…" in prompt1
    assert "- jarvis : Message historique 12" in prompt1
    assert prompt1.index("Long-") < prompt1.index("Message historique 12")
    assert "- utilisateur : Question après nouvelle empreinte" not in prompt1
    assert prompt1.endswith("\n\nQuestion après nouvelle empreinte")

    # 2. La session existe désormais dans sessions_moteur -> aucun récapitulatif injecté.
    rep2 = await agent_runtime.repondre(
        "jarvis",
        "agent:jarvis",
        "Question suivante même session",
    )
    assert rep2 == "Réponse 2"
    assert requetes[1].session_cle == f"agent:jarvis#{emp1}"
    assert "Contexte des derniers échanges (nouvelle session) :" not in requetes[1].prompt
    assert requetes[1].prompt == "Question suivante même session"

    # 3. Modification de l'agent -> nouvelle empreinte -> nouvelle session avec récapitulatif.
    donnees_maj = agent_init.model_dump()
    donnees_maj["instructions"] = agent_init.instructions + "\nSois encore plus synthétique."
    agent_maj = agent_runtime.enregistrer_agent(donnees_maj, ecraser=True)
    emp2 = agent_runtime.empreinte_agent(agent_maj, 0)
    assert emp2 != emp1

    rep3 = await agent_runtime.repondre(
        "jarvis",
        "agent:jarvis",
        "Question après modification agent",
    )
    assert rep3 == "Réponse 3"
    assert requetes[2].session_cle == f"agent:jarvis#{emp2}"
    assert requetes[2].prompt.startswith("Contexte des derniers échanges (nouvelle session) :\n")
    assert "- utilisateur : Question suivante même session" in requetes[2].prompt
    assert "- jarvis : Réponse 2" in requetes[2].prompt
    assert requetes[2].prompt.endswith("\n\nQuestion après modification agent")


async def test_recapitulatif_non_injecte_pour_canal(jarvis_root: Path) -> None:
    """Vérifie qu'un appel sur une conversation canal ne préfixe pas le prompt avec le récap."""
    del jarvis_root
    agent_runtime.initialiser_equipe_initiale()
    db.execute(
        """
        INSERT INTO conversations (id, type, cible_id, titre, cree_le)
        VALUES ('canal:general', 'canal', 'general', 'general', '2026-11-16T09:00:00')
        """
    )
    db.execute(
        """
        INSERT INTO messages (conversation_id, role, auteur, contenu, meta, cree_le)
        VALUES ('canal:general', 'user', 'utilisateur', 'Ancien message canal', '{}', '2026-11-16T09:01:00')
        """
    )

    moteur = _MoteurProgrammable(
        lambda idx, req, cb: '{"pensee":"ok","actions":[],"reponse":"OK canal","termine":true}'
    )
    _activer_moteur(moteur)

    rep = await agent_runtime.repondre(
        "jarvis",
        "canal:general",
        "Nouveau message canal",
        historique_canal="FIL RÉCENT DU CANAL :\n[utilisateur] : Ancien message canal",
        _persister_user=False,
        _session_cle_override="canal:general:jarvis",
    )
    assert rep == "OK canal"
    assert "Contexte des derniers échanges (nouvelle session) :" not in moteur.prompts[0]
    assert moteur.prompts[0] == "Nouveau message canal"
