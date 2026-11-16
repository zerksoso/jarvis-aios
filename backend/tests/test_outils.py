"""Tests du registre d'outils AIOS, permissions cerveau, scripts et accord utilisateur."""

from pathlib import Path
from typing import Any

from backend.aios import tools_registry
from backend.aios.tools_registry import ContexteOutil


async def _accord_oui(resume: str, action: dict[str, Any], risque: str) -> bool:
    """Simule une approbation utilisateur positive."""
    del resume, action, risque
    return True


async def _accord_non(resume: str, action: dict[str, Any], risque: str) -> bool:
    """Simule un refus utilisateur."""
    del resume, action, risque
    return False


async def test_registre_et_description_prompt(jarvis_root: Path) -> None:
    """Vérifie le chargement des outils du socle et la génération de description prompt."""
    del jarvis_root
    reg = tools_registry.charger_outils()
    attendus = {
        "cerveau.chercher",
        "cerveau.lire",
        "cerveau.ecrire",
        "memoriser",
        "web.chercher",
        "web.lire",
        "taches.lister",
        "taches.ajouter",
        "taches.cocher",
        "carte.afficher",
        "script.executer",
        "skill.creer",
    }
    assert attendus.issubset(set(reg.keys()))

    prompt = tools_registry.description_pour_prompt(["cerveau.chercher", "script.executer"])
    assert "cerveau.chercher" in prompt
    assert "script.executer" in prompt
    assert "irreversible" in prompt


async def test_refus_sans_acces_cerveau_et_lecture_ecriture_avec_acces(jarvis_root: Path) -> None:
    """Les outils cerveau.* refusent tout appel si acces_cerveau=False et fonctionnent sinon."""
    del jarvis_root
    ctx_refus = ContexteOutil(
        agent_id="atlas",
        conversation_id="agent:atlas",
        acces_cerveau=False,
        demander_accord=_accord_oui,
    )
    for outil_nom, args in (
        ("cerveau.ecrire", {"id": "01-Business-Projets/test", "contenu_md": "# Test"}),
        ("cerveau.lire", {"id": "01-Business-Projets/test"}),
        ("cerveau.chercher", {"q": "Test"}),
    ):
        res = await tools_registry.executer(outil_nom, args, ctx_refus)
        assert res["ok"] is False
        assert "refus" in str(res["erreur"]).lower()

    ctx_ok = ContexteOutil(
        agent_id="jarvis",
        conversation_id="agent:jarvis",
        acces_cerveau=True,
        demander_accord=_accord_oui,
    )
    ecrit = await tools_registry.executer(
        "cerveau.ecrire",
        {
            "id": "01-Business-Projets/note-outil",
            "contenu_md": "---\ntitre: Note Outil\nstatut: actif\n---\n\nContenu vérifié.\n",
        },
        ctx_ok,
    )
    assert ecrit["ok"] is True

    lu = await tools_registry.executer(
        "cerveau.lire",
        {"id": "01-Business-Projets/note-outil"},
        ctx_ok,
    )
    assert lu["ok"] is True
    assert lu["resultat"]["titre"] == "Note Outil"

    cherche = await tools_registry.executer("cerveau.chercher", {"q": "vérifié"}, ctx_ok)
    assert cherche["ok"] is True
    assert len(cherche["resultat"]) == 1


async def test_script_executer_accord_et_noms_malveillants(jarvis_root: Path) -> None:
    """script.executer exige l'accord utilisateur et rejette tout nom de script malveillant."""
    scripts_dir = jarvis_root / "aios" / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "salut.py").write_text("print('OK_SCRIPT_42')", encoding="utf-8")

    ctx_non = ContexteOutil(
        agent_id="jarvis",
        conversation_id=None,
        acces_cerveau=True,
        demander_accord=_accord_non,
    )
    refuse = await tools_registry.executer("script.executer", {"nom": "salut.py"}, ctx_non)
    assert refuse["ok"] is False
    assert refuse["erreur"] == "Refusé par l'utilisateur"

    ctx_oui = ContexteOutil(
        agent_id="jarvis",
        conversation_id=None,
        acces_cerveau=True,
        demander_accord=_accord_oui,
    )
    ok = await tools_registry.executer("script.executer", {"nom": "salut.py"}, ctx_oui)
    assert ok["ok"] is True
    assert ok["resultat"]["returncode"] == 0
    assert "OK_SCRIPT_42" in ok["resultat"]["sortie"]

    for nom_malveillant in (
        "../salut.py",
        "sous/salut.py",
        "sous\\salut.py",
        "C:\\temp\\salut.py",
        "programme.exe",
        "",
    ):
        res = await tools_registry.executer(
            "script.executer",
            {"nom": nom_malveillant},
            ctx_oui,
        )
        assert res["ok"] is False


async def test_outils_taches_cartes_memoire_et_skills(jarvis_root: Path) -> None:
    """Vérifie taches.*, carte.afficher, memoriser et skill.creer via tools_registry."""
    del jarvis_root
    ctx = ContexteOutil(
        agent_id="jarvis",
        conversation_id="agent:jarvis",
        acces_cerveau=True,
        demander_accord=_accord_oui,
    )

    ajout = await tools_registry.executer("taches.ajouter", {"titre": "Tester les outils"}, ctx)
    assert ajout["ok"] is True
    tache_id = ajout["resultat"]["id"]

    coche = await tools_registry.executer("taches.cocher", {"id": tache_id}, ctx)
    assert coche["ok"] is True
    assert coche["resultat"]["fait"] is True

    liste = await tools_registry.executer("taches.lister", {}, ctx)
    assert liste["ok"] is True
    assert len(liste["resultat"]) == 1

    carte = await tools_registry.executer(
        "carte.afficher",
        {"titre": "Synthèse", "contenu_md": "**OK**"},
        ctx,
    )
    assert carte["ok"] is True
    assert carte["resultat"]["epinglee"] is True

    mem = await tools_registry.executer(
        "memoriser",
        {"type": "fait", "texte": "Tests unitaires outils validés"},
        ctx,
    )
    assert mem["ok"] is True
    assert mem["resultat"]["ajoute"] is True

    sk = await tools_registry.executer(
        "skill.creer",
        {
            "nom": "Test Auto",
            "description": "Skill créé par outil",
            "declencheurs": ["test auto"],
            "outils": ["taches.lister"],
            "etapes": ["Lister les tâches"],
        },
        ctx,
    )
    assert sk["ok"] is True
    assert sk["resultat"]["nom"] == "test-auto"
