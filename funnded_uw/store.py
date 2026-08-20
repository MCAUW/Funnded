"""SQLite record of which emails have been processed, so restarts never
double-analyze or double-reply to a deal."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FINAL_STATUSES = {"done", "failed", "skipped"}


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS processed (
                message_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                subject TEXT,
                error TEXT,
                updated_at TEXT
            )"""
        )
        self.conn.commit()

    def status(self, message_id: str):
        row = self.conn.execute(
            "SELECT status, attempts FROM processed WHERE message_id = ?", (message_id,)
        ).fetchone()
        return row  # (status, attempts) or None

    def is_finished(self, message_id: str) -> bool:
        row = self.status(message_id)
        return bool(row) and row[0] in FINAL_STATUSES

    def record(self, message_id: str, status: str, subject: str = "", error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO processed (message_id, status, attempts, subject, error, updated_at)
               VALUES (?, ?, 0, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                 status = excluded.status, subject = excluded.subject,
                 error = excluded.error, updated_at = excluded.updated_at""",
            (message_id, status, subject, error, now),
        )
        self.conn.commit()

    def bump_attempts(self, message_id: str, subject: str, error: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO processed (message_id, status, attempts, subject, error, updated_at)
               VALUES (?, 'error', 1, ?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET
                 status = 'error', attempts = processed.attempts + 1,
                 error = excluded.error, updated_at = excluded.updated_at""",
            (message_id, subject, error, now),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT attempts FROM processed WHERE message_id = ?", (message_id,)
        ).fetchone()[0]
