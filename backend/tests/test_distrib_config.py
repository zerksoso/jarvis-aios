"""Tests de résolution de APP_DIR, ROOT (3 cas), VERSION et chemins dérivés."""

from pathlib import Path

import pytest

from backend import config


def test_resoudre_root_priorite_jarvis_root(
    jarvis_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cas 1 : JARVIS_ROOT défini prime même si installed.flag existe dans APP_DIR."""
    del jarvis_root
    fake_app = tmp_path / "app"
    fake_app.mkdir()
    (fake_app / "installed.flag").write_text("1", encoding="utf-8")

    custom_root = tmp_path / "custom-root"
    monkeypatch.setenv("JARVIS_ROOT", str(custom_root))

    root = config.refresh_paths(app_dir=fake_app)
    assert root == custom_root
    assert config.ROOT == custom_root
    assert config.RUNTIME == custom_root / "runtime"
    assert config.FRONT_DIST == fake_app / "frontend/dist"
    assert config.TEMPLATES_DIR == fake_app / "templates"


def test_resoudre_root_installe_avec_installed_flag(
    jarvis_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cas 2 : sans JARVIS_ROOT, si APP_DIR/installed.flag existe -> %USERPROFILE%\\JarvisAIOS."""
    del jarvis_root
    fake_app = tmp_path / "program"
    fake_app.mkdir()
    (fake_app / "installed.flag").write_text("installed", encoding="utf-8")

    fake_home = tmp_path / "user-home"
    monkeypatch.delenv("JARVIS_ROOT", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    root = config.refresh_paths(app_dir=fake_app)
    assert root == fake_home / "JarvisAIOS"
    assert config.ROOT == fake_home / "JarvisAIOS"
    assert config.TEMPLATES_DIR == fake_app / "templates"
    assert config.FRONT_DIST == fake_app / "frontend/dist"


def test_resoudre_root_dev_sans_installed_flag(
    jarvis_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cas 3 : sans JARVIS_ROOT ni installed.flag -> ROOT == APP_DIR (mode développement)."""
    del jarvis_root
    fake_app = tmp_path / "dev-repo"
    fake_app.mkdir()
    (fake_app / "VERSION").write_text("1.2.3\n", encoding="utf-8")

    monkeypatch.delenv("JARVIS_ROOT", raising=False)

    root = config.refresh_paths(app_dir=fake_app)
    assert root == fake_app
    assert config.ROOT == fake_app
    assert config.VERSION == "1.2.3"
    assert not hasattr(config, "AGY_QUOTA_SCRIPT")


def test_version_defaut_si_fichier_absent(jarvis_root: Path, tmp_path: Path) -> None:
    """VERSION vaut 0.0.0-dev lorsque APP_DIR/VERSION est absent ou vide."""
    del jarvis_root
    app_sans_version = tmp_path / "app-vide"
    app_sans_version.mkdir()

    config.refresh_paths(nouveau_root=tmp_path, app_dir=app_sans_version)
    assert config.VERSION == "0.0.0-dev"

    (app_sans_version / "VERSION").write_text("   \n", encoding="utf-8")
    config.refresh_paths(nouveau_root=tmp_path, app_dir=app_sans_version)
    assert config.VERSION == "0.0.0-dev"
