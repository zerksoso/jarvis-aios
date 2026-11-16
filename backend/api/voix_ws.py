"""Route WebSocket ``/ws/voix`` pour les conversations vocales quasi temps réel."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend import security
from backend.voice.stream import SessionVocale

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voix"])

CODE_FERMETURE_REMPLACEE = 4000
CODE_FERMETURE_NON_AUTORISE = 4401


@dataclass
class _ConnexionActive:
    """Représente l'unique session vocale WebSocket active à un instant donné."""

    websocket: WebSocket
    session: SessionVocale
    stop_event: asyncio.Event
    loop: asyncio.AbstractEventLoop
    remplacee: bool = False


_connexion_active: _ConnexionActive | None = None


async def _fermer_connexion_remplacee(precedente: _ConnexionActive) -> None:
    """Ferme proprement une session remplacée sur sa propre boucle asyncio."""
    precedente.remplacee = True
    precedente.stop_event.set()
    with contextlib.suppress(Exception):
        await precedente.websocket.close(
            code=CODE_FERMETURE_REMPLACEE,
            reason="Session vocale remplacée par une nouvelle connexion",
        )
    await precedente.session.fermer()


def reset_etat_pour_tests() -> None:
    """Réinitialise la référence globale de session vocale active pour l'isolation des tests.

    Effets de bord:
        Met ``_connexion_active`` à ``None``.
    """
    global _connexion_active
    _connexion_active = None


@router.websocket("/ws/voix")
async def websocket_voix(
    websocket: WebSocket,
    token: str | None = None,
) -> None:
    """Point d'entrée WebSocket bidirectionnel pour l'audio PCM 16 kHz et les réponses TTS.

    Applique le même contrôle d'origine et de jeton que ``/ws`` (fermeture ``4401`` en cas
    de refus) et n'autorise qu'une seule session vocale active à la fois (fermeture ``4000``
    de la session précédente).

    Args:
        websocket: Connexion WebSocket FastAPI entrante.
        token: Jeton d'authentification passé en paramètre de requête ``?token=``.

    Effets de bord:
        Accepte la socket, ferme une éventuelle session précédente avec le code ``4000``,
        précharge Whisper et orchestre une ``SessionVocale`` jusqu'à la déconnexion.
    """
    global _connexion_active

    origin = websocket.headers.get("origin")
    if origin and not security.origine_est_valide(origin):
        await websocket.close(code=CODE_FERMETURE_NON_AUTORISE, reason="Origine non autorisée")
        return
    if not security.verifier_jeton(token):
        await websocket.close(code=CODE_FERMETURE_NON_AUTORISE, reason="Jeton invalide ou manquant")
        return

    await websocket.accept()

    session = SessionVocale(websocket)
    courant = _ConnexionActive(
        websocket=websocket,
        session=session,
        stop_event=asyncio.Event(),
        loop=asyncio.get_running_loop(),
    )

    precedente = _connexion_active
    _connexion_active = courant

    if precedente is not None:
        if precedente.loop is courant.loop:
            await _fermer_connexion_remplacee(precedente)
        else:
            fut = asyncio.run_coroutine_threadsafe(_fermer_connexion_remplacee(precedente), precedente.loop)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(fut.result, 5.0)

    try:
        await session.demarrer()
        while not courant.stop_event.is_set():
            tache_recv = asyncio.create_task(websocket.receive())
            tache_stop = asyncio.create_task(courant.stop_event.wait())
            faites, pendantes = await asyncio.wait(
                {tache_recv, tache_stop},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for tache in pendantes:
                tache.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tache

            if tache_stop in faites or courant.stop_event.is_set():
                break

            message = tache_recv.result()
            if message.get("type") == "websocket.disconnect":
                break

            donnees_binaires = message.get("bytes")
            if donnees_binaires is not None:
                await session.traiter_audio(donnees_binaires)
                continue

            donnees_texte = message.get("text")
            if donnees_texte is not None:
                continuer = await session.traiter_message_texte(donnees_texte)
                if not continuer:
                    with contextlib.suppress(Exception):
                        await websocket.close(code=1000, reason="Fin de session")
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("Erreur inattendue sur /ws/voix : %s", exc)
    finally:
        if _connexion_active is courant:
            _connexion_active = None
        await session.fermer()
