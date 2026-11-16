"""Tests d'intégration réels écran/UIA/OCR (exclus par défaut, lancés via ``pytest -m reel``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.jarvis_engine import dpi, ocr, uia, vision


@pytest.mark.reel
def test_reel_capture_fenetre_uia_et_ocr(jarvis_root: Path) -> None:
    """Capture réellement l'écran principal, lit la fenêtre active, les éléments UIA et l'OCR."""
    del jarvis_root
    dpi.activer()

    cap = vision.capturer(ecran=1, largeur_max=1280)
    assert cap.chemin.is_file()
    assert cap.largeur > 0
    assert cap.hauteur > 0

    fenetre = uia.fenetre_active()
    assert isinstance(fenetre["titre"], str)
    assert fenetre["rect"]["w"] > 0

    elems = uia.elements(max=100)
    assert isinstance(elems, list)
    assert len(elems) > 0

    lignes_ocr = ocr.lire(cap)
    assert isinstance(lignes_ocr, list)
