"""Tests unitaires et CLI du script d'export propre ``distrib/export_repo.py``."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_EXPORT_PATH = Path(__file__).resolve().parents[2] / "distrib" / "export_repo.py"


def _charger_export_repo() -> ModuleType:
    """Charge dynamiquement le module ``distrib/export_repo.py`` pour les tests."""
    nom = "distrib_export_repo"
    if nom in sys.modules:
        return sys.modules[nom]
    spec = importlib.util.spec_from_file_location(nom, _EXPORT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


export_repo = _charger_export_repo()


def _git(depot: Path, *args: str) -> str:
    """Exécute une commande Git dans ``depot`` et retourne la sortie standard nettoyée."""
    return subprocess.run(
        ["git", *args],
        cwd=depot,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _creer_faux_depot(racine: Path) -> None:
    """Construit une arborescence minimale simulant un dépôt source."""
    (racine / "backend" / "aios").mkdir(parents=True)
    (racine / "backend" / "__pycache__").mkdir(parents=True)
    (racine / "backend" / "app.py").write_text('MESSAGE = "Jarvis AIOS"\n', encoding="utf-8")
    (racine / "backend" / "aios" / "outil.py").write_text("VALEUR = 1\n", encoding="utf-8")
    (racine / "backend" / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"\x00\x01")
    (racine / "backend" / "cache.db").write_bytes(b"sqlite")

    (racine / "frontend" / "src").mkdir(parents=True)
    (racine / "frontend" / "node_modules" / "pkg").mkdir(parents=True)
    (racine / "frontend" / "dist").mkdir(parents=True)
    (racine / "frontend" / ".vite").mkdir(parents=True)
    (racine / "frontend" / "src" / "App.tsx").write_text("export const App = () => null;\n", encoding="utf-8")
    (racine / "frontend" / "node_modules" / "pkg" / "index.js").write_text("module.exports = {};\n", encoding="utf-8")
    (racine / "frontend" / "dist" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (racine / "frontend" / ".vite" / "deps.json").write_text("{}\n", encoding="utf-8")

    (racine / "launcher").mkdir(parents=True)
    (racine / "launcher" / "JarvisLauncher.cs").write_text("// launcher\n", encoding="utf-8")
    (racine / "launcher" / "build.cmd").write_text("@echo off\n", encoding="utf-8")
    (racine / "launcher" / "Jarvis.exe").write_bytes(b"MZ")

    (racine / "distrib" / "templates" / "agents").mkdir(parents=True)
    (racine / "distrib" / "templates" / "agents" / "jarvis.json").write_text("{}\n", encoding="utf-8")

    for dossier_donnees in ("runtime", "second-brain", "agents", "canaux", "aios"):
        (racine / dossier_donnees).mkdir(parents=True)
        (racine / dossier_donnees / "donnees.txt").write_text("prive\n", encoding="utf-8")

    (racine / ".gitignore").write_text("/runtime/\n/build/\n", encoding="utf-8")
    (racine / "README.md").write_text("# README prive\n", encoding="utf-8")
    (racine / "README.GITHUB.md").write_text("# Jarvis AIOS Public\n", encoding="utf-8")


def test_export_detecte_piege_et_exclut_donnees(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un fichier piège avec fausse clé API bloque l'export (code 1) après filtrage."""
    racine = tmp_path / "depot"
    _creer_faux_depot(racine)

    fausse_cle = "AIza" + "0123456789abcdefABCDEF_-01234567890"
    (racine / "backend" / "piege.py").write_text(f'API_KEY = "{fausse_cle}"\n', encoding="utf-8")

    code = export_repo.main(["--racine", str(racine), "--sortie", "build/publish"])
    capture = capsys.readouterr()

    assert code == 1
    sortie = racine / "build" / "publish"

    assert (sortie / "backend" / "app.py").is_file()
    assert (sortie / "backend" / "aios" / "outil.py").is_file()
    assert (sortie / "README.md").read_text(encoding="utf-8") == "# Jarvis AIOS Public\n"
    assert (sortie / ".gitignore").read_text(encoding="utf-8") == "/runtime/\n/build/\n"

    for dossier_interdit in ("runtime", "second-brain", "agents", "canaux", "aios"):
        assert not (sortie / dossier_interdit).exists()
    assert not (sortie / "backend" / "__pycache__").exists()
    assert not (sortie / "backend" / "cache.db").exists()
    assert not (sortie / ".git").exists()
    assert "Échec du scan" in capture.err or "piege.py" in capture.err


def test_export_propre_cree_git_et_respecte_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un dépôt sain produit un commit Git unique sur main, refuse sans --force et écrase avec."""
    racine = tmp_path / "depot"
    _creer_faux_depot(racine)
    monkeypatch.setattr(export_repo, "RACINE_DEPOT", racine)

    code = export_repo.main(["--sortie", "build/publish"])
    capture = capsys.readouterr()

    assert code == 0
    sortie = racine / "build" / "publish"

    assert (sortie / "frontend" / "src" / "App.tsx").is_file()
    assert not (sortie / "frontend" / "node_modules").exists()
    assert not (sortie / "frontend" / "dist").exists()
    assert not (sortie / "frontend" / ".vite").exists()
    assert (sortie / "launcher" / "JarvisLauncher.cs").is_file()
    assert not (sortie / "launcher" / "Jarvis.exe").exists()
    assert (sortie / "distrib" / "templates" / "agents" / "jarvis.json").is_file()
    assert (sortie / ".git").is_dir()

    assert _git(sortie, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _git(sortie, "rev-list", "--count", "HEAD") == "1"
    assert _git(sortie, "log", "-1", "--pretty=%B") == "Initial commit: Jarvis AIOS"
    assert _git(sortie, "config", "--local", "user.name") == "Jarvis AIOS"
    assert _git(sortie, "config", "--local", "user.email") == "noreply@" + "jarvis-aios.invalid"
    assert _git(sortie, "remote") == ""

    assert "Fichiers exportés" in capture.out
    assert "Commit initial" in capture.out
    assert "rien n'a été poussé" in capture.out

    assert export_repo.main(["--sortie", "build/publish"]) == 1
    assert export_repo.main(["--sortie", "build/publish", "--force"]) == 0


def test_refuse_sortie_hors_build(tmp_path: Path) -> None:
    """Tout chemin de sortie hors de build/ ou égal à build/ doit être refusé."""
    racine = tmp_path / "depot"
    racine.mkdir()

    assert export_repo.main(["--racine", str(racine), "--sortie", str(tmp_path / "ailleurs")]) == 1
    assert export_repo.main(["--racine", str(racine), "--sortie", "build"]) == 1
    assert export_repo.main(["--racine", str(racine), "--sortie", "../dehors"]) == 1
