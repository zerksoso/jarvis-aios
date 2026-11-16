"""Tests du mode Professeur (repérage UIA/OCR, repli vision set-of-marks, guidage WS) et overlay/test."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.jarvis_engine import ocr, teacher, uia, vision
from backend.jarvis_engine.vision import Capture
from backend.llm import quota, router
from backend.llm.base import DeltaCallback, Engine, LLMRequest, LLMResult
from backend.ws_hub import hub


class _MoteurTeacherSimule:
    """Moteur LLM simulé enregistrant les requêtes (dont les images) pour tester teacher.py."""

    nom = "agy"

    def __init__(self, handler: Callable[[int, LLMRequest], str]) -> None:
        self._handler = handler
        self.requetes: list[LLMRequest] = []
        self.images_vues: list[list[bool]] = []

    async def disponible(self) -> tuple[bool, str]:
        return True, "OK"

    async def generer(
        self,
        req: LLMRequest,
        modele_id: str,
        session_id: str | None,
        on_delta: DeltaCallback,
    ) -> LLMResult:
        del session_id, on_delta
        self.requetes.append(req)
        # Vérifie l'existence effective des images jointes au moment de l'appel moteur.
        self.images_vues.append([Path(p).is_file() for p in req.images])
        texte = self._handler(len(self.requetes), req)
        return LLMResult(texte=texte, moteur="agy", modele=modele_id, session_id="sess-teacher")


def _activer_moteur(moteur: Engine) -> None:
    """Configure les quotas et injecte le moteur simulé dans le routeur LLM."""
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


def _preparer_ecran_simule(monkeypatch: pytest.MonkeyPatch, jarvis_root: Path) -> Capture:
    """Prépare une capture réelle Pillow, des éléments UIA et un OCR simulés."""
    dossier = jarvis_root / "runtime" / "captures"
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / "ecran-prof.png"
    Image.new("RGB", (640, 360), (15, 23, 42)).save(chemin, format="PNG")
    cap = Capture(chemin=chemin, largeur=640, hauteur=360, echelle=2.0, origine_x=0, origine_y=0)

    monkeypatch.setattr(vision, "capturer", lambda ecran=1, largeur_max=1280: cap)
    monkeypatch.setattr(
        uia,
        "elements",
        lambda max=300: [
            {"index": 1, "nom": "Fichier", "role": "MenuItemControl", "rect": {"x": 16, "y": 8, "w": 60, "h": 28}, "actif": True},
            {"index": 2, "nom": "Édition", "role": "MenuItemControl", "rect": {"x": 84, "y": 8, "w": 60, "h": 28}, "actif": True},
        ],
    )
    monkeypatch.setattr(
        ocr,
        "lire",
        lambda _cap: [
            {"texte": "Enregistrer", "rect": {"x": 24, "y": 80, "w": 96, "h": 24}},
        ],
    )
    return cap


async def test_teacher_trouver_choix_candidat_uia_ocr(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trouver() sélectionne le candidat UIA/OCR choisi par le LLM en un seul appel."""
    _preparer_ecran_simule(monkeypatch, jarvis_root)
    moteur = _MoteurTeacherSimule(
        lambda _n, _req: json.dumps({"index": 1, "raison": "Menu Fichier en haut à gauche"})
    )
    _activer_moteur(moteur)

    cible = await teacher.trouver("Ouvrir le menu Fichier")
    assert cible["x"] == 16
    assert cible["y"] == 8
    assert cible["w"] == 60
    assert cible["h"] == 28
    assert cible["label"] == "Fichier"
    assert cible["source"] == "uia"
    assert len(moteur.requetes) == 1
    assert moteur.requetes[0].modele == "hydrogen"
    assert moteur.requetes[0].effort == "low"
    assert moteur.requetes[0].images == []


async def test_teacher_repli_vision_quand_index_invalide(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quand le 1er appel renvoie un index invalide, trouver() annote la capture et bascule en vision."""
    _preparer_ecran_simule(monkeypatch, jarvis_root)

    def _reponse(tour: int, _req: LLMRequest) -> str:
        if tour == 1:
            return json.dumps({"index": 999, "raison": "Index hors bornes"})
        return json.dumps({"index": 3, "raison": "Identifié visuellement sur la pastille 3"})

    moteur = _MoteurTeacherSimule(_reponse)
    _activer_moteur(moteur)

    cible = await teacher.trouver("Cliquer sur Enregistrer")
    assert len(moteur.requetes) == 2
    assert moteur.requetes[0].images == []
    assert len(moteur.requetes[1].images) == 1
    assert moteur.images_vues[1] == [True]
    assert cible["source"] == "vision"
    assert cible["label"] == "Enregistrer"
    assert cible["x"] == 24
    assert cible["y"] == 80


def test_annoter_capture_set_of_marks_pillow(jarvis_root: Path) -> None:
    """Vérifie la génération d'une image annotée PNG valide par annoter_capture_set_of_marks."""
    chemin = jarvis_root / "runtime" / "captures" / "base.png"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 200), (0, 0, 0)).save(chemin, format="PNG")
    cap = Capture(chemin=chemin, largeur=400, hauteur=200, echelle=2.0, origine_x=0, origine_y=0)

    sortie = teacher.annoter_capture_set_of_marks(
        cap,
        [{"index": 1, "x": 20, "y": 10, "w": 100, "h": 40}],
    )
    assert sortie.is_file()
    with Image.open(sortie) as img_annot:
        assert img_annot.size == (400, 200)


async def test_teacher_guider_ne_clique_pas_et_attend_guide_clic(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """guider() diffuse guide, attend guide_clic WS sans cliquer puis diffuse guide_effacer."""
    _preparer_ecran_simule(monkeypatch, jarvis_root)
    moteur = _MoteurTeacherSimule(lambda _n, _req: json.dumps({"index": 1, "raison": "OK"}))
    _activer_moteur(moteur)
    monkeypatch.setattr(teacher, "_demarrer_ecoute_souris", lambda _att: None)

    evenements: list[tuple[str, dict[str, Any]]] = []
    orig_broadcast = hub.broadcast

    async def _espion(msg_type: str, data: dict[str, Any] | None = None, cible: str = "tous") -> None:
        evenements.append((msg_type, dict(data or {})))
        await orig_broadcast(msg_type, data, cible=cible)

    monkeypatch.setattr(hub, "broadcast", _espion)

    tache = asyncio.create_task(
        teacher.guider(
            "Ouvrir Fichier",
            etapes=["Cliquer sur Fichier", "Cliquer sur Enregistrer"],
            timeout_etape_s=5.0,
            guide_id="guide-test-1",
        )
    )

    # Valide l'étape 1 puis l'étape 2 via message WebSocket guide_clic.
    for _ in range(2):
        for _ in range(40):
            if "guide-test-1" in teacher._guides_actifs:
                break
            await asyncio.sleep(0.01)
        await hub.handle_client_message(
            None,  # type: ignore[arg-type]
            {"type": "guide_clic", "data": {"id": "guide-test-1", "index": 0}},
        )
        # Laisse guider() passer à l'étape suivante avant le clic suivant (comme un humain).
        await asyncio.sleep(0.3)

    res = await tache
    assert res["ok"] is True
    assert res["etapes_completees"] == 2
    guides = [d for t, d in evenements if t == "guide"]
    effaces = [d for t, d in evenements if t == "guide_effacer"]
    assert len(guides) == 2
    assert guides[0]["attendre_clic"] is True
    assert guides[0]["boites"][0]["label"].startswith("1/2")
    assert guides[1]["boites"][0]["label"].startswith("2/2")
    assert effaces == [{"id": "guide-test-1"}]


def test_api_professeur_et_overlay_test(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie /api/professeur/trouver, /api/overlay/test et /api/professeur/guider avec guide_clic WS."""
    _preparer_ecran_simule(monkeypatch, jarvis_root)
    moteur = _MoteurTeacherSimule(lambda _n, _req: json.dumps({"index": 1, "raison": "OK"}))
    _activer_moteur(moteur)
    monkeypatch.setattr(teacher, "_demarrer_ecoute_souris", lambda _att: None)

    r_trouver = client.post(
        "/api/professeur/trouver",
        headers=auth_headers,
        json={"objectif": "menu Fichier"},
    )
    assert r_trouver.status_code == 200
    assert r_trouver.json()["label"] == "Fichier"

    with client.websocket_connect(f"/ws?token={token}&client=overlay") as ws:
        # Ignore le broadcast initial overlay_statut.
        premier = ws.receive_json()
        assert premier["type"] == "overlay_statut"

        r_test = client.post("/api/overlay/test", headers=auth_headers)
        assert r_test.status_code == 200
        assert r_test.json()["ok"] is True

        recus = [ws.receive_json() for _ in range(3)]
        types = [m["type"] for m in recus]
        assert types == ["guide", "narration", "carte"]
        assert recus[0]["data"]["boites"][0]["label"] == "Test du calque · 1/1"
        assert recus[0]["data"]["attendre_clic"] is False
        assert recus[2]["data"]["titre"] == "Test du calque HUD"

        r_guider = client.post(
            "/api/professeur/guider",
            headers=auth_headers,
            json={"objectif": "menu Fichier"},
        )
        assert r_guider.status_code == 200
        gid = r_guider.json()["guide_id"]
        assert gid.startswith("guide-")

        # Attend l'émission de la boîte de guidage avant de valider le clic via WebSocket.
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "guide" and msg["data"]["id"] == gid:
                break
        else:
            pytest.fail("Message WS guide non reçu après /api/professeur/guider")

        ws.send_json({"type": "guide_clic", "data": {"id": gid, "index": 0}})

        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "guide_effacer" and msg["data"]["id"] == gid:
                break
        else:
            pytest.fail("Message WS guide_effacer non reçu après guide_clic")
