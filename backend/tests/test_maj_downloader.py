"""Tests du téléchargeur HTTPS sécurisé des mises à jour (hôtes, taille, SHA-256)."""

import hashlib
from pathlib import Path

import httpx
import pytest

from backend.update import downloader
from backend.update.downloader import ErreurTelechargement


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/a/b/releases/latest",
        "https://github.com/a/b/releases/download/v1.0.0/app.zip",
        "https://objects.githubusercontent.com/github-production-release-asset/123",
        "https://release-assets.githubusercontent.com/asset.exe",
    ],
)
def test_hotes_autorises_acceptes(url: str) -> None:
    """Les 4 hôtes GitHub autorisés en HTTPS sont acceptés."""
    assert downloader.valider_url_telechargement(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://github.com/a/b/file.zip",
        "file:///C:/temp/app.zip",
        "https://evil.example/app.zip",
        "https://github.com.evil.example/app.zip",
        "https://127.0.0.1:5230/app.zip",
        "https://user:pass" + chr(64) + "github.com/a/b/app.zip",
    ],
)
def test_hotes_et_schemas_non_autorises_refuses(url: str) -> None:
    """Refuse tout hôte hors liste blanche, HTTP non chiffré ou identifiants intégrés."""
    with pytest.raises(ErreurTelechargement):
        downloader.valider_url_telechargement(url)


async def test_redirect_vers_hote_refuse(tmp_path: Path) -> None:
    """Refuse une redirection 302 pointant vers un hôte externe non autorisé."""
    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(302, headers={"Location": "https://evil.example/payload.zip"})

    dest = tmp_path / "app.zip"
    with pytest.raises(ErreurTelechargement, match="Hôte non autorisé"):
        await downloader.telecharger_fichier(
            url="https://github.com/a/b/releases/download/v1/app.zip",
            destination=dest,
            sha256_attendu="0" * 64,
            transport=httpx.MockTransport(_handler),
        )
    assert not dest.exists()


async def test_taille_excessive_content_length_et_flux_refuses(tmp_path: Path) -> None:
    """Refuse un téléchargement dépassant le plafond (Content-Length et comptage en flux)."""
    dest = tmp_path / "gros.zip"

    def _handler_cl(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, headers={"Content-Length": str(downloader.TAILLE_MAX_OCTETS + 1)}, content=b"x")

    with pytest.raises(ErreurTelechargement, match="supérieure au plafond"):
        await downloader.telecharger_fichier(
            url="https://github.com/a/b/releases/download/v1/gros.zip",
            destination=dest,
            sha256_attendu="a" * 64,
            transport=httpx.MockTransport(_handler_cl),
        )

    def _handler_stream(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"0123456789ABCDEF")

    with pytest.raises(ErreurTelechargement, match="supérieure"):
        await downloader.telecharger_fichier(
            url="https://github.com/a/b/releases/download/v1/stream.zip",
            destination=dest,
            sha256_attendu=hashlib.sha256(b"0123456789ABCDEF").hexdigest(),
            taille_max=8,
            transport=httpx.MockTransport(_handler_stream),
        )
    assert list(tmp_path.iterdir()) == []


async def test_sha256_faux_refuse_et_part_nettoye(tmp_path: Path) -> None:
    """Refuse un fichier dont le SHA-256 diffère du manifest et supprime le fichier .part."""
    contenu = b"contenu authentique"
    dest = tmp_path / "app.zip"

    def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=contenu)

    with pytest.raises(ErreurTelechargement, match="SHA-256"):
        await downloader.telecharger_fichier(
            url="https://objects.githubusercontent.com/asset/app.zip",
            destination=dest,
            sha256_attendu="f" * 64,
            taille_attendue=len(contenu),
            transport=httpx.MockTransport(_handler),
        )
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


async def test_telechargement_valide_avec_redirection_autorisee_et_progression(tmp_path: Path) -> None:
    """Télécharge en flux via redirection GitHub autorisée, vérifie SHA-256 et progression."""
    contenu = b"jarvis-release-payload" * 50
    sha = hashlib.sha256(contenu).hexdigest()
    dest = tmp_path / "updates" / "app.zip"
    progres: list[tuple[int, int | None]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "github.com":
            return httpx.Response(
                302,
                headers={"Location": "https://release-assets.githubusercontent.com/file.zip"},
            )
        return httpx.Response(200, headers={"Content-Length": str(len(contenu))}, content=contenu)

    res = await downloader.telecharger_fichier(
        url="https://github.com/OWNER/jarvis-aios/releases/download/v1.0.0/app.zip",
        destination=dest,
        sha256_attendu=sha,
        taille_attendue=len(contenu),
        on_progress=lambda r, t: progres.append((r, t)),
        transport=httpx.MockTransport(_handler),
    )
    assert res == dest
    assert dest.read_bytes() == contenu
    assert progres[-1] == (len(contenu), len(contenu))
