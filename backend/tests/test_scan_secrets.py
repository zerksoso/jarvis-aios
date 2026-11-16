"""Tests unitaires et CLI du scanner de secrets ``distrib/scan_secrets.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCAN_PATH = Path(__file__).resolve().parents[2] / "distrib" / "scan_secrets.py"


def _charger_scan_secrets() -> ModuleType:
    """Charge dynamiquement le module ``distrib/scan_secrets.py`` pour les tests."""
    nom = "distrib_scan_secrets"
    if nom in sys.modules:
        return sys.modules[nom]
    spec = importlib.util.spec_from_file_location(nom, _SCAN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


scan_secrets = _charger_scan_secrets()


def test_dossier_sain_accepte(tmp_path: Path) -> None:
    """Un dossier contenant du code et des exemples génériques doit passer avec code 0."""
    (tmp_path / "app.py").write_text(
        'chemin = r"C:\\Users\\<nom>\\JarvisAIOS"\n'
        'email = "contact@example.com"\n'
        'icone = "logo@2x.png"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Jarvis AIOS\nDocumentation propre.\n", encoding="utf-8")

    alertes = scan_secrets.scanner_dossier(tmp_path)
    assert alertes == []
    assert scan_secrets.main([str(tmp_path)]) == 0


@pytest.mark.parametrize(
    ("nom_fichier", "contenu"),
    [
        ("google.txt", "KEY=" + "AIza" + "0123456789abcdefABCDEF_-01234567890"),
        ("openai.txt", "SECRET=" + "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"),
        ("github.txt", "TOKEN=" + "ghp_" + "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6"),
        ("slack.txt", "SLACK=" + "xox" + "b-123456-abcdef"),
        ("pem.txt", "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIE...\n"),
        ("email.txt", "auteur = alice.dupont" + "@entreprise-reelle.fr\n"),
        ("chemin.txt", 'root = "C:\\\\Users\\\\jeandupont\\\\Documents"\n'),
        ("identifiant.txt", "dossier = " + "mi" + "mia\n"),
    ],
)
def test_fichier_piege_detecte(tmp_path: Path, nom_fichier: str, contenu: str) -> None:
    """Chaque catégorie de secret ou donnée personnelle doit retourner code 1."""
    (tmp_path / nom_fichier).write_text(contenu, encoding="utf-8")

    alertes = scan_secrets.scanner_dossier(tmp_path)
    assert len(alertes) >= 1
    assert scan_secrets.main([str(tmp_path)]) == 1


def test_dossiers_exclus_ignores(tmp_path: Path) -> None:
    """Les dossiers exclus (.venv, __pycache__, runtime) sont ignorés par le scan."""
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "piege.txt").write_text("TOKEN=" + "ghp_" + "A" * 32, encoding="utf-8")

    assert scan_secrets.scanner_dossier(tmp_path) == []
    assert scan_secrets.main([str(tmp_path)]) == 0
