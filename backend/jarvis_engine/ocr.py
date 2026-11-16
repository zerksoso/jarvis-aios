"""Reconnaissance optique de texte (OCR) via Windows.Media.Ocr avec repli RapidOCR."""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any

from backend.jarvis_engine.vision import Capture

logger = logging.getLogger(__name__)

_rapidocr_lock = threading.Lock()
_rapidocr_engine: Any | None = None


def _vers_coords_ecran(
    ix: float,
    iy: float,
    iw: float,
    ih: float,
    echelle: float,
    origine_x: int,
    origine_y: int,
) -> dict[str, int]:
    """Convertit un rectangle de l'image capturée vers les pixels physiques de l'écran."""
    facteur = echelle if echelle > 0 else 1.0
    return {
        "x": round(origine_x + ix * facteur),
        "y": round(origine_y + iy * facteur),
        "w": max(1, round(iw * facteur)),
        "h": max(1, round(ih * facteur)),
    }


def _ocr_winrt(
    chemin: Path,
    echelle: float,
    origine_x: int,
    origine_y: int,
) -> list[dict[str, Any]]:
    """Exécute l'OCR natif ``Windows.Media.Ocr`` à partir de bytes BGRA8."""
    if sys.platform != "win32":
        raise RuntimeError("Windows.Media.Ocr disponible uniquement sous Windows.")

    from PIL import Image
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine

    engine = OcrEngine.try_create_from_language(Language("fr"))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Impossible d'initialiser Windows.Media.Ocr (fr ou profil utilisateur).")

    with Image.open(chemin) as img:
        rgba = img.convert("RGBA")
        largeur, hauteur = rgba.size
        bgra_bytes = rgba.tobytes("raw", "BGRA")

    bitmap = SoftwareBitmap.create_copy_from_buffer(
        bgra_bytes,
        BitmapPixelFormat.BGRA8,
        largeur,
        hauteur,
        BitmapAlphaMode.PREMULTIPLIED,
    )
    try:
        ocr_result = engine.recognize_async(bitmap).get()
    finally:
        close_fn = getattr(bitmap, "close", None)
        if callable(close_fn):
            close_fn()

    entrees: list[dict[str, Any]] = []
    for ligne in ocr_result.lines:
        texte = str(ligne.text or "").strip()
        if not texte:
            continue
        mots = list(ligne.words)
        if not mots:
            continue

        xs1: list[float] = []
        ys1: list[float] = []
        xs2: list[float] = []
        ys2: list[float] = []
        for mot in mots:
            br = mot.bounding_rect
            xs1.append(float(br.x))
            ys1.append(float(br.y))
            xs2.append(float(br.x) + float(br.width))
            ys2.append(float(br.y) + float(br.height))

        ix = min(xs1)
        iy = min(ys1)
        iw = max(xs2) - ix
        ih = max(ys2) - iy
        entrees.append(
            {
                "texte": texte,
                "rect": _vers_coords_ecran(ix, iy, iw, ih, echelle, origine_x, origine_y),
            }
        )

    return entrees


def _obtenir_rapidocr() -> Any:
    """Initialise paresseusement le singleton ``RapidOCR`` sous verrou thread."""
    global _rapidocr_engine
    with _rapidocr_lock:
        if _rapidocr_engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _rapidocr_engine = RapidOCR()
        return _rapidocr_engine


def _ocr_rapidocr(
    chemin: Path,
    echelle: float,
    origine_x: int,
    origine_y: int,
) -> list[dict[str, Any]]:
    """Exécute le repli ``rapidocr-onnxruntime`` sur le fichier image."""
    engine = _obtenir_rapidocr()
    resultat, _ = engine(str(chemin))
    if not resultat:
        return []

    entrees: list[dict[str, Any]] = []
    for item in resultat:
        if not isinstance(item, list | tuple) or len(item) < 2:
            continue
        boite = item[0]
        texte = str(item[1] or "").strip()
        if not texte or not boite:
            continue
        xs = [float(pt[0]) for pt in boite]
        ys = [float(pt[1]) for pt in boite]
        ix = min(xs)
        iy = min(ys)
        iw = max(xs) - ix
        ih = max(ys) - iy
        entrees.append(
            {
                "texte": texte,
                "rect": _vers_coords_ecran(ix, iy, iw, ih, echelle, origine_x, origine_y),
            }
        )

    return entrees


def lire(capture: Capture | Path | str) -> list[dict[str, Any]]:
    """Extrait les lignes de texte visibles et leurs rectangles en coordonnées écran.

    Utilise ``Windows.Media.Ocr`` en priorité (langue ``fr``, repli langues du profil),
    puis ``rapidocr-onnxruntime`` si WinRT échoue ou est indisponible.

    Args:
        capture: Objet ``Capture`` ou chemin vers une image PNG/JPG.

    Returns:
        Liste de dictionnaires ``{"texte": str, "rect": {"x", "y", "w", "h"}}``.
    """
    if isinstance(capture, Capture):
        chemin = capture.chemin
        echelle = capture.echelle
        origine_x = capture.origine_x
        origine_y = capture.origine_y
    else:
        chemin = Path(getattr(capture, "chemin", capture))
        echelle = float(getattr(capture, "echelle", 1.0))
        origine_x = int(getattr(capture, "origine_x", 0))
        origine_y = int(getattr(capture, "origine_y", 0))

    if not chemin.is_file():
        return []

    try:
        return _ocr_winrt(chemin, echelle, origine_x, origine_y)
    except Exception as exc:
        logger.debug("Windows.Media.Ocr indisponible (%s), repli RapidOCR.", exc)

    try:
        return _ocr_rapidocr(chemin, echelle, origine_x, origine_y)
    except Exception as exc:
        logger.warning("Échec complet de l'OCR (WinRT et RapidOCR) : %s", exc)
        return []
