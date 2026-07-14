from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class HistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    profile_label TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    screenshot TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_delivery_campaign_recipient
                ON delivery_log(campaign_id, recipient, status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_delivery_profile_date
                ON delivery_log(profile_id, created_at, status)
                """
            )

    def already_completed(self, campaign_id: str, recipient: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM delivery_log
                WHERE campaign_id = ? AND recipient = ?
                  AND status IN ('sent', 'draft_created')
                LIMIT 1
                """,
                (campaign_id, recipient.lower()),
            ).fetchone()
        return row is not None

    def operations_last_24h(self, profile_id: str) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS total FROM delivery_log
                WHERE profile_id = ?
                  AND status IN ('sent', 'draft_created')
                  AND created_at >= ?
                """,
                (profile_id, cutoff),
            ).fetchone()
        return int(row["total"] if row else 0)

    def record(
        self,
        *,
        campaign_id: str,
        recipient: str,
        profile_id: str,
        profile_label: str,
        subject: str,
        mode: str,
        status: str,
        error: str = "",
        screenshot: str = "",
    ) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO delivery_log (
                    created_at, campaign_id, recipient, profile_id, profile_label,
                    subject, mode, status, error, screenshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    campaign_id,
                    recipient.lower(),
                    profile_id,
                    profile_label,
                    subject,
                    mode,
                    status,
                    error,
                    screenshot,
                ),
            )

    def recent(self, limit: int = 300) -> list[sqlite3.Row]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, campaign_id, recipient, profile_label, subject,
                       mode, status, error, screenshot
                FROM delivery_log
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return list(rows)
