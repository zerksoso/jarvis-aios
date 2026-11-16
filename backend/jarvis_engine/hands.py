"""Exécution sécurisée d'actions PC (Computer Use) sous contrôle strict des garde-fous."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from backend.jarvis_engine import approvals, guardrails, uia, vision
from backend.ws_hub import hub

logger = logging.getLogger(__name__)

_ALIAS_TOUCHES: dict[str, str] = {
    "entree": "enter",
    "return": "enter",
    "echap": "esc",
    "escape": "esc",
    "suppr": "delete",
    "supprimer": "delete",
    "del": "delete",
    "espace": "space",
    "retour": "backspace",
    "retour arriere": "backspace",
    "control": "ctrl",
    "controle": "ctrl",
    "maj": "shift",
    "win": "winleft",
    "windows": "winleft",
    "gauche": "left",
    "droite": "right",
    "haut": "up",
    "bas": "down",
    "pgup": "pageup",
    "pgdn": "pagedown",
}

_EXECUTABLES_AUTORISES: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "calc": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "mspaint": ["mspaint.exe"],
}


def _configurer_pyautogui() -> Any:
    """Importe paresseusement ``pyautogui`` et applique ``FAILSAFE=True`` et ``PAUSE=0.15``."""
    try:
        import pyautogui
    except ImportError as exc:
        raise RuntimeError("Module 'pyautogui' indisponible pour le contrôle PC.") from exc

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.15
    return pyautogui


def _cliquer(x: int, y: int, bouton: str = "left") -> None:
    """Effectue un clic souris simple aux coordonnées physiques ``(x, y)``."""
    pag = _configurer_pyautogui()
    bouton_propre = bouton if bouton in ("left", "right", "middle") else "left"
    pag.click(int(x), int(y), button=bouton_propre)


def _double_cliquer(x: int, y: int, bouton: str = "left") -> None:
    """Effectue un double-clic souris aux coordonnées physiques ``(x, y)``."""
    pag = _configurer_pyautogui()
    bouton_propre = bouton if bouton in ("left", "right", "middle") else "left"
    pag.doubleClick(int(x), int(y), button=bouton_propre)


def _taper(texte: str) -> None:
    """Saisit du texte Unicode de manière fiable via le presse-papiers et ``Ctrl+V``."""
    try:
        import pyperclip
    except ImportError as exc:
        raise RuntimeError("Module 'pyperclip' indisponible pour la saisie de texte.") from exc

    pag = _configurer_pyautogui()
    pyperclip.copy(texte)
    time.sleep(0.05)
    pag.hotkey("ctrl", "v")


def _normaliser_touches(combinaison: str | list[str]) -> list[str]:
    """Découpe et traduit une combinaison clavier (ex. ``ctrl+entrée`` → ``['ctrl', 'enter']``)."""
    if isinstance(combinaison, list | tuple):
        morceaux = [str(k) for k in combinaison]
    else:
        morceaux = [p for p in str(combinaison or "").replace("-", "+").split("+") if p.strip()]

    touches: list[str] = []
    for m in morceaux:
        norm = guardrails.normaliser(m)
        if not norm:
            continue
        touches.append(_ALIAS_TOUCHES.get(norm, norm))
    return touches


def _touche(combinaison: str | list[str]) -> None:
    """Appuie sur une touche unique ou une combinaison de touches via ``pyautogui``."""
    touches = _normaliser_touches(combinaison)
    if not touches:
        raise ValueError("Combinaison de touches vide.")

    pag = _configurer_pyautogui()
    if len(touches) == 1:
        pag.press(touches[0])
    else:
        pag.hotkey(*touches)


def _defiler(dy: int, x: int | None = None, y: int | None = None) -> None:
    """Fait défiler la molette verticalement (positif = haut, négatif = bas)."""
    pag = _configurer_pyautogui()
    px = int(x) if x is not None else None
    py = int(y) if y is not None else None
    pag.scroll(int(dy), x=px, y=py)


# Nom d'exécutable réel de la fenêtre principale de chaque application de la liste blanche.
_PROCESSUS_FENETRE: dict[str, str] = {
    "notepad": "notepad.exe",
    "calc": "calculatorapp.exe",
    "explorer": "explorer.exe",
    "mspaint": "mspaint.exe",
    "chrome": "chrome.exe",
    "msedge": "msedge.exe",
    "obsidian": "obsidian.exe",
}


def _mettre_au_premier_plan(cle: str, delai_s: float = 6.0) -> bool:
    """Attend la fenêtre visible de l'application ``cle`` puis la met au premier plan.

    Windows refuse ``SetForegroundWindow`` à un processus d'arrière-plan : on simule
    l'appui sur Alt (astuce documentée) avant de demander le premier plan.
    """
    import ctypes
    import ctypes.wintypes as wt

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    exe_attendu = _PROCESSUS_FENETRE.get(cle, f"{cle}.exe")

    def _exe_de(hwnd: int) -> str:
        """Retourne le nom d'exécutable (minuscules) propriétaire de la fenêtre."""
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(520)
            taille = wt.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(taille)):
                return Path(buf.value).name.lower()
            return ""
        finally:
            kernel32.CloseHandle(h)

    fin = time.monotonic() + delai_s
    trouvees: list[int] = []
    while time.monotonic() < fin:
        trouvees.clear()

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def _cb(hwnd: int, _l: int) -> bool:
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0 and _exe_de(hwnd) == exe_attendu:
                trouvees.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        if trouvees:
            hwnd = trouvees[0]
            user32.keybd_event(0x12, 0, 0, 0)  # Alt enfoncé
            user32.keybd_event(0x12, 0, 2, 0)  # Alt relâché
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.4)
            return user32.GetForegroundWindow() == hwnd
        time.sleep(0.3)
    return False


def _ouvrir_application(nom: str) -> None:
    """Lance une application de la liste blanche puis place sa fenêtre au premier plan."""
    _lancer_application(nom)
    if sys.platform == "win32":
        _mettre_au_premier_plan(guardrails.normaliser(nom).removesuffix(".exe").strip())


def _lancer_application(nom: str) -> None:
    """Lance une application strictement limitée à la liste blanche de sécurité."""
    if any(sep in nom for sep in ("/", "\\", ":", "..", "\x00")):
        raise PermissionError(f"Chemin d'application interdit : {nom!r}")

    cle = guardrails.normaliser(nom).removesuffix(".exe").strip()
    if cle not in guardrails.APPS_LISTE_BLANCHE:
        raise PermissionError(f"Application hors liste blanche interdite : {nom!r}")

    if sys.platform != "win32":
        raise RuntimeError("L'ouverture d'applications Windows n'est disponible que sous Windows.")

    if cle in _EXECUTABLES_AUTORISES:
        subprocess.Popen(_EXECUTABLES_AUTORISES[cle])
        time.sleep(0.5)
        return

    if cle == "obsidian":
        startfile = getattr(os, "startfile", None)
        if startfile is not None:
            try:
                startfile("obsidian://")
                time.sleep(0.5)
                return
            except OSError:
                pass
        candidats: list[Path] = []
        if local_app := os.environ.get("LOCALAPPDATA"):
            candidats.extend(
                [
                    Path(local_app) / "Programs" / "Obsidian" / "Obsidian.exe",
                    Path(local_app) / "Obsidian" / "Obsidian.exe",
                ]
            )
        for exe in candidats:
            if exe.is_file():
                subprocess.Popen([str(exe)])
                time.sleep(0.5)
                return
        raise FileNotFoundError("Obsidian introuvable sur ce poste.")

    # chrome / msedge
    trouve = shutil.which(cle) or shutil.which(f"{cle}.exe")
    if trouve:
        subprocess.Popen([trouve])
        time.sleep(0.5)
        return

    chemins: list[Path] = []
    if cle == "chrome":
        for var, sous_chemin in (
            ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
            ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
            ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
        ):
            if base_dir := os.environ.get(var):
                chemins.append(Path(base_dir) / sous_chemin)
    else:
        for var, sous_chemin in (
            ("PROGRAMFILES(X86)", "Microsoft/Edge/Application/msedge.exe"),
            ("PROGRAMFILES", "Microsoft/Edge/Application/msedge.exe"),
        ):
            if base_dir := os.environ.get(var):
                chemins.append(Path(base_dir) / sous_chemin)

    for exe in chemins:
        if exe.is_file():
            subprocess.Popen([str(exe)])
            time.sleep(0.5)
            return

    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        startfile(f"{cle}.exe")
        time.sleep(0.5)
        return
    raise FileNotFoundError(f"Application introuvable : {cle}")


def _fenetre_correspond(fenetre: dict[str, Any], attendu: dict[str, Any] | None) -> tuple[bool, str]:
    """Vérifie que ``fenetre`` satisfait les contraintes ``titre_contient`` et ``processus``."""
    if not attendu:
        return True, ""

    titre_actuel = str(fenetre.get("titre") or "")
    proc_actuel = str(fenetre.get("processus") or "")

    titre_contient = attendu.get("titre_contient")
    if titre_contient:
        att_norm = guardrails.normaliser(str(titre_contient))
        act_norm = guardrails.normaliser(titre_actuel)
        if att_norm and att_norm not in act_norm:
            return (
                False,
                f"Fenêtre active inattendue : « {titre_actuel} » (attendu contenant « {titre_contient} »).",
            )

    proc_attendu = attendu.get("processus")
    if proc_attendu:
        att_proc = guardrails.normaliser(str(proc_attendu)).removesuffix(".exe").strip()
        act_proc = guardrails.normaliser(proc_actuel).removesuffix(".exe").strip()
        if att_proc and att_proc != act_proc and att_proc not in act_proc:
            return (
                False,
                f"Processus actif inattendu : « {proc_actuel} » (attendu « {proc_attendu} »).",
            )

    return True, ""


def _libelle_action(
    action_type: str,
    action: dict[str, Any],
    element: dict[str, Any] | None,
) -> str:
    """Formate la narration française décrivant l'action sur le point d'être exécutée."""
    nom_elem = str(element.get("nom") or "").strip() if isinstance(element, dict) else ""
    if action_type == "cliquer":
        if nom_elem:
            return f"Je clique sur « {nom_elem} »"
        return f"Je clique en ({action.get('x')}, {action.get('y')})"
    if action_type == "double_cliquer":
        if nom_elem:
            return f"Je double-clique sur « {nom_elem} »"
        return f"Je double-clique en ({action.get('x')}, {action.get('y')})"
    if action_type == "taper":
        nb = len(str(action.get("texte") or ""))
        return f"Je saisis du texte ({nb} caractères)"
    if action_type == "touche":
        comb = action.get("combinaison") or action.get("touches") or action.get("touche") or ""
        return f"J'appuie sur « {comb} »"
    if action_type == "defiler":
        return f"Je fais défiler l'écran ({action.get('dy', 0)})"
    if action_type == "ouvrir_application":
        nom_app = action.get("nom") or action.get("application") or ""
        return f"J'ouvre l'application « {nom_app} »"
    return f"J'exécute l'action « {action_type} »"


def _resumer_action_accord(
    action_type: str,
    action: dict[str, Any],
    element: dict[str, Any] | None,
    fenetre: dict[str, Any],
) -> str:
    """Construit un résumé clair pour la carte/modale d'approbation utilisateur."""
    titre = str(fenetre.get("titre") or fenetre.get("processus") or "fenêtre active")
    return f"{_libelle_action(action_type, action, element)} dans « {titre} »"


async def executer_action(
    action: dict[str, Any],
    attendu: dict[str, Any] | None = None,
    *,
    source: str = "jarvis",
    demander_accord_fn: Callable[[str, dict[str, Any], str], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Exécute une action PC encadrée par la boucle obligatoire capture → vérification → action → vérification.

    Ne contourne jamais ``guardrails.verifier`` : récupère d'abord la capture et la fenêtre
    active, vérifie ``attendu``, inspecte l'élément UIA ciblé (pour les clics), soumet
    l'action aux garde-fous (refus si ``interdit``, demande d'approbation si
    ``accord_requis``), exécute l'action bas niveau puis re-capture et vérifie l'état final.

    Args:
        action: Dictionnaire d'action (``type`` + paramètres).
        attendu: Contraintes optionnelles sur la fenêtre active (``titre_contient``, ``processus``).
        source: Identifiant de l'agent/source pour l'approbation utilisateur.
        demander_accord_fn: Callback asynchrone optionnel ``(resume, action, risque) -> bool``.

    Returns:
        Dictionnaire ``{"ok", "avant", "apres", "fenetre", "narration", ...}``.

    Effets de bord:
        Diffuse ``etat_jarvis`` (``action`` → ``repos``) et ``narration`` à chaque étape,
        capture l'écran dans ``RUNTIME/captures/`` et pilote clavier/souris si autorisé.
    """
    narration: list[str] = []

    async def _narrer(texte: str, niveau: str = "info") -> None:
        narration.append(texte)
        await hub.broadcast("narration", {"texte": texte, "niveau": niveau})

    await hub.broadcast("etat_jarvis", {"etat": "action"})
    try:
        # 1. Capture initiale avant toute vérification ou action.
        await _narrer("Je regarde l'écran…")
        try:
            cap_avant = await asyncio.to_thread(vision.capturer)
        except Exception as exc:
            await _narrer(f"Capture d'écran impossible : {exc}", "erreur")
            return {
                "ok": False,
                "erreur": str(exc),
                "avant": None,
                "apres": None,
                "fenetre": {},
                "narration": narration,
            }

        # 2. Lecture et validation de la fenêtre active.
        fenetre = await asyncio.to_thread(uia.fenetre_active)
        titre_fenetre = str(fenetre.get("titre") or "")
        proc_fenetre = str(fenetre.get("processus") or "")
        nom_fenetre = titre_fenetre or proc_fenetre or "inconnue"
        await _narrer(f"Je vérifie la fenêtre active : {nom_fenetre}")

        ok_fenetre, raison_fenetre = _fenetre_correspond(fenetre, attendu)
        if not ok_fenetre:
            await _narrer(f"Action annulée : {raison_fenetre}", "avertissement")
            return {
                "ok": False,
                "erreur": raison_fenetre,
                "avant": str(cap_avant.chemin),
                "apres": None,
                "fenetre": fenetre,
                "narration": narration,
            }

        # 3. Inspection UIA du point ciblé / focus puis validation par guardrails.verifier.
        action_dict = action if isinstance(action, dict) else {}
        action_type = guardrails.normaliser(str(action_dict.get("type") or action_dict.get("action") or ""))

        element: dict[str, Any] | None = None
        if action_type in ("cliquer", "double_cliquer"):
            if action_dict.get("x") is None or action_dict.get("y") is None:
                msg = "Coordonnées (x, y) requises pour un clic."
                await _narrer(msg, "erreur")
                return {
                    "ok": False,
                    "erreur": msg,
                    "avant": str(cap_avant.chemin),
                    "apres": None,
                    "fenetre": fenetre,
                    "narration": narration,
                }
            try:
                px = int(action_dict["x"])
                py = int(action_dict["y"])
            except (TypeError, ValueError):
                msg = "Coordonnées (x, y) invalides : entiers attendus."
                await _narrer(msg, "erreur")
                return {
                    "ok": False,
                    "erreur": msg,
                    "avant": str(cap_avant.chemin),
                    "apres": None,
                    "fenetre": fenetre,
                    "narration": narration,
                }
            element = await asyncio.to_thread(uia.element_au_point, px, py)
        elif action_type == "taper":
            try:
                element = await asyncio.to_thread(uia.element_focus)
            except Exception:
                element = None

        contexte = {
            "titre": titre_fenetre,
            "processus": proc_fenetre,
            "url": str(fenetre.get("url") or action_dict.get("url") or ""),
            "element": element,
        }
        decision = guardrails.verifier(action, contexte)

        if decision.statut == "interdit":
            await _narrer(f"Action bloquée : {decision.raison}", "erreur")
            return {
                "ok": False,
                "erreur": decision.raison,
                "statut_garde_fou": "interdit",
                "avant": str(cap_avant.chemin),
                "apres": None,
                "fenetre": fenetre,
                "narration": narration,
            }

        if decision.statut == "accord_requis":
            await _narrer(f"Accord requis : {decision.raison}", "avertissement")
            resume = _resumer_action_accord(action_type, action_dict, element, fenetre)
            if demander_accord_fn is not None:
                accord = await demander_accord_fn(resume, dict(action_dict), "action_pc")
            else:
                accord = await approvals.demander_accord(source, resume, dict(action_dict), "action_pc")
            if not accord:
                await _narrer("Action annulée : refusée par l'utilisateur.", "avertissement")
                return {
                    "ok": False,
                    "erreur": "Refusé par l'utilisateur",
                    "statut_garde_fou": "refuse",
                    "avant": str(cap_avant.chemin),
                    "apres": None,
                    "fenetre": fenetre,
                    "narration": narration,
                }
            # L'accord peut arriver longtemps après : la fenêtre active doit être restée la même.
            fenetre_apres_accord = await asyncio.to_thread(uia.fenetre_active)
            if (
                str(fenetre_apres_accord.get("titre") or "") != titre_fenetre
                or str(fenetre_apres_accord.get("processus") or "") != proc_fenetre
            ):
                msg = "La fenêtre active a changé pendant l'attente de l'accord : action annulée."
                await _narrer(msg, "avertissement")
                return {
                    "ok": False,
                    "erreur": msg,
                    "avant": str(cap_avant.chemin),
                    "apres": None,
                    "fenetre": fenetre_apres_accord,
                    "narration": narration,
                }

        # 4. Exécution effective après validation complète.
        await _narrer(_libelle_action(action_type, action_dict, element))
        try:
            if action_type == "cliquer":
                await asyncio.to_thread(
                    _cliquer,
                    int(action_dict["x"]),
                    int(action_dict["y"]),
                    str(action_dict.get("bouton") or "left"),
                )
            elif action_type == "double_cliquer":
                await asyncio.to_thread(
                    _double_cliquer,
                    int(action_dict["x"]),
                    int(action_dict["y"]),
                    str(action_dict.get("bouton") or "left"),
                )
            elif action_type == "taper":
                await asyncio.to_thread(_taper, str(action_dict.get("texte") or ""))
            elif action_type == "touche":
                comb = action_dict.get("combinaison") or action_dict.get("touches") or action_dict.get("touche") or ""
                await asyncio.to_thread(_touche, comb)
            elif action_type == "defiler":
                await asyncio.to_thread(
                    _defiler,
                    int(action_dict.get("dy", 0)),
                    action_dict.get("x"),
                    action_dict.get("y"),
                )
            elif action_type == "ouvrir_application":
                nom_app = str(action_dict.get("nom") or action_dict.get("application") or "")
                await asyncio.to_thread(_ouvrir_application, nom_app)
            else:
                raise ValueError(f"Type d'action non supporté : {action_type}")
        except Exception as exc:
            await _narrer(f"Échec pendant l'exécution : {exc}", "erreur")
            return {
                "ok": False,
                "erreur": str(exc),
                "avant": str(cap_avant.chemin),
                "apres": None,
                "fenetre": fenetre,
                "narration": narration,
            }

        # 5. Vérification post-action (nouvelle capture, fenêtre et diff d'image).
        await _narrer("Je vérifie le résultat…")
        cap_apres = await asyncio.to_thread(vision.capturer)
        fenetre_apres = await asyncio.to_thread(uia.fenetre_active)
        diff = await asyncio.to_thread(vision.comparer_captures, cap_avant, cap_apres)

        if attendu and action_type != "ouvrir_application":
            ok_apres, raison_apres = _fenetre_correspond(fenetre_apres, attendu)
            if not ok_apres:
                await _narrer(f"Vérification échouée : {raison_apres}", "avertissement")
                return {
                    "ok": False,
                    "erreur": raison_apres,
                    "avant": str(cap_avant.chemin),
                    "apres": str(cap_apres.chemin),
                    "fenetre": fenetre_apres,
                    "difference_image": round(diff, 4),
                    "changement_detecte": diff > 0.0005,
                    "narration": narration,
                }

        return {
            "ok": True,
            "avant": str(cap_avant.chemin),
            "apres": str(cap_apres.chemin),
            "fenetre": fenetre_apres,
            "difference_image": round(diff, 4),
            "changement_detecte": diff > 0.0005,
            "narration": narration,
        }
    finally:
        await hub.broadcast("etat_jarvis", {"etat": "repos"})
