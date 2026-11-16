"""Mode Professeur : repérage UIA/OCR/vision (« set-of-marks ») et guidage visuel sans clic."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from backend import config
from backend.jarvis_engine import ocr, uia, vision
from backend.llm import router as llm_router
from backend.llm.base import LLMRequest
from backend.llm.jsonparse import extraire_json
from backend.ws_hub import hub, on_client_message

logger = logging.getLogger(__name__)

_COULEURS_SOM: tuple[str, ...] = (
    "#22d3ee",
    "#f59e0b",
    "#a78bfa",
    "#34d399",
    "#f472b6",
    "#38bdf8",
)

_MAX_CANDIDATS_PROMPT = 200
_TOLERANCE_CLIC_PX = 6
_TIMEOUT_ETAPE_DEFAUT_S = 120.0


@dataclass
class _AttenteGuide:
    """État d'attente de validation utilisateur (clic réel ou WS ``guide_clic``) pour une étape."""

    event: asyncio.Event
    loop: asyncio.AbstractEventLoop
    rect: dict[str, int]
    clique: bool = False


_guides_actifs: dict[str, _AttenteGuide] = {}
_taches_guidage: set[asyncio.Task[Any]] = set()


def reset_etat_pour_tests() -> None:
    """Annule les guidages en cours et vide les attentes actives pour l'isolation des tests."""
    for tache in list(_taches_guidage):
        if not tache.done():
            tache.cancel()
    _taches_guidage.clear()
    for attente in list(_guides_actifs.values()):
        attente.event.set()
    _guides_actifs.clear()


def _fusionner_candidats(
    elems_uia: list[dict[str, Any]],
    elems_ocr: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fusionne les contrôles UIA et les textes OCR en candidats numérotés à partir de 1."""
    candidats: list[dict[str, Any]] = []

    for el in elems_uia:
        rect = el.get("rect") or {}
        w = int(rect.get("w") or 0)
        h = int(rect.get("h") or 0)
        label = str(el.get("nom") or "").strip()
        if not label or w <= 0 or h <= 0:
            continue
        candidats.append(
            {
                "index": len(candidats) + 1,
                "x": int(rect.get("x") or 0),
                "y": int(rect.get("y") or 0),
                "w": w,
                "h": h,
                "label": label,
                "role": str(el.get("role") or ""),
                "source": "uia",
            }
        )

    for item in elems_ocr:
        rect = item.get("rect") or {}
        w = int(rect.get("w") or 0)
        h = int(rect.get("h") or 0)
        texte = str(item.get("texte") or "").strip()
        if not texte or w <= 0 or h <= 0:
            continue
        candidats.append(
            {
                "index": len(candidats) + 1,
                "x": int(rect.get("x") or 0),
                "y": int(rect.get("y") or 0),
                "w": w,
                "h": h,
                "label": texte,
                "role": "ocr",
                "source": "ocr",
            }
        )

    return candidats


def annoter_capture_set_of_marks(
    capture: vision.Capture,
    candidats: list[dict[str, Any]],
    sortie: Path | None = None,
) -> Path:
    """Dessine sur une copie de la capture des cadres numérotés façon « set-of-marks ».

    Convertit chaque rectangle candidat (coordonnées écran) vers les pixels de l'image
    capturée, trace une bordure colorée et une pastille lisible avec son numéro.

    Args:
        capture: Capture d'écran de référence.
        candidats: Liste de candidats contenant ``index``, ``x``, ``y``, ``w``, ``h``.
        sortie: Chemin PNG de destination (sinon fichier temporaire dans ``RUNTIME/captures/``).

    Returns:
        Chemin du fichier PNG annoté.
    """
    from PIL import Image, ImageDraw, ImageFont

    dest = sortie or (config.RUNTIME / "captures" / f"som-{uuid.uuid4().hex[:8]}.png")
    dest.parent.mkdir(parents=True, exist_ok=True)

    facteur = capture.echelle if capture.echelle > 0 else 1.0
    font = ImageFont.load_default(size=14)

    with Image.open(capture.chemin) as base:
        img = base.convert("RGB")
        draw = ImageDraw.Draw(img)
        img_w, img_h = img.size

        for idx_pos, cand in enumerate(candidats[:_MAX_CANDIDATS_PROMPT]):
            num = int(cand["index"])
            ix = round((int(cand["x"]) - capture.origine_x) / facteur)
            iy = round((int(cand["y"]) - capture.origine_y) / facteur)
            iw = max(4, round(int(cand["w"]) / facteur))
            ih = max(4, round(int(cand["h"]) / facteur))

            x1 = max(0, min(img_w - 2, ix))
            y1 = max(0, min(img_h - 2, iy))
            x2 = max(x1 + 2, min(img_w - 1, ix + iw))
            y2 = max(y1 + 2, min(img_h - 1, iy + ih))

            couleur = _COULEURS_SOM[idx_pos % len(_COULEURS_SOM)]
            draw.rectangle([x1, y1, x2, y2], outline=couleur, width=2)

            etiquette = str(num)
            bbox = draw.textbbox((0, 0), etiquette, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            badge_w = tw + 8
            badge_h = th + 6

            bx1 = max(0, min(img_w - badge_w, x1))
            # Place la pastille juste au-dessus si possible, sinon dans le coin supérieur.
            by1 = y1 - badge_h if y1 >= badge_h else min(img_h - badge_h, y1)
            bx2 = min(img_w - 1, bx1 + badge_w)
            by2 = min(img_h - 1, by1 + badge_h)

            draw.rectangle([bx1, by1, bx2, by2], fill="#0f172a", outline=couleur, width=1)
            draw.text((bx1 + 4, by1 + 2), etiquette, fill="#f8fafc", font=font)

        img.save(dest, format="PNG")

    return dest


def _formater_candidats_prompt(candidats: list[dict[str, Any]]) -> str:
    """Formate les candidats UIA/OCR en liste compacte numérotée pour le LLM."""
    lignes: list[str] = []
    for c in candidats[:_MAX_CANDIDATS_PROMPT]:
        lignes.append(
            f"[{c['index']}] ({c['source']} {c['role']}) {c['label']!r} "
            f"@ ({c['x']},{c['y']},{c['w']}x{c['h']})"
        )
    return "\n".join(lignes)


def _extraire_index_valide(donnees: dict[str, Any] | None, par_index: dict[int, dict[str, Any]]) -> int | None:
    """Extrait un entier ``index`` valide et présent dans ``par_index`` (exclut les booléens)."""
    if not isinstance(donnees, dict):
        return None
    brut = donnees.get("index")
    if isinstance(brut, bool) or not isinstance(brut, int):
        return None
    return brut if brut in par_index else None


async def trouver(objectif: str) -> dict[str, Any]:
    """Localise un élément d'interface correspondant à ``objectif`` via UIA + OCR + repli vision.

    1. Capture l'écran et collecte les candidats UIA + OCR.
    2. Demande à ``hydrogen`` (effort ``low``) de sélectionner l'index JSON correspondant.
    3. Si aucun candidat n'existe ou si l'index retourné est invalide, annote la capture
       façon « set-of-marks » et déclenche un second appel vision avec l'image jointe.

    Args:
        objectif: Description en français de l'élément recherché (ex. ``"menu Fichier"``).

    Returns:
        Dictionnaire ``{"x", "y", "w", "h", "label", "source", "raison"}``.

    Raises:
        ValueError: Si ``objectif`` est vide ou si aucun élément n'a pu être localisé.
    """
    objectif_propre = (objectif or "").strip()
    if not objectif_propre:
        raise ValueError("L'objectif de recherche ne peut pas être vide.")

    capture = await asyncio.to_thread(vision.capturer)
    elems_uia = await asyncio.to_thread(uia.elements, 300)
    elems_ocr = await asyncio.to_thread(ocr.lire, capture)
    candidats = _fusionner_candidats(elems_uia, elems_ocr)
    par_index = {int(c["index"]): c for c in candidats}

    if candidats:
        prompt_texte = (
            f"Objectif utilisateur : {objectif_propre}\n\n"
            f"Éléments détectés à l'écran (UIA + OCR) :\n"
            f"{_formater_candidats_prompt(candidats)}\n\n"
            'Réponds uniquement en JSON : {"index": <numero>, "raison": "<courte justification>"}.'
        )
        res = await llm_router.generer(
            LLMRequest(
                prompt=prompt_texte,
                systeme=(
                    "Tu identifies l'élément d'interface exact correspondant à l'objectif. "
                    "Réponds strictement en JSON valide avec les clés index (entier) et raison."
                ),
                moteur="auto",
                modele="hydrogen",
                effort="low",
                timeout_s=60,
            )
        )
        donnees = extraire_json(res.texte)
        idx_valide = _extraire_index_valide(donnees, par_index)
        if idx_valide is not None:
            c = par_index[idx_valide]
            return {
                "x": c["x"],
                "y": c["y"],
                "w": c["w"],
                "h": c["h"],
                "label": c["label"],
                "source": c["source"],
                "raison": str((donnees or {}).get("raison") or ""),
            }

    # Repli vision avec capture annotée « set-of-marks ».
    som_path = await asyncio.to_thread(annoter_capture_set_of_marks, capture, candidats)
    try:
        liste_txt = (
            f"\nCandidats numérotés :\n{_formater_candidats_prompt(candidats)}"
            if candidats
            else ""
        )
        prompt_vision = (
            f"Objectif utilisateur : {objectif_propre}\n"
            "Analyse l'image jointe annotée de rectangles numérotés.\n"
            'Si un numéro correspond, réponds en JSON : {"index": <numero>, "raison": "…"}.\n'
            'Sinon, donne ses coordonnées dans l\'image : {"x": <int>, "y": <int>, "w": <int>, "h": <int>, "label": "…", "raison": "…"}.'
            f"{liste_txt}"
        )
        res_vision = await llm_router.generer(
            LLMRequest(
                prompt=prompt_vision,
                systeme=(
                    "Tu localises visuellement un élément sur une capture d'écran annotée. "
                    "Réponds strictement en JSON valide."
                ),
                moteur="auto",
                modele="hydrogen",
                effort="medium",
                images=[str(som_path)],
                timeout_s=90,
            )
        )
    finally:
        if som_path != capture.chemin:
            som_path.unlink(missing_ok=True)

    donnees_vis = extraire_json(res_vision.texte)
    idx_vis = _extraire_index_valide(donnees_vis, par_index)
    if idx_vis is not None:
        c = par_index[idx_vis]
        return {
            "x": c["x"],
            "y": c["y"],
            "w": c["w"],
            "h": c["h"],
            "label": c["label"],
            "source": "vision",
            "raison": str((donnees_vis or {}).get("raison") or ""),
        }

    if isinstance(donnees_vis, dict) and all(k in donnees_vis for k in ("x", "y")):
        try:
            facteur = capture.echelle if capture.echelle > 0 else 1.0
            rx = round(capture.origine_x + float(donnees_vis["x"]) * facteur)
            ry = round(capture.origine_y + float(donnees_vis["y"]) * facteur)
            rw = max(12, round(float(donnees_vis.get("w") or 80) * facteur))
            rh = max(12, round(float(donnees_vis.get("h") or 32) * facteur))
            return {
                "x": rx,
                "y": ry,
                "w": rw,
                "h": rh,
                "label": str(donnees_vis.get("label") or objectif_propre),
                "source": "vision",
                "raison": str(donnees_vis.get("raison") or ""),
            }
        except (TypeError, ValueError):
            pass

    raise ValueError(f"Élément introuvable pour l'objectif : {objectif_propre}")


def _point_dans_rect(px: float, py: float, rect: dict[str, int]) -> bool:
    """Vérifie si ``(px, py)`` tombe dans le rectangle (avec une tolérance de 6 px)."""
    rx = int(rect.get("x", 0)) - _TOLERANCE_CLIC_PX
    ry = int(rect.get("y", 0)) - _TOLERANCE_CLIC_PX
    rw = int(rect.get("w", 0)) + 2 * _TOLERANCE_CLIC_PX
    rh = int(rect.get("h", 0)) + 2 * _TOLERANCE_CLIC_PX
    return rx <= px <= rx + rw and ry <= py <= ry + rh


def _demarrer_ecoute_souris(attente: _AttenteGuide) -> Any | None:
    """Démarre ``pynput.mouse.Listener`` dans un thread pour détecter un clic dans ``attente.rect``."""
    try:
        from pynput import mouse
    except ImportError:
        logger.debug("Module 'pynput' absent ; attente uniquement via WebSocket guide_clic.")
        return None

    def _on_click(x: int, y: int, button: Any, pressed: bool) -> bool | None:
        if not pressed:
            return None
        if button != mouse.Button.left:
            return None
        if _point_dans_rect(x, y, attente.rect):
            attente.clique = True
            if not attente.loop.is_closed():
                attente.loop.call_soon_threadsafe(attente.event.set)
            # Retourner False arrête immédiatement le Listener pynput.
            return False
        return None

    try:
        listener = mouse.Listener(on_click=_on_click)
        listener.start()
        return listener
    except Exception as exc:
        logger.debug("Impossible de démarrer pynput.mouse.Listener (%s).", exc)
        return None


async def _arreter_ecoute_souris(listener: Any | None) -> None:
    """Arrête proprement un ``pynput.mouse.Listener`` sans bloquer la boucle asyncio."""
    if listener is None:
        return
    try:
        listener.stop()
        await asyncio.to_thread(listener.join, 1.0)
    except Exception as exc:
        logger.debug("Arrêt du listener souris ignoré (%s).", exc)


async def handle_ws_guide_clic(ws: WebSocket | None, data: dict[str, Any]) -> None:
    """Gestionnaire WebSocket pour les messages client ``guide_clic{id, index}``.

    Args:
        ws: Connexion WebSocket cliente (optionnelle).
        data: Dictionnaire contenant ``id`` du guidage actif.
    """
    del ws
    if not isinstance(data, dict):
        return
    guide_id = str(data.get("id") or "").strip()
    if not guide_id:
        return
    attente = _guides_actifs.get(guide_id)
    if attente is not None:
        attente.clique = True
        attente.event.set()


on_client_message("guide_clic", handle_ws_guide_clic)


async def guider(
    objectif: str,
    etapes: list[str] | None = None,
    timeout_etape_s: float = _TIMEOUT_ETAPE_DEFAUT_S,
    *,
    guide_id: str | None = None,
) -> dict[str, Any]:
    """Guide visuellement l'utilisateur étape par étape via le calque HUD sans jamais cliquer.

    Pour chaque étape, localise l'élément cible, diffuse ``guide`` avec le cadre lumineux
    et ``attendre_clic=True``, puis attend soit un clic souris réel dans le rectangle (via
    ``pynput.mouse.Listener``), soit l'événement WS ``guide_clic`` (délai max 120 s/étape).
    Diffuse ``guide_effacer`` à la fin.

    Args:
        objectif: Objectif général affiché si ``etapes`` n'est pas fourni.
        etapes: Liste ordonnée d'étapes cibles optionnelle.
        timeout_etape_s: Délai maximal d'attente par étape en secondes (défaut 120 s).
        guide_id: Identifiant imposé optionnel (pratique pour lancement en tâche de fond).

    Returns:
        Dictionnaire ``{"ok", "guide_id", "etapes_completees", "total_etapes", ...}``.

    Effets de bord:
        Diffuse ``narration``, ``guide`` et ``guide_effacer`` sur le hub WebSocket et
        écoute temporairement les clics souris via ``pynput``.
    """
    gid = guide_id or f"guide-{uuid.uuid4().hex[:10]}"
    liste_etapes = (
        [str(e).strip() for e in etapes if str(e).strip()]
        if etapes
        else [str(objectif or "").strip()]
    )
    if not liste_etapes or not liste_etapes[0]:
        raise ValueError("Objectif ou liste d'étapes requis pour le mode Professeur.")

    total = len(liste_etapes)
    completees = 0
    loop = asyncio.get_running_loop()

    try:
        for idx, etape in enumerate(liste_etapes, start=1):
            await hub.broadcast(
                "narration",
                {"texte": f"Mode Professeur ({idx}/{total}) : recherche de « {etape} »…", "niveau": "info"},
            )
            cible = await trouver(etape)
            rect = {
                "x": int(cible["x"]),
                "y": int(cible["y"]),
                "w": int(cible["w"]),
                "h": int(cible["h"]),
            }
            attente = _AttenteGuide(event=asyncio.Event(), loop=loop, rect=rect)
            _guides_actifs[gid] = attente

            await hub.broadcast(
                "guide",
                {
                    "id": gid,
                    "boites": [
                        {
                            **rect,
                            "label": f"{idx}/{total} {etape}",
                            "couleur": "#22d3ee",
                        }
                    ],
                    "attendre_clic": True,
                },
            )
            await hub.broadcast(
                "narration",
                {"texte": f"Clique sur « {etape} » ({idx}/{total}).", "niveau": "info"},
            )

            listener = _demarrer_ecoute_souris(attente)
            try:
                await asyncio.wait_for(attente.event.wait(), timeout=timeout_etape_s)
                completees += 1
            except TimeoutError:
                await hub.broadcast(
                    "narration",
                    {
                        "texte": f"Guidage expiré à l'étape {idx}/{total} ({timeout_etape_s:g} s).",
                        "niveau": "avertissement",
                    },
                )
                return {
                    "ok": False,
                    "guide_id": gid,
                    "etapes_completees": completees,
                    "total_etapes": total,
                    "erreur": "Délai d'attente du clic dépassé",
                }
            finally:
                await _arreter_ecoute_souris(listener)
                _guides_actifs.pop(gid, None)

        await hub.broadcast(
            "narration",
            {"texte": "Guidage terminé avec succès.", "niveau": "info"},
        )
        return {
            "ok": True,
            "guide_id": gid,
            "etapes_completees": completees,
            "total_etapes": total,
        }
    finally:
        _guides_actifs.pop(gid, None)
        await hub.broadcast("guide_effacer", {"id": gid})


def demarrer_guidage(
    objectif: str,
    etapes: list[str] | None = None,
    timeout_etape_s: float = _TIMEOUT_ETAPE_DEFAUT_S,
) -> str:
    """Lance une session ``guider()`` en tâche de fond et retourne immédiatement son ``guide_id``.

    Args:
        objectif: Objectif général du guidage.
        etapes: Étapes cibles ordonnées (optionnel).
        timeout_etape_s: Timeout par étape en secondes.

    Returns:
        Identifiant ``guide_id`` créé pour suivre/valider le guidage.
    """
    gid = f"guide-{uuid.uuid4().hex[:10]}"

    async def _runner() -> None:
        try:
            await guider(objectif, etapes=etapes, timeout_etape_s=timeout_etape_s, guide_id=gid)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Échec du guidage %s : %s", gid, exc)
            await hub.broadcast("narration", {"texte": f"Guidage interrompu : {exc}", "niveau": "erreur"})

    tache = asyncio.create_task(_runner())
    _taches_guidage.add(tache)
    tache.add_done_callback(_taches_guidage.discard)
    return gid
