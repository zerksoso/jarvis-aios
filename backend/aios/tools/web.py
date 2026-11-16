"""Outils de recherche web (DuckDuckGo via ``ddgs``) et lecture de page (``httpx``)."""

import asyncio
import html
import re
from typing import Any
from urllib.parse import urlparse

import httpx
from ddgs import DDGS

from backend.aios.tools_registry import ContexteOutil, outil

_MAX_PAGE_CHARS = 8000
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|svg)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _chercher_ddg(q: str, n: int) -> list[dict[str, str]]:
    """Exécute une recherche texte synchrone via ``DDGS``."""
    nb = max(1, min(int(n), 20))
    with DDGS(timeout=10) as client:
        bruts = client.text(q, max_results=nb)
    resultats: list[dict[str, str]] = []
    for item in bruts:
        if not isinstance(item, dict):
            continue
        resultats.append(
            {
                "titre": str(item.get("title") or ""),
                "url": str(item.get("href") or item.get("url") or ""),
                "extrait": str(item.get("body") or item.get("snippet") or ""),
            }
        )
    return resultats


def _html_vers_texte(corps_html: str) -> str:
    """Convertit un document HTML en texte brut lisible limité à 8 000 caractères."""
    sans_scripts = _SCRIPT_STYLE_RE.sub(" ", corps_html)
    sans_balises = _TAG_RE.sub(" ", sans_scripts)
    decode = html.unescape(sans_balises)
    normalise = " ".join(decode.split())
    return normalise[:_MAX_PAGE_CHARS]


@outil(
    nom="web.chercher",
    description="Recherche des informations récentes sur le web via DuckDuckGo.",
    parametres={"q": "str — requête web", "n": "int — nombre de résultats (défaut 5)"},
    risque="lecture",
)
async def chercher_web(ctx: ContexteOutil, q: str, n: int = 5) -> list[dict[str, str]]:
    """Interroge DuckDuckGo dans un thread secondaire et retourne titre, URL et extrait."""
    del ctx
    if not q or not q.strip():
        raise ValueError("La requête de recherche web ne peut pas être vide.")
    return await asyncio.to_thread(_chercher_ddg, q.strip(), n)


@outil(
    nom="web.lire",
    description="Télécharge une page web HTTP(S) et en extrait le texte brut (max 8 000 car.).",
    parametres={"url": "str — URL http:// ou https:// à lire"},
    risque="lecture",
)
async def lire_web(ctx: ContexteOutil, url: str) -> dict[str, Any]:
    """Récupère une page web en 15 s max et en extrait le texte brut."""
    del ctx
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"URL invalide (http/https requis) : {url!r}")

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        reponse = await client.get(
            url.strip(),
            headers={"User-Agent": "Jarvis-AIOS/0.1 (+http://127.0.0.1)"},
        )
        reponse.raise_for_status()
        content_type = reponse.headers.get("content-type", "")
        texte_brut = reponse.text

    contenu = _html_vers_texte(texte_brut) if "html" in content_type.lower() else " ".join(texte_brut.split())[:_MAX_PAGE_CHARS]
    return {
        "url": str(reponse.url),
        "statut": reponse.status_code,
        "texte": contenu,
    }
