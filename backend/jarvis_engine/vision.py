"""Capture d'écran multi-moniteurs via ``mss`` et comparaison d'images."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from backend import config

logger = logging.getLogger(__name__)

MAX_CAPTURES_CONSERVEES = 20


@dataclass(frozen=True)
class Capture:
    """Métadonnées d'une capture d'écran enregistrée dans ``RUNTIME/captures/``."""

    chemin: Path
    largeur: int
    hauteur: int
    echelle: float
    origine_x: int
    origine_y: int


def _dossier_captures() -> Path:
    """Retourne et crée si besoin le répertoire ``RUNTIME/captures/``."""
    dossier = config.RUNTIME / "captures"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def _elaguer_captures(dossier: Path, limite: int = MAX_CAPTURES_CONSERVEES) -> None:
    """Ne conserve que les ``limite`` captures PNG les plus récentes dans ``dossier``."""
    try:
        fichiers = [p for p in dossier.glob("*.png") if p.is_file()]
    except OSError:
        return
    if len(fichiers) <= limite:
        return

    fichiers.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    for ancien in fichiers[limite:]:
        try:
            ancien.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Impossible de supprimer l'ancienne capture %s : %s", ancien, exc)


def capturer(ecran: int = 1, largeur_max: int = 1280) -> Capture:
    """Capture un écran (ou le bureau virtuel si ``ecran=0``) et redimensionne si besoin.

    Enregistre la capture PNG dans ``RUNTIME/captures/`` en conservant les 20 plus récentes.
    ``echelle`` représente ``largeur_reelle / largeur_image`` afin de reconvertir les
    coordonnées de l'image vers les pixels physiques de l'écran.

    Args:
        ecran: Index de moniteur ``mss`` (1 = écran principal, 0 = bureau virtuel complet).
        largeur_max: Largeur maximale de sortie en pixels (``<= 0`` désactive le redimensionnement).

    Returns:
        Instance ``Capture`` décrivant le fichier PNG et sa géométrie.

    Raises:
        RuntimeError: Si ``mss`` ou ``Pillow`` est indisponible ou si la capture échoue.

    Effets de bord:
        Écrit un fichier PNG dans ``RUNTIME/captures/`` et purge les captures excédentaires.
    """
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError("Module 'mss' indisponible pour la capture d'écran.") from exc

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Module 'Pillow' indisponible pour le traitement d'image.") from exc

    dossier = _dossier_captures()

    with mss.mss() as sct:
        moniteurs = sct.monitors
        if not moniteurs:
            raise RuntimeError("Aucun moniteur détecté par mss.")

        idx = int(ecran)
        if idx < 0 or idx >= len(moniteurs):
            idx = 1 if len(moniteurs) > 1 else 0
        moniteur = moniteurs[idx]

        origine_x = int(moniteur.get("left", 0))
        origine_y = int(moniteur.get("top", 0))
        brut = sct.grab(moniteur)
        real_w, real_h = int(brut.width), int(brut.height)
        image = Image.frombytes("RGB", (real_w, real_h), brut.rgb)

    if largeur_max > 0 and real_w > largeur_max:
        ratio = largeur_max / float(real_w)
        new_w = int(largeur_max)
        new_h = max(1, round(real_h * ratio))
        image = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        echelle = real_w / float(new_w)
    else:
        new_w, new_h = real_w, real_h
        echelle = 1.0

    horodatage = datetime.now().strftime("%Y%m%d-%H%M%S")
    nom_fichier = f"cap-{horodatage}-{uuid.uuid4().hex[:6]}.png"
    chemin = dossier / nom_fichier
    image.save(chemin, format="PNG")
    image.close()

    _elaguer_captures(dossier, MAX_CAPTURES_CONSERVEES)
    return Capture(
        chemin=chemin,
        largeur=new_w,
        hauteur=new_h,
        echelle=echelle,
        origine_x=origine_x,
        origine_y=origine_y,
    )


def comparer_captures(avant: Capture | Path | str, apres: Capture | Path | str) -> float:
    """Calcule la différence visuelle moyenne normalisée (0.0 à 1.0) entre deux captures.

    Args:
        avant: Capture ou chemin de l'image avant action.
        apres: Capture ou chemin de l'image après action.

    Returns:
        Score flottant dans ``[0.0, 1.0]`` (``0.0`` si identiques ou illisibles).
    """
    chemin_avant = avant.chemin if isinstance(avant, Capture) else Path(avant)
    chemin_apres = apres.chemin if isinstance(apres, Capture) else Path(apres)
    if not chemin_avant.is_file() or not chemin_apres.is_file():
        return 0.0

    try:
        from PIL import Image, ImageChops, ImageStat

        with Image.open(chemin_avant) as img_a, Image.open(chemin_apres) as img_b:
            taille = (320, 180)
            a_rgb = img_a.convert("RGB").resize(taille, Image.Resampling.BILINEAR)
            b_rgb = img_b.convert("RGB").resize(taille, Image.Resampling.BILINEAR)
            diff = ImageChops.difference(a_rgb, b_rgb)
            moyennes = ImageStat.Stat(diff).mean
            return float(sum(moyennes) / (len(moyennes) * 255.0))
    except Exception as exc:
        logger.debug("Comparaison de captures ignorée (%s).", exc)
        return 0.0
