"""Session LLM locale (serveur compatible OpenAI, ex. LM Studio) pour la voie vocale rapide.

Même interface que :class:`backend.llm.agy_session.SessionAgyChaude` (``demarrer``, ``arreter``,
``envoyer``, ``est_active``, ``tours_sur_processus``, ``modele``) afin d'être interchangeable
dans le pipeline vocal. Le contexte est conservé en mémoire (historique borné).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from backend.llm.base import EngineError

logger = logging.getLogger(__name__)

URL_LOCALE_DEFAUT = "http://127.0.0.1:1234/v1"
HISTORIQUE_MAX_MESSAGES = 16
TIMEOUT_TOUR_S = 60.0


def _demarrer_serveur_lms() -> bool:
    """Lance le serveur LM Studio (``lms server start``) et attend qu'il réponde ; vrai si démarré."""
    lms = Path(os.environ.get("USERPROFILE", "~")).expanduser() / ".lmstudio" / "bin" / ("lms.exe" if sys.platform == "win32" else "lms")
    if not lms.is_file():
        return False
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        res = subprocess.run(  # noqa: S603 - binaire local connu
            [str(lms), "server", "start"], capture_output=True, timeout=60, creationflags=flags, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return res.returncode == 0


class SessionLLMLocale:
    """Conversation vocale avec un petit modèle local servi en HTTP (streaming SSE)."""

    def __init__(
        self,
        *,
        modele: str = "",
        url: str = URL_LOCALE_DEFAUT,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_TOUR_S,
    ) -> None:
        """Prépare la session sans contacter le serveur.

        Args:
            modele: Identifiant du modèle côté serveur ; vide = premier modèle chargé/disponible.
            url: Base OpenAI (``…/v1``) du serveur local.
            transport: Transport httpx injectable (tests).
            timeout_s: Délai maximal d'un tour complet.
        """
        self._modele = (modele or "").strip()
        self._base = (url or URL_LOCALE_DEFAUT).strip().rstrip("/")
        self._transport = transport
        self._timeout_s = float(timeout_s)
        self._actif = False
        self._systeme: str | None = None
        self._historique: list[dict[str, str]] = []
        self._tours = 0

    @property
    def modele(self) -> str:
        """Identifiant du modèle utilisé (peut être vide avant ``demarrer``)."""
        return self._modele

    @property
    def est_active(self) -> bool:
        """Vrai une fois le serveur joint par ``demarrer``."""
        return self._actif

    @property
    def tours_sur_processus(self) -> int:
        """Nombre de tours réussis depuis le démarrage (0 → le premier message contient la consigne)."""
        return self._tours

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)

    async def _lister_modeles(self) -> list[str]:
        async with self._client() as client:
            rep = await client.get(f"{self._base}/models")
            rep.raise_for_status()
            return [str(m.get("id") or "") for m in rep.json().get("data", [])]

    async def demarrer(self) -> None:
        """Vérifie que le serveur répond (le démarre via ``lms`` si besoin) et résout le modèle.

        Raises:
            EngineError: Serveur injoignable ou aucun modèle disponible.
        """
        if self._actif:
            return
        try:
            try:
                ids = await self._lister_modeles()
            except Exception:
                if self._transport is not None or not await asyncio.to_thread(_demarrer_serveur_lms):
                    raise
                ids = await self._lister_modeles()
        except Exception as exc:
            raise EngineError(f"LLM local injoignable ({exc})", transitoire=True) from exc
        ids = [i for i in ids if i and "embed" not in i.lower()]
        if self._modele:
            if self._modele not in ids:
                raise EngineError(f"Modèle local « {self._modele} » absent du serveur", transitoire=False)
        elif ids:
            self._modele = ids[0]
        else:
            raise EngineError("Aucun modèle local disponible", transitoire=False)
        self._actif = True

    async def prechauffer(self) -> None:
        """Charge le modèle en mémoire GPU (requête d'un jeton) pour éviter le délai du premier tour."""
        if not self._actif:
            await self.demarrer()
        corps = {"model": self._modele, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
        try:
            async with self._client() as client:
                await client.post(f"{self._base}/chat/completions", json=corps)
        except Exception as exc:
            logger.info("Préchauffage LLM local sans effet (%s).", exc)

    async def arreter(self) -> None:
        """Marque la session comme arrêtée (aucun processus à tuer)."""
        self._actif = False

    async def envoyer(self, texte: str) -> AsyncIterator[str]:
        """Envoie un tour et streame les fragments de texte.

        Le premier tour (``tours_sur_processus == 0``) contient la consigne vocale suivie du
        message utilisateur ; elle est conservée comme message système.

        Raises:
            EngineError: Erreur HTTP ou réponse invalide du serveur.
        """
        if not texte or not texte.strip():
            return
        if not self._actif:
            await self.demarrer()

        if self._tours == 0 and self._systeme is None:
            self._systeme = texte
            utilisateur = "Bonjour."
            if "DEMANDE UTILISATEUR :" in texte:
                debut, _, reste = texte.rpartition("DEMANDE UTILISATEUR :")
                self._systeme = debut.strip()
                utilisateur = reste.lstrip(" :\n").strip() or utilisateur
        else:
            utilisateur = texte

        messages: list[dict[str, str]] = []
        if self._systeme:
            messages.append({"role": "system", "content": self._systeme})
        messages.extend(self._historique[-HISTORIQUE_MAX_MESSAGES:])
        messages.append({"role": "user", "content": utilisateur})

        corps: dict[str, Any] = {
            "model": self._modele,
            "messages": messages,
            "stream": True,
            "temperature": 0.6,
            "max_tokens": 400,
        }
        morceaux: list[str] = []
        try:
            async with self._client() as client:
                async with client.stream("POST", f"{self._base}/chat/completions", json=corps) as rep:
                    if rep.status_code >= 400:
                        await rep.aread()
                        raise EngineError(f"LLM local HTTP {rep.status_code}", transitoire=True)
                    async for ligne in rep.aiter_lines():
                        if not ligne.startswith("data:"):
                            continue
                        charge = ligne[5:].strip()
                        if charge == "[DONE]":
                            break
                        try:
                            delta = json.loads(charge)["choices"][0]["delta"].get("content") or ""
                        except (ValueError, KeyError, IndexError):
                            continue
                        if delta:
                            morceaux.append(delta)
                            yield delta
        except EngineError:
            self._actif = False
            raise
        except Exception as exc:
            self._actif = False
            raise EngineError(f"LLM local : {exc}", transitoire=True) from exc

        self._historique.append({"role": "user", "content": utilisateur})
        self._historique.append({"role": "assistant", "content": "".join(morceaux)})
        self._tours += 1
