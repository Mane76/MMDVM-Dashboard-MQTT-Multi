"""Protezione persistente contro comandi duplicati o scaduti."""

import sqlite3
import time
from pathlib import Path

MAX_AGE = 30
MAX_FUTURE = 5


class CommandGuard:
    def __init__(self, database):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS seen_commands (
                    operation_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    received_at REAL NOT NULL
                )
            """)

    def _connect(self):
        return sqlite3.connect(str(self.database), timeout=5)

    def check(self, request):
        now = time.time()
        created = request.get("created_at")

        if (
            type(created) not in (int, float)
            or not now - MAX_AGE <= created <= now + MAX_FUTURE
        ):
            raise ValueError("Command expired or invalid timestamp")

        operation_id = request["operation_id"]

        with self._connect() as db:
            # Manteniamo i record abbastanza a lungo da impedire
            # la riesecuzione di messaggi ancora validi.
            db.execute(
                "DELETE FROM seen_commands WHERE received_at < ?",
                (now - 3600,)
            )

            try:
                db.execute(
                    """INSERT INTO seen_commands
                       (operation_id, created_at, received_at)
                       VALUES (?, ?, ?)""",
                    (operation_id, created, now)
                )
            except sqlite3.IntegrityError:
                raise ValueError("Duplicate command") from None

        return True
