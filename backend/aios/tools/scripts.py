"""Outil d'exécution sécurisée des scripts autorisés dans ``aios/scripts/``."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend import config
from backend.aios.tools_registry import ContexteOutil, accord_deja_obtenu, outil

_EXTENSIONS_AUTORISEES = {".py", ".ps1"}
_MAX_SORTIE_CHARS = 4000
_TIMEOUT_S = 60


def _valider_chemin_script(nom: str) -> Path:
    """Valide strictement le nom de script et résout son chemin sous ``SCRIPTS_DIR``."""
    if not isinstance(nom, str) or not nom.strip():
        raise ValueError("Nom de script vide.")

    propre = nom.strip()
    if "/" in propre or "\\" in propre or ".." in propre or ":" in propre or "\x00" in propre:
        raise ValueError(f"Nom de script invalide ou chemin interdit : {nom!r}")

    suffixe = Path(propre).suffix.lower()
    if suffixe not in _EXTENSIONS_AUTORISEES:
        raise ValueError(f"Extension non autorisée ({suffixe or 'aucune'}) : uniquement .py ou .ps1")

    base = config.SCRIPTS_DIR.resolve()
    cible = (base / propre).resolve()
    if not cible.is_relative_to(base) or cible.parent != base:
        raise ValueError("Le script doit résider directement dans SCRIPTS_DIR.")
    if not cible.is_file():
        raise FileNotFoundError(f"Script introuvable : {propre}")
    return cible


def _executer_subprocess(chemin: Path, args: list[str]) -> dict[str, Any]:
    """Lance le script dans un sous-processus sans fenêtre avec délai de 60 s."""
    if chemin.suffix.lower() == ".py":
        cmd = [sys.executable, str(chemin), *args]
    else:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(chemin),
            *args,
        ]

    env = os.environ.copy()
    env["JARVIS_ROOT"] = str(config.ROOT)

    creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.run(
        cmd,
        cwd=str(config.SCRIPTS_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_S,
        creationflags=creation_flags,
        check=False,
    )

    sortie = proc.stdout or ""
    if proc.stderr:
        sortie = f"{sortie}\n[stderr]\n{proc.stderr}".strip() if sortie else proc.stderr
    if len(sortie) > _MAX_SORTIE_CHARS:
        sortie = sortie[: _MAX_SORTIE_CHARS - 14] + "\n… [tronqué]"

    return {
        "script": chemin.name,
        "returncode": proc.returncode,
        "sortie": sortie,
    }


@outil(
    nom="script.executer",
    description="Exécute un script .py ou .ps1 présent dans aios/scripts/ après accord utilisateur.",
    parametres={
        "nom": "str — nom de fichier simple (.py ou .ps1) dans aios/scripts/",
        "args": "list[str] — arguments optionnels passés au script",
    },
    risque="irreversible",
)
async def executer_script(
    ctx: ContexteOutil,
    nom: str,
    args: list[str] | None = None,
) -> dict[str, Any]:
    """Valide le nom du script, vérifie l'accord utilisateur et l'exécute avec timeout 60 s."""
    chemin = _valider_chemin_script(nom)
    args_str = [str(a) for a in (args or [])]

    if not accord_deja_obtenu():
        accord = await ctx.demander_accord(
            f"Exécuter le script {chemin.name}",
            {"outil": "script.executer", "args": {"nom": chemin.name, "args": args_str}},
            "irreversible",
        )
        if not accord:
            raise PermissionError("Refusé par l'utilisateur")

    return await asyncio.to_thread(_executer_subprocess, chemin, args_str)
