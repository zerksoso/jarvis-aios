"""Téléchargement HTTPS en flux avec liste blanche d'hôtes, limite 600 Mo et contrôle SHA-256."""

import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

HOSTS_AUTORISES: frozenset[str] = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)

TAILLE_MAX_OCTETS: int = 600 * 1024 * 1024  # 600 Mo
TAILLE_CHUNK_OCTETS: int = 65_536
MAX_REDIRECTIONS: int = 5
USER_AGENT_MAJ: str = "JarvisAIOS-Updater"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

ProgressCallback = Callable[[int, int | None], Awaitable[None] | None]


class ErreurMiseAJour(Exception):
    """Erreur de base du sous-système de mise à jour Jarvis AIOS."""


class ErreurTelechargement(ErreurMiseAJour, ValueError):
    """Erreur levée lors de la validation d'URL, de taille ou de somme SHA-256."""


class ErreurInstallation(ErreurMiseAJour):
    """Erreur levée lors de la vérification de signature, extraction zip ou lancement."""


def valider_url_telechargement(url: str) -> str:
    """Vérifie qu'une URL utilise HTTPS sans identifiants et cible un hôte GitHub autorisé.

    Args:
        url: URL candidate.

    Returns:
        URL nettoyée si valide.

    Raises:
        ErreurTelechargement: Si le schéma n'est pas HTTPS, s'il y a des identifiants
            embarqués ou si l'hôte n'appartient pas à ``HOSTS_AUTORISES``.
    """
    propre = (url or "").strip()
    if not propre:
        raise ErreurTelechargement("URL de téléchargement vide.")

    parts = urlsplit(propre)
    if parts.scheme.lower() != "https":
        raise ErreurTelechargement(f"Schéma non autorisé (HTTPS requis) : {parts.scheme or '<vide>'}")
    if parts.username is not None or parts.password is not None:
        raise ErreurTelechargement("Les identifiants intégrés dans l'URL sont interdits.")

    hote = (parts.hostname or "").lower().rstrip(".")
    if not hote or hote not in HOSTS_AUTORISES:
        raise ErreurTelechargement(f"Hôte non autorisé pour le téléchargement : {hote or '<vide>'}")

    return propre


def calculer_sha256(chemin: Path) -> str:
    """Calcule l'empreinte SHA-256 hexadécimale minuscule d'un fichier local.

    Args:
        chemin: Fichier existant à hacher.

    Returns:
        Chaîne de 64 caractères hexadécimaux minuscules.
    """
    hasher = hashlib.sha256()
    with chemin.open("rb") as f:
        while True:
            bloc = f.read(TAILLE_CHUNK_OCTETS)
            if not bloc:
                break
            hasher.update(bloc)
    return hasher.hexdigest().lower()


async def _notifier_progress(
    callback: ProgressCallback | None,
    recus: int,
    total: int | None,
) -> None:
    """Invoque le callback de progression (synchrone ou asynchrone) si fourni."""
    if callback is None:
        return
    res = callback(recus, total)
    if res is not None:
        await res


async def requete_json_securisee(
    url: str,
    headers: dict[str, str] | None = None,
    taille_max: int = 2 * 1024 * 1024,
    timeout_s: float = 15.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Any:
    """Exécute un GET HTTPS JSON avec contrôle d'hôte sur l'URL initiale et les redirections.

    Args:
        url: URL HTTPS cible.
        headers: En-têtes HTTP complémentaires.
        taille_max: Taille maximale acceptée pour la réponse JSON (défaut 2 Mo).
        timeout_s: Délai HTTP par requête en secondes.
        transport: Transport ``httpx`` optionnel (tests sans réseau).

    Returns:
        Objet Python décodé depuis le corps JSON.

    Raises:
        ErreurTelechargement: Si l'URL/redirection est refusée, dépasse ``taille_max`` ou échoue.
    """
    url_courante = valider_url_telechargement(url)
    entetes = {"User-Agent": USER_AGENT_MAJ}
    if headers:
        entetes.update(headers)

    async with httpx.AsyncClient(
        timeout=timeout_s,
        follow_redirects=False,
        transport=transport,
    ) as client:
        for _ in range(MAX_REDIRECTIONS + 1):
            async with client.stream("GET", url_courante, headers=entetes) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise ErreurTelechargement("Redirection HTTP sans en-tête Location.")
                    url_courante = valider_url_telechargement(urljoin(url_courante, location))
                    continue

                if resp.status_code >= 400:
                    raise ErreurTelechargement(
                        f"Réponse HTTP {resp.status_code} lors de la requête vers {url_courante}"
                    )

                cl_brut = resp.headers.get("content-length")
                if cl_brut:
                    try:
                        if int(cl_brut) > taille_max:
                            raise ErreurTelechargement(
                                f"Réponse JSON trop volumineuse ({cl_brut} octets > {taille_max})."
                            )
                    except ValueError:
                        pass

                tampon = bytearray()
                async for chunk in resp.aiter_bytes(TAILLE_CHUNK_OCTETS):
                    tampon.extend(chunk)
                    if len(tampon) > taille_max:
                        raise ErreurTelechargement(
                            f"Réponse JSON supérieure à la limite autorisée ({taille_max} octets)."
                        )
                try:
                    return json.loads(bytes(tampon).decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise ErreurTelechargement(f"Corps JSON invalide : {exc}") from exc

    raise ErreurTelechargement("Trop de redirections HTTP.")


async def telecharger_fichier(
    url: str,
    destination: Path,
    sha256_attendu: str,
    taille_attendue: int | None = None,
    taille_max: int = TAILLE_MAX_OCTETS,
    on_progress: ProgressCallback | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 120.0,
) -> Path:
    """Télécharge un asset en flux HTTPS et valide hôte, taille (<= 600 Mo) et SHA-256.

    Écrit d'abord dans un fichier temporaire ``.part`` puis le renomme atomiquement vers
    ``destination`` uniquement lorsque toutes les vérifications réussissent.

    Args:
        url: URL HTTPS GitHub à télécharger.
        destination: Chemin final souhaité sur disque.
        sha256_attendu: Empreinte SHA-256 attendue (64 caractères hexadécimaux).
        taille_attendue: Taille exacte déclarée dans ``manifest.json`` (optionnelle).
        taille_max: Plafond absolu en octets (défaut 600 Mo).
        on_progress: Callback recevant ``(octets_recus, octets_totaux_ou_None)``.
        transport: Transport ``httpx`` optionnel pour tests isolés.
        timeout_s: Délai HTTP en secondes.

    Returns:
        Chemin ``destination`` du fichier vérifié.

    Raises:
        ErreurTelechargement: En cas d'hôte interdit, HTTP non-HTTPS, redirection non sûre,
            dépassement de taille ou SHA-256 invalide.

    Effets de bord:
        Crée les répertoires parents, écrit puis supprime le fichier temporaire ``.part``
        en cas d'échec, ou le déplace vers ``destination`` en cas de succès.
    """
    url_courante = valider_url_telechargement(url)
    hash_cible = (sha256_attendu or "").strip().lower()
    if not _SHA256_RE.match(hash_cible):
        raise ErreurTelechargement("Empreinte SHA-256 attendue invalide.")

    if taille_attendue is not None:
        if taille_attendue < 0 or taille_attendue > taille_max:
            raise ErreurTelechargement(
                f"Taille annoncée invalide ou excessive ({taille_attendue} octets > {taille_max})."
            )

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f"{destination.name}.{uuid.uuid4().hex[:8]}.part")
    entetes = {"User-Agent": USER_AGENT_MAJ}

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=False,
            transport=transport,
        ) as client:
            for _ in range(MAX_REDIRECTIONS + 1):
                async with client.stream("GET", url_courante, headers=entetes) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise ErreurTelechargement("Redirection HTTP sans en-tête Location.")
                        url_courante = valider_url_telechargement(urljoin(url_courante, location))
                        continue

                    if resp.status_code >= 400:
                        raise ErreurTelechargement(
                            f"Échec du téléchargement HTTP {resp.status_code} ({url_courante})."
                        )

                    total_estime: int | None = taille_attendue
                    cl_brut = resp.headers.get("content-length")
                    if cl_brut:
                        try:
                            cl_val = int(cl_brut)
                        except ValueError:
                            cl_val = -1
                        if cl_val > taille_max:
                            raise ErreurTelechargement(
                                f"Taille annoncée ({cl_val} octets) supérieure au plafond de {taille_max} octets."
                            )
                        if cl_val >= 0 and total_estime is None:
                            total_estime = cl_val

                    hasher = hashlib.sha256()
                    recus = 0
                    with tmp_path.open("wb") as sortie:
                        async for bloc in resp.aiter_bytes(TAILLE_CHUNK_OCTETS):
                            if not bloc:
                                continue
                            recus += len(bloc)
                            if recus > taille_max:
                                raise ErreurTelechargement(
                                    f"Téléchargement interrompu : taille supérieure à {taille_max} octets."
                                )
                            sortie.write(bloc)
                            hasher.update(bloc)
                            await _notifier_progress(on_progress, recus, total_estime)

                    if taille_attendue is not None and recus != taille_attendue:
                        raise ErreurTelechargement(
                            f"Taille téléchargée incohérente ({recus} octets != {taille_attendue} attendus)."
                        )

                    sha_calcule = hasher.hexdigest().lower()
                    if sha_calcule != hash_cible:
                        raise ErreurTelechargement(
                            f"Empreinte SHA-256 invalide (obtenu {sha_calcule}, attendu {hash_cible})."
                        )

                    tmp_path.replace(destination)
                    return destination

        raise ErreurTelechargement("Trop de redirections HTTP lors du téléchargement.")
    except httpx.HTTPError as exc:
        raise ErreurTelechargement(f"Erreur réseau pendant le téléchargement : {exc}") from exc
    finally:
        tmp_path.unlink(missing_ok=True)
