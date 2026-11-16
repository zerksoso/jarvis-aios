"""Gestionnaire léger de hooks de démarrage et d'arrêt pour les modules Jarvis."""

import inspect
from collections.abc import Callable
from typing import Any

HookFn = Callable[[], Any]

on_startup_hooks: list[HookFn] = []
on_shutdown_hooks: list[HookFn] = []


def register_startup(fn: HookFn) -> HookFn:
    """Enregistre un callback synchrone ou asynchrone exécuté au démarrage du serveur.

    Args:
        fn: Fonction ou coroutine sans argument à appeler lors du lifespan startup.

    Returns:
        La fonction enregistrée (permet l'usage en décorateur).
    """
    if fn not in on_startup_hooks:
        on_startup_hooks.append(fn)
    return fn


def register_shutdown(fn: HookFn) -> HookFn:
    """Enregistre un callback synchrone ou asynchrone exécuté à l'arrêt du serveur.

    Args:
        fn: Fonction ou coroutine sans argument à appeler lors du lifespan shutdown.

    Returns:
        La fonction enregistrée.
    """
    if fn not in on_shutdown_hooks:
        on_shutdown_hooks.append(fn)
    return fn


async def executer_hooks(hooks: list[HookFn]) -> None:
    """Exécute séquentiellement une liste de hooks synchrones ou asynchrones.

    Args:
        hooks: Liste des callbacks à invoquer.
    """
    for fn in list(hooks):
        resultat = fn()
        if inspect.isawaitable(resultat):
            await resultat
