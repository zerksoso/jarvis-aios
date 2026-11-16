"""Tests d'exécution sécurisée PC (hands.py), outils ecran/fichier et routes API Jarvis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.aios import tools_registry
from backend.aios.tools_registry import ContexteOutil
from backend.jarvis_engine import approvals, hands, uia, vision
from backend.jarvis_engine.vision import Capture
from backend.ws_hub import hub


def _creer_capture_factice(dossier: Path, nom: str, couleur: tuple[int, int, int]) -> Capture:
    """Crée une petite image PNG factice et retourne son objet ``Capture``."""
    dossier.mkdir(parents=True, exist_ok=True)
    chemin = dossier / nom
    img = Image.new("RGB", (160, 90), couleur)
    img.save(chemin, format="PNG")
    return Capture(chemin=chemin, largeur=160, hauteur=90, echelle=1.0, origine_x=0, origine_y=0)


def _simuler_environnement_hands(
    monkeypatch: pytest.MonkeyPatch,
    jarvis_root: Path,
    fenetre: dict[str, Any],
    element: dict[str, Any] | None = None,
) -> tuple[list[tuple[str, Any]], list[tuple[str, dict[str, Any]]]]:
    """Simule captures, UIA, fonctions bas niveau ``hands._*`` et espionne les broadcasts WS."""
    captures_dir = jarvis_root / "runtime" / "captures"
    compteur = {"n": 0}
    couleurs = [(20, 30, 40), (200, 210, 220)]

    def _fake_capturer(ecran: int = 1, largeur_max: int = 1280) -> Capture:
        del ecran, largeur_max
        idx = compteur["n"]
        compteur["n"] += 1
        coul = couleurs[min(idx, len(couleurs) - 1)]
        return _creer_capture_factice(captures_dir, f"sim-{idx}.png", coul)

    monkeypatch.setattr(vision, "capturer", _fake_capturer)
    monkeypatch.setattr(uia, "fenetre_active", lambda: dict(fenetre))
    monkeypatch.setattr(uia, "element_au_point", lambda _x, _y: element)
    monkeypatch.setattr(uia, "element_focus", lambda: element)

    appels: list[tuple[str, Any]] = []
    monkeypatch.setattr(hands, "_cliquer", lambda x, y, b="left": appels.append(("cliquer", (x, y, b))))
    monkeypatch.setattr(
        hands,
        "_double_cliquer",
        lambda x, y, b="left": appels.append(("double_cliquer", (x, y, b))),
    )
    monkeypatch.setattr(hands, "_taper", lambda texte: appels.append(("taper", texte)))
    monkeypatch.setattr(hands, "_touche", lambda comb: appels.append(("touche", comb)))
    monkeypatch.setattr(hands, "_defiler", lambda dy, x=None, y=None: appels.append(("defiler", (dy, x, y))))
    monkeypatch.setattr(hands, "_ouvrir_application", lambda nom: appels.append(("ouvrir_application", nom)))

    ws_events: list[tuple[str, dict[str, Any]]] = []
    orig_broadcast = hub.broadcast

    async def _espion(msg_type: str, data: dict[str, Any] | None = None, cible: str = "tous") -> None:
        ws_events.append((msg_type, dict(data or {})))
        await orig_broadcast(msg_type, data, cible=cible)

    monkeypatch.setattr(hub, "broadcast", _espion)
    return appels, ws_events


async def test_hands_refuse_si_fenetre_active_ne_correspond_pas(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'action est refusée avant exécution si ``attendu`` ne correspond pas à la fenêtre active."""
    appels, ws_events = _simuler_environnement_hands(
        monkeypatch,
        jarvis_root,
        fenetre={"titre": "Calculatrice", "processus": "calc.exe", "pid": 42, "rect": {"x": 0, "y": 0, "w": 300, "h": 400}},
        element={"nom": "Fichier", "role": "MenuItemControl", "rect": {"x": 10, "y": 10, "w": 40, "h": 20}},
    )

    res = await hands.executer_action(
        {"type": "cliquer", "x": 20, "y": 20},
        attendu={"titre_contient": "Bloc-notes"},
    )
    assert res["ok"] is False
    assert "inattendue" in res["erreur"].lower()
    assert appels == []
    etats = [d["etat"] for t, d in ws_events if t == "etat_jarvis"]
    assert etats == ["action", "repos"]


async def test_hands_interdit_ne_clique_jamais(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une action interdite (paiement) n'exécute aucune fonction bas niveau."""
    appels, _ = _simuler_environnement_hands(
        monkeypatch,
        jarvis_root,
        fenetre={"titre": "Boutique - Checkout", "processus": "chrome.exe", "pid": 10, "rect": {"x": 0, "y": 0, "w": 800, "h": 600}},
        element={"nom": "Payer 49€", "role": "ButtonControl", "rect": {"x": 100, "y": 100, "w": 80, "h": 30}},
    )

    res = await hands.executer_action({"type": "cliquer", "x": 120, "y": 115})
    assert res["ok"] is False
    assert res["statut_garde_fou"] == "interdit"
    assert appels == []


async def test_hands_accord_refuse_et_accepte_et_ordre_narration(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie accord refusé (0 exécution), accord accepté (1 exécution) et ordre exact des narrations."""
    fenetre_bloc = {
        "titre": "Sans titre - Bloc-notes",
        "processus": "notepad.exe",
        "pid": 100,
        "rect": {"x": 0, "y": 0, "w": 800, "h": 600},
    }
    elem_suppr = {"nom": "Supprimer", "role": "ButtonControl", "rect": {"x": 50, "y": 50, "w": 70, "h": 30}}

    # 1. Accord refusé -> rien n'est exécuté.
    appels_refus, _ = _simuler_environnement_hands(monkeypatch, jarvis_root, fenetre_bloc, elem_suppr)

    async def _accord_non(_source: str, _resume: str, _action: dict[str, Any], _risque: str) -> bool:
        return False

    monkeypatch.setattr(approvals, "demander_accord", _accord_non)
    res_refus = await hands.executer_action({"type": "cliquer", "x": 70, "y": 60})
    assert res_refus["ok"] is False
    assert res_refus["statut_garde_fou"] == "refuse"
    assert appels_refus == []

    # 2. Accord accepté -> exécuté exactement une fois.
    appels_ok, ws_ok = _simuler_environnement_hands(monkeypatch, jarvis_root, fenetre_bloc, elem_suppr)

    async def _accord_oui(_source: str, _resume: str, _action: dict[str, Any], _risque: str) -> bool:
        return True

    monkeypatch.setattr(approvals, "demander_accord", _accord_oui)
    res_ok = await hands.executer_action(
        {"type": "cliquer", "x": 70, "y": 60},
        attendu={"titre_contient": "Bloc-notes", "processus": "notepad.exe"},
    )
    assert res_ok["ok"] is True
    assert appels_ok == [("cliquer", (70, 60, "left"))]
    assert res_ok["changement_detecte"] is True

    # 3. Action autorisée : vérifie l'ordre exact des narrations et états.
    elem_fichier = {"nom": "Fichier", "role": "MenuItemControl", "rect": {"x": 15, "y": 35, "w": 45, "h": 22}}
    appels_auto, ws_auto = _simuler_environnement_hands(monkeypatch, jarvis_root, fenetre_bloc, elem_fichier)
    res_auto = await hands.executer_action(
        {"type": "cliquer", "x": 25, "y": 40},
        attendu={"titre_contient": "Bloc-notes"},
    )
    assert res_auto["ok"] is True
    assert appels_auto == [("cliquer", (25, 40, "left"))]

    narrations = [d["texte"] for t, d in ws_auto if t == "narration"]
    assert narrations == [
        "Je regarde l'écran…",
        "Je vérifie la fenêtre active : Sans titre - Bloc-notes",
        "Je clique sur « Fichier »",
        "Je vérifie le résultat…",
    ]
    assert res_auto["narration"] == narrations
    etats = [d["etat"] for t, d in ws_auto if t == "etat_jarvis"]
    assert etats == ["action", "repos"]
    del ws_ok


async def test_outils_ecran_et_fichier_supprimer(
    jarvis_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie l'enregistrement des outils ecran/pc/professeur/fichier et la suppression via corbeille."""
    reg = tools_registry.charger_outils()
    for nom in (
        "ecran.capturer",
        "ecran.fenetre_active",
        "ecran.trouver",
        "pc.action",
        "professeur.guider",
        "fichier.supprimer",
    ):
        assert nom in reg

    assert reg["pc.action"].risque == "action_pc"
    assert reg["fichier.supprimer"].risque == "irreversible"

    accords_demandes: list[str] = []
    poubelle: list[str] = []

    async def _demander_oui(resume: str, _action: dict[str, Any], _risque: str) -> bool:
        accords_demandes.append(resume)
        return True

    async def _demander_non(resume: str, _action: dict[str, Any], _risque: str) -> bool:
        accords_demandes.append(resume)
        return False

    from backend.aios.tools import ecran as ecran_tools

    monkeypatch.setattr(ecran_tools, "send2trash", lambda path_str: poubelle.append(path_str))

    # Chemin interdit -> rejet immédiat SANS même demander d'accord ni appeler send2trash.
    ctx_oui = ContexteOutil(
        agent_id="jarvis",
        conversation_id=None,
        acces_cerveau=True,
        demander_accord=_demander_oui,
    )
    res_interdit = await tools_registry.executer(
        "fichier.supprimer",
        {"chemin": str(jarvis_root / "backend" / "main.py")},
        ctx_oui,
    )
    assert res_interdit["ok"] is False
    assert accords_demandes == []
    assert poubelle == []

    # Chemin autorisé avec accord -> refusé si utilisateur dit non.
    brouillon = jarvis_root / "second-brain" / "brouillon.md"
    brouillon.parent.mkdir(parents=True, exist_ok=True)
    brouillon.write_text("# Brouillon\n", encoding="utf-8")

    ctx_non = ContexteOutil(
        agent_id="jarvis",
        conversation_id=None,
        acces_cerveau=True,
        demander_accord=_demander_non,
    )
    res_refus = await tools_registry.executer(
        "fichier.supprimer",
        {"chemin": str(brouillon)},
        ctx_non,
    )
    assert res_refus["ok"] is False
    assert res_refus["erreur"] == "Refusé par l'utilisateur"
    assert poubelle == []

    # Accord accepté -> send2trash appelé une fois.
    res_ok = await tools_registry.executer(
        "fichier.supprimer",
        {"chemin": str(brouillon)},
        ctx_oui,
    )
    assert res_ok["ok"] is True
    assert res_ok["resultat"]["corbeille"] is True
    assert poubelle == [str(brouillon.resolve())]


def test_api_ecran_capture_image_fenetre_et_pc_action(
    client: TestClient,
    jarvis_root: Path,
    auth_headers: dict[str, str],
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie POST /api/ecran/capture, GET /api/ecran/image/{nom}?token=, /fenetre et /pc/action."""
    cap = _creer_capture_factice(jarvis_root / "runtime" / "captures", "cap-api-test.png", (40, 80, 120))
    monkeypatch.setattr(vision, "capturer", lambda ecran=1, largeur_max=1280: cap)
    monkeypatch.setattr(
        uia,
        "fenetre_active",
        lambda: {"titre": "Bloc-notes", "processus": "notepad.exe", "pid": 99, "rect": {"x": 0, "y": 0, "w": 640, "h": 480}},
    )

    r_cap = client.post("/api/ecran/capture", headers=auth_headers)
    assert r_cap.status_code == 200
    payload = r_cap.json()
    assert payload == {
        "nom": "cap-api-test.png",
        "url": "/api/ecran/image/cap-api-test.png",
        "largeur": 160,
        "hauteur": 90,
    }

    assert client.get("/api/ecran/image/cap-api-test.png").status_code == 403
    r_img = client.get(f"/api/ecran/image/cap-api-test.png?token={token}")
    assert r_img.status_code == 200
    assert r_img.headers["content-type"] == "image/png"
    assert len(r_img.content) > 0

    r_fen = client.get("/api/ecran/fenetre", headers=auth_headers)
    assert r_fen.status_code == 200
    assert r_fen.json()["processus"] == "notepad.exe"

    appels, _ = _simuler_environnement_hands(
        monkeypatch,
        jarvis_root,
        fenetre={"titre": "Bloc-notes", "processus": "notepad.exe", "pid": 99, "rect": {"x": 0, "y": 0, "w": 640, "h": 480}},
    )
    r_act = client.post(
        "/api/pc/action",
        headers=auth_headers,
        json={"action": {"type": "taper", "texte": "Salut"}, "attendu": {"titre_contient": "Bloc-notes"}},
    )
    assert r_act.status_code == 200
    assert r_act.json()["ok"] is True
    assert appels == [("taper", "Salut")]
