"""Coda persistente dei comandi Mobile Control, separata per nodo."""

import sqlite3
import uuid
from datetime import datetime, timezone

from node_paths import data_file
from mobile_commands import validate


def database_path():
    return data_file("mobile-control.sqlite3")


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            result TEXT
        )
    """)
    db.commit()
    return db


def enqueue(command):
    command = validate(command)
    operation_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()

    with connect() as db:
        db.execute(
            """INSERT INTO commands
               (id, command, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (operation_id, command, created_at),
        )

    return operation_id


def get(operation_id):
    with connect() as db:
        row = db.execute(
            "SELECT * FROM commands WHERE id = ?",
            (operation_id,),
        ).fetchone()
    return dict(row) if row else None


def claim_next():
    """Preleva atomicamente un comando, uno alla volta per nodo."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")

        busy = db.execute(
            "SELECT 1 FROM commands WHERE status = 'running' LIMIT 1"
        ).fetchone()

        if busy:
            return None

        row = db.execute(
            """SELECT * FROM commands
               WHERE status = 'pending'
               ORDER BY created_at, rowid
               LIMIT 1"""
        ).fetchone()

        if row is None:
            return None

        db.execute(
            "UPDATE commands SET status = 'running' WHERE id = ?",
            (row["id"],),
        )

        return dict(row)


def complete(operation_id, success, result):
    """Registra il risultato di un comando in esecuzione."""
    status = "completed" if success else "failed"
    completed_at = datetime.now(timezone.utc).isoformat()

    with connect() as db:
        cursor = db.execute(
            """UPDATE commands
               SET status = ?, completed_at = ?, result = ?
               WHERE id = ? AND status = 'running'""",
            (status, completed_at, str(result)[:2000], operation_id),
        )

        return cursor.rowcount == 1


def recover_interrupted():
    """Chiude le operazioni interrotte senza ritrasmetterle."""
    completed_at = datetime.now(timezone.utc).isoformat()

    with connect() as db:
        cursor = db.execute(
            """UPDATE commands
               SET status = 'interrupted',
                   completed_at = ?,
                   result = 'Collector restarted; execution outcome unknown'
               WHERE status = 'running'""",
            (completed_at,),
        )
        return cursor.rowcount


def cancel_pending():
    """Annulla i comandi non ancora eseguiti."""
    completed_at = datetime.now(timezone.utc).isoformat()

    with connect() as db:
        cursor = db.execute(
            """UPDATE commands
               SET status = 'cancelled',
                   completed_at = ?,
                   result = 'Pending command cancelled'
               WHERE status = 'pending'""",
            (completed_at,),
        )
        return cursor.rowcount


def recover_on_startup():
    """Recupero sicuro all'avvio del collector.

    Non ritrasmette comandi precedenti al riavvio.
    """
    interrupted = recover_interrupted()
    cancelled = cancel_pending()

    return {
        "interrupted": interrupted,
        "cancelled": cancelled,
    }
