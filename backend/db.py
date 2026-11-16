"""Couche d'accès SQLite (WAL, thread-safe) et schéma de la base Jarvis."""

import asyncio
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from backend import config

_write_lock = threading.Lock()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    type TEXT CHECK(type IN ('agent', 'canal')),
    cible_id TEXT,
    titre TEXT,
    cree_le TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT,
    role TEXT,
    auteur TEXT,
    contenu TEXT,
    meta TEXT DEFAULT '{}',
    cree_le TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id
    ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS sessions_moteur (
    cle TEXT,
    modele TEXT,
    session_id TEXT,
    maj_le TEXT,
    PRIMARY KEY (cle, modele)
);

CREATE TABLE IF NOT EXISTS usage_llm (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    moteur TEXT,
    modele TEXT,
    input_tokens INT,
    output_tokens INT,
    duree REAL,
    ok INT,
    erreur TEXT
);

CREATE TABLE IF NOT EXISTS runs_routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id TEXT,
    debut TEXT,
    fin TEXT,
    statut TEXT,
    sortie_path TEXT,
    resume TEXT,
    erreur TEXT
);

CREATE TABLE IF NOT EXISTS approbations (
    id TEXT PRIMARY KEY,
    source TEXT,
    action TEXT,
    resume TEXT,
    risque TEXT,
    statut TEXT,
    cree_le TEXT,
    decide_le TEXT
);

CREATE TABLE IF NOT EXISTS taches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titre TEXT,
    fait INT DEFAULT 0,
    date TEXT,
    source TEXT,
    ordre INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cartes (
    id TEXT PRIMARY KEY,
    titre TEXT,
    contenu_md TEXT,
    type TEXT,
    epinglee INT DEFAULT 0,
    cree_le TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    titre TEXT,
    branche TEXT,
    tags TEXT,
    statut TEXT,
    date TEXT,
    mtime REAL,
    taille INT,
    liens TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    id UNINDEXED,
    titre,
    contenu,
    tags,
    tokenize="unicode61 remove_diacritics 2"
);
"""


def maintenant_iso() -> str:
    """Retourne l'horodatage local courant au format ISO 8601 (précision seconde).

    Returns:
        Chaîne ISO 8601 locale sans microsecondes.
    """
    return datetime.now().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    """Ouvre une connexion SQLite configurée (WAL, foreign_keys, Row factory).

    L'appelant est responsable de fermer la connexion retournée.

    Returns:
        Connexion ``sqlite3.Connection`` prête à l'emploi.

    Effets de bord:
        Crée le dossier parent de ``DB_PATH`` et ouvre le fichier SQLite.
    """
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialise toutes les tables et index SQLite s'ils n'existent pas encore.

    Effets de bord:
        Exécute le script DDL sous verrou d'écriture et crée ``jarvis.db``.
    """
    with _write_lock:
        conn = get_connection()
        try:
            with conn:
                conn.executescript(_SCHEMA_SQL)
        finally:
            conn.close()


def execute(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> int:
    """Exécute une requête SQL d'écriture sous verrou thread.

    Args:
        sql: Requête SQL paramétrée (INSERT, UPDATE, DELETE, etc.).
        params: Paramètres positionnels ou nommés.

    Returns:
        ``lastrowid`` pour une insertion (``INSERT``), sinon ``rowcount``.

    Effets de bord:
        Modifie la base SQLite et valide la transaction.
    """
    with _write_lock:
        conn = get_connection()
        try:
            with conn:
                cur = conn.execute(sql, params)
                if sql.lstrip().upper().startswith("INSERT"):
                    return int(cur.lastrowid or 0)
                return int(cur.rowcount)
        finally:
            conn.close()


def query(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[dict[str, Any]]:
    """Exécute une requête SQL de lecture et retourne toutes les lignes en dictionnaires.

    Args:
        sql: Requête SQL ``SELECT``.
        params: Paramètres positionnels ou nommés.

    Returns:
        Liste de dictionnaires (un par ligne retournée).
    """
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> dict[str, Any] | None:
    """Exécute une requête SQL de lecture et retourne la première ligne ou ``None``.

    Args:
        sql: Requête SQL ``SELECT``.
        params: Paramètres positionnels ou nommés.

    Returns:
        Dictionnaire de la première ligne, ou ``None`` si aucun résultat.
    """
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


async def async_execute(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> int:
    """Version asynchrone de ``execute`` exécutée dans un thread via ``asyncio.to_thread``.

    Args:
        sql: Requête SQL d'écriture.
        params: Paramètres SQL.

    Returns:
        ``lastrowid`` ou ``rowcount`` retourné par ``execute``.
    """
    return await asyncio.to_thread(execute, sql, params)


async def async_query(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[dict[str, Any]]:
    """Version asynchrone de ``query`` exécutée dans un thread via ``asyncio.to_thread``.

    Args:
        sql: Requête SQL ``SELECT``.
        params: Paramètres SQL.

    Returns:
        Liste de dictionnaires.
    """
    return await asyncio.to_thread(query, sql, params)


async def async_query_one(sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> dict[str, Any] | None:
    """Version asynchrone de ``query_one`` exécutée dans un thread via ``asyncio.to_thread``.

    Args:
        sql: Requête SQL ``SELECT``.
        params: Paramètres SQL.

    Returns:
        Dictionnaire ou ``None``.
    """
    return await asyncio.to_thread(query_one, sql, params)
