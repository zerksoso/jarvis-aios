"""Tests du bootstrap des données utilisateur et de l'état de premier lancement."""

from pathlib import Path

from fastapi.testclient import TestClient

from backend import bootstrap, config, main


def test_ensure_data_dir_copie_idempotente_sans_ecrasement(jarvis_root: Path) -> None:
    """Copie les modèles absents vers ROOT sans jamais écraser un fichier existant."""
    templates = config.TEMPLATES_DIR
    (templates / "second-brain" / "00-Profil").mkdir(parents=True, exist_ok=True)
    (templates / "agents").mkdir(parents=True, exist_ok=True)
    (templates / "second-brain" / "00-Profil" / "profil-utilisateur.md").write_text(
        "# Profil vierge\n", encoding="utf-8"
    )
    (templates / "second-brain" / "memories.json").write_text(
        '[{"id": "tpl-1", "texte": "Règle modèle"}]', encoding="utf-8"
    )
    (templates / "agents" / "jarvis.json").write_text('{"id": "jarvis"}', encoding="utf-8")

    # Pré-existant dans ROOT : ne doit jamais être écrasé.
    cible_mem = jarvis_root / "second-brain" / "memories.json"
    cible_mem.parent.mkdir(parents=True, exist_ok=True)
    cible_mem.write_text('[{"id": "user-1", "texte": "Donnée personnelle"}]', encoding="utf-8")

    bootstrap.ensure_data_dir()

    assert (jarvis_root / "runtime").is_dir()
    assert (jarvis_root / "second-brain" / "00-Profil" / "profil-utilisateur.md").read_text(
        encoding="utf-8"
    ) == "# Profil vierge\n"
    assert (jarvis_root / "agents" / "jarvis.json").read_text(encoding="utf-8") == '{"id": "jarvis"}'
    assert "Donnée personnelle" in cible_mem.read_text(encoding="utf-8")

    # Modification utilisateur sur un fichier copié puis second appel : préservé.
    profil_cible = jarvis_root / "second-brain" / "00-Profil" / "profil-utilisateur.md"
    profil_cible.write_text("# Profil complété\n", encoding="utf-8")
    bootstrap.ensure_data_dir()
    assert profil_cible.read_text(encoding="utf-8") == "# Profil complété\n"


def test_etat_premier_lancement_lecture_et_ecriture(jarvis_root: Path) -> None:
    """Vérifie le cycle complet lecture/écriture atomique de premier_lancement.json."""
    etat_init = bootstrap.lire_etat_premier_lancement()
    assert etat_init == {"termine": False, "etape": "moteurs", "termine_le": None}

    etat_etape = bootstrap.ecrire_etat_premier_lancement(etape="connexion")
    assert etat_etape == {"termine": False, "etape": "connexion", "termine_le": None}

    etat_fin = bootstrap.ecrire_etat_premier_lancement(termine=True, etape="termine")
    assert etat_fin["termine"] is True
    assert etat_fin["etape"] == "termine"
    assert isinstance(etat_fin["termine_le"], str) and len(etat_fin["termine_le"]) >= 10

    fichier = jarvis_root / "runtime" / "premier_lancement.json"
    assert fichier.is_file()
    assert bootstrap.lire_etat_premier_lancement() == etat_fin

    # En cas de JSON corrompu, retombe proprement sur l'état par défaut.
    fichier.write_text("{json invalide", encoding="utf-8")
    assert bootstrap.lire_etat_premier_lancement() == {
        "termine": False,
        "etape": "moteurs",
        "termine_le": None,
    }


def test_lifespan_appelle_bootstrap_avant_db_et_hooks(jarvis_root: Path) -> None:
    """Le démarrage FastAPI exécute bootstrap.ensure_data_dir() avant l'init DB."""
    (config.TEMPLATES_DIR / "second-brain").mkdir(parents=True, exist_ok=True)
    (config.TEMPLATES_DIR / "second-brain" / "note-modele.md").write_text(
        "---\ntitre: Modèle\n---\nContenu modèle\n", encoding="utf-8"
    )

    app = main.create_app()
    with TestClient(app):
        assert (jarvis_root / "runtime" / "jarvis.db").is_file()
        assert (jarvis_root / "second-brain" / "note-modele.md").is_file()
