"""Tests du système d'approbations : demander_accord, API REST, WebSocket et expiration."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from backend import db
from backend.jarvis_engine import approvals
from backend.ws_hub import hub


async def test_demander_accord_accepte_et_refuse_et_expire(jarvis_root: Path) -> None:
    """Vérifie le cycle complet demander_accord : acceptation, refus et expiration par timeout."""
    del jarvis_root

    # 1. Acceptation
    tache_oui = asyncio.create_task(
        approvals.demander_accord(
            source="jarvis",
            resume="Exécuter salut.py",
            action={"outil": "script.executer", "args": {"nom": "salut.py"}},
            risque="irreversible",
            timeout_s=5.0,
        )
    )
    await asyncio.sleep(0.02)

    en_attente = approvals.lister_approbations("en_attente")
    assert len(en_attente) == 1
    app_id = en_attente[0]["id"]
    assert en_attente[0]["source"] == "jarvis"
    assert en_attente[0]["action"]["outil"] == "script.executer"

    await approvals.repondre_approbation(app_id, "oui")
    assert await tache_oui is True

    ligne_oui = db.query_one("SELECT statut FROM approbations WHERE id = ?", (app_id,))
    assert ligne_oui == {"statut": "autorisee"}

    # 2. Refus
    tache_non = asyncio.create_task(
        approvals.demander_accord(
            source="atlas",
            resume="Supprimer brouillon",
            action={"outil": "fichier.supprimer"},
            risque="irreversible",
            timeout_s=5.0,
        )
    )
    await asyncio.sleep(0.02)
    en_attente_non = approvals.lister_approbations("en_attente")
    assert len(en_attente_non) == 1
    app_id_non = en_attente_non[0]["id"]

    await approvals.repondre_approbation(app_id_non, "non")
    assert await tache_non is False

    ligne_non = db.query_one("SELECT statut FROM approbations WHERE id = ?", (app_id_non,))
    assert ligne_non == {"statut": "refusee"}

    # 3. Expiration
    res_expire = await approvals.demander_accord(
        source="jarvis",
        resume="Action sans réponse",
        action={"outil": "script.executer"},
        risque="irreversible",
        timeout_s=0.03,
    )
    assert res_expire is False
    expirees = approvals.lister_approbations("expiree")
    assert len(expirees) == 1
    assert expirees[0]["statut"] == "expiree"


async def test_approbation_resolution_via_message_ws_client(jarvis_root: Path) -> None:
    """Un message client WebSocket approbation_reponse débloque demander_accord."""
    del jarvis_root
    tache = asyncio.create_task(
        approvals.demander_accord(
            source="jarvis",
            resume="Validation via WS",
            action={"outil": "script.executer"},
            risque="irreversible",
            timeout_s=5.0,
        )
    )
    await asyncio.sleep(0.02)

    attente = approvals.lister_approbations("en_attente")
    assert len(attente) == 1
    app_id = attente[0]["id"]

    await hub.handle_client_message(
        None,  # type: ignore[arg-type]
        {"type": "approbation_reponse", "data": {"id": app_id, "decision": "oui"}},
    )
    assert await tache is True


def test_api_approbations_get_et_post(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vérifie GET /api/approbations?statut=en_attente et POST /api/approbations/{id}."""
    db.execute(
        """
        INSERT INTO approbations (id, source, action, resume, risque, statut, cree_le)
        VALUES ('apr-test-1', 'jarvis', '{"outil":"script.executer"}', 'Test API', 'irreversible', 'en_attente', '2026-11-16T09:00:00')
        """
    )

    resp_get = client.get("/api/approbations?statut=en_attente")
    assert resp_get.status_code == 200
    liste = resp_get.json()
    assert len(liste) == 1
    assert liste[0]["id"] == "apr-test-1"
    assert liste[0]["action"] == {"outil": "script.executer"}

    resp_post = client.post(
        "/api/approbations/apr-test-1",
        headers=auth_headers,
        json={"decision": "oui"},
    )
    assert resp_post.status_code == 200
    assert resp_post.json()["statut"] == "autorisee"

    assert client.get("/api/approbations?statut=en_attente").json() == []
    assert client.post(
        "/api/approbations/inconnue",
        headers=auth_headers,
        json={"decision": "non"},
    ).status_code == 404
