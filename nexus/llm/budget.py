from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3


class DailyBudgetStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path)
        self._init_db()

    def get_spend(self, role: str, *, now: datetime | None = None) -> float:
        today = _utc_date(now)
        row = self._conn.execute(
            "SELECT spend_usd FROM llm_daily_budget WHERE date_utc = ? AND role = ?",
            (today, role),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def add_spend(self, role: str, cost_usd: float, *, now: datetime | None = None) -> float:
        today = _utc_date(now)
        self._conn.execute(
            """
            INSERT INTO llm_daily_budget(date_utc, role, spend_usd)
            VALUES (?, ?, ?)
            ON CONFLICT(date_utc, role)
            DO UPDATE SET spend_usd = spend_usd + excluded.spend_usd
            """,
            (today, role, cost_usd),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT spend_usd FROM llm_daily_budget WHERE date_utc = ? AND role = ?",
            (today, role),
        ).fetchone()
        return float(row[0]) if row else cost_usd

    def _init_db(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_daily_budget (
                date_utc TEXT NOT NULL,
                role TEXT NOT NULL,
                spend_usd REAL NOT NULL,
                PRIMARY KEY (date_utc, role)
            )
            """
        )
        self._conn.commit()


def _utc_date(now: datetime | None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).date().isoformat()
