"""Vérification des releases GitHub, comparaison semver pure et lecture du manifest."""

import re
from dataclasses import dataclass
from typing import Any

import httpx

from backend import version as version_mod
from backend.update.downloader import (
    ErreurTelechargement,
    requete_json_securisee,
    valider_url_telechargement,
)

_SEMVER_RE = re.compile(
    r"^[vV]?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@dataclass(frozen=True, slots=True)
class AssetRelease:
    """Description et empreinte d'un fichier d'une release GitHub."""

    name: str
    sha256: str
    size: int
    url: str


@dataclass(frozen=True, slots=True)
class InfoRelease:
    """Synthèse d'une release distante évaluée face à la version locale."""

    version: str
    runtime_version: str
    notes: str
    legere: bool
    disponible: bool
    app_zip: AssetRelease
    setup: AssetRelease


def parser_semver(texte: str) -> tuple[int, int, int, tuple[str, ...]]:
    """Parse une chaîne SemVer 2.0.0 (avec préfixe ``v`` optionnel, sans dépendance externe).

    Args:
        texte: Chaîne de version (ex. ``"1.2.10"``, ``"v1.2.3-rc.1"``, ``"0.0.0-dev"``).

    Returns:
        Tuple ``(major, minor, patch, prerelease_parts)``.

    Raises:
        ValueError: Si la chaîne ne respecte pas le format SemVer.
    """
    propre = (texte or "").strip()
    m = _SEMVER_RE.match(propre)
    if not m:
        raise ValueError(f"Version semver invalide : {texte!r}")

    major = int(m.group(1))
    minor = int(m.group(2))
    patch = int(m.group(3))
    pre = tuple(m.group(4).split(".")) if m.group(4) else ()
    return major, minor, patch, pre


def comparer_semver(v1: str, v2: str) -> int:
    """Compare deux versions selon les règles de précédence SemVer 2.0.0.

    Args:
        v1: Première version.
        v2: Seconde version.

    Returns:
        ``-1`` si ``v1 < v2``, ``0`` si ``v1 == v2``, ``1`` si ``v1 > v2``.
    """
    maj1, min1, pat1, pre1 = parser_semver(v1)
    maj2, min2, pat2, pre2 = parser_semver(v2)

    if (maj1, min1, pat1) != (maj2, min2, pat2):
        return 1 if (maj1, min1, pat1) > (maj2, min2, pat2) else -1

    if not pre1 and not pre2:
        return 0
    if not pre1:
        return 1
    if not pre2:
        return -1

    for part1, part2 in zip(pre1, pre2, strict=False):
        num1 = part1.isdigit()
        num2 = part2.isdigit()
        if num1 and num2:
            i1, i2 = int(part1), int(part2)
            if i1 != i2:
                return 1 if i1 > i2 else -1
        elif num1 != num2:
            # SemVer 2.0.0 : un identifiant numérique a une précédence inférieure à un alphanumérique.
            return -1 if num1 else 1
        elif part1 != part2:
            return 1 if part1 > part2 else -1

    if len(pre1) == len(pre2):
        return 0
    return 1 if len(pre1) > len(pre2) else -1


def est_plus_recente(candidat: str, actuelle: str) -> bool:
    """Indique si ``candidat`` est strictement plus récente que ``actuelle`` (semver)."""
    return comparer_semver(candidat, actuelle) > 0


def _extraire_asset(
    manifest: dict[str, Any],
    cle: str,
    assets_par_nom: dict[str, str],
) -> AssetRelease:
    """Valide un bloc ``app_zip`` ou ``setup`` du manifest et résout son URL d'asset GitHub."""
    bloc = manifest.get(cle)
    if not isinstance(bloc, dict):
        raise ErreurTelechargement(f"Bloc '{cle}' manquant dans manifest.json.")

    nom = str(bloc.get("name") or "").strip()
    sha256 = str(bloc.get("sha256") or "").strip().lower()
    taille_brute = bloc.get("size")
    if not nom or not sha256 or isinstance(taille_brute, bool) or taille_brute is None:
        raise ErreurTelechargement(f"Métadonnées incomplètes pour '{cle}' dans manifest.json.")
    try:
        taille = int(taille_brute)
    except (TypeError, ValueError) as exc:
        raise ErreurTelechargement(f"Taille invalide pour '{cle}' dans manifest.json.") from exc

    url_asset = assets_par_nom.get(nom)
    if not url_asset:
        raise ErreurTelechargement(f"Asset '{nom}' introuvable dans la release GitHub.")
    url_valide = valider_url_telechargement(url_asset)
    return AssetRelease(name=nom, sha256=sha256, size=taille, url=url_valide)


async def verifier_release_github(
    depot: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InfoRelease:
    """Interroge la dernière release GitHub, télécharge ``manifest.json`` et compare les versions.

    Args:
        depot: Dépôt ``owner/repo`` explicite ; sinon résolu via ``version.depot_github()``.
        transport: Transport ``httpx`` optionnel (tests sans réseau).

    Returns:
        Instance ``InfoRelease`` prête pour l'état et l'installation.

    Raises:
        ErreurTelechargement: Si GitHub est inaccessible, la release sans manifest ou les URLs refusées.
        ValueError: Si la version du manifest ou le dépôt est invalide.

    Effets de bord:
        Effectue au plus deux requêtes HTTPS GET sans authentification vers GitHub.
    """
    depot_cible = depot or version_mod.depot_github()
    url_release = f"https://api.github.com/repos/{depot_cible}/releases/latest"
    try:
        release = await requete_json_securisee(
            url_release,
            headers={"Accept": "application/vnd.github+json"},
            transport=transport,
        )
    except httpx.HTTPError as exc:
        raise ErreurTelechargement(f"Impossible de joindre GitHub ({exc}).") from exc

    if not isinstance(release, dict):
        raise ErreurTelechargement("Réponse GitHub Releases invalide.")

    assets_bruts = release.get("assets")
    if not isinstance(assets_bruts, list):
        raise ErreurTelechargement("Aucun asset trouvé dans la release GitHub.")

    assets_par_nom: dict[str, str] = {}
    for item in assets_bruts:
        if not isinstance(item, dict):
            continue
        nom = str(item.get("name") or "").strip()
        url_dl = str(item.get("browser_download_url") or "").strip()
        if nom and url_dl:
            assets_par_nom[nom] = url_dl

    url_manifest = assets_par_nom.get("manifest.json")
    if not url_manifest:
        raise ErreurTelechargement("Fichier manifest.json absent des assets de la release.")

    try:
        manifest = await requete_json_securisee(url_manifest, transport=transport)
    except httpx.HTTPError as exc:
        raise ErreurTelechargement(f"Impossible de télécharger manifest.json ({exc}).") from exc

    if not isinstance(manifest, dict):
        raise ErreurTelechargement("Le fichier manifest.json n'est pas un objet JSON valide.")

    version_distante = str(manifest.get("version") or release.get("tag_name") or "").strip()
    if not version_distante:
        raise ErreurTelechargement("Version absente de manifest.json.")
    parser_semver(version_distante)

    runtime_distant = str(manifest.get("runtime_version") or "").strip()
    notes = str(manifest.get("notes") or release.get("body") or "").strip()

    app_zip = _extraire_asset(manifest, "app_zip", assets_par_nom)
    setup = _extraire_asset(manifest, "setup", assets_par_nom)

    version_locale = version_mod.version_actuelle()
    disponible = est_plus_recente(version_distante, version_locale)

    runtime_local = version_mod.runtime_version_actuelle()
    legere = bool(runtime_local and runtime_distant and runtime_local == runtime_distant)

    return InfoRelease(
        version=version_distante.lstrip("vV"),
        runtime_version=runtime_distant,
        notes=notes,
        legere=legere,
        disponible=disponible,
        app_zip=app_zip,
        setup=setup,
    )
