"""Persistent enable modes and generation counts, independent of AstrBot."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import time


class MemeState:
    """A SQLite connection used on the plugin's owning event loop thread.

    Each meme retains separate blacklist and whitelist memberships. Switching
    its global mode therefore restores that mode's previous scoped settings,
    matching NoneBot's meme manager behavior.
    """

    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS meme_modes (
                meme_key TEXT PRIMARY KEY,
                default_enabled INTEGER NOT NULL CHECK (default_enabled IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS meme_memberships (
                meme_key TEXT NOT NULL,
                mode INTEGER NOT NULL CHECK (mode IN (0, 1)),
                scope TEXT NOT NULL,
                PRIMARY KEY (meme_key, mode, scope)
            );
            CREATE TABLE IF NOT EXISTS generation_records (
                id INTEGER PRIMARY KEY,
                scope TEXT NOT NULL,
                user_id TEXT NOT NULL,
                meme_key TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS generation_scope_time
                ON generation_records (scope, created_at);
            CREATE INDEX IF NOT EXISTS generation_user_time
                ON generation_records (user_id, created_at);
        """)
        self._db.commit()

    def _mode(self, key: str) -> bool:
        row = self._db.execute("SELECT default_enabled FROM meme_modes WHERE meme_key = ?", (key,)).fetchone()
        return bool(row[0]) if row is not None else True

    def enabled(self, scope: str, key: str) -> bool:
        default = self._mode(key)
        member = self._db.execute(
            "SELECT 1 FROM meme_memberships WHERE meme_key = ? AND mode = ? AND scope = ?",
            (key, int(default), scope),
        ).fetchone() is not None
        return not member if default else member

    def set_enabled(self, scope: str, key: str, enabled: bool) -> None:
        mode = self._mode(key)
        with self._db:
            if bool(enabled) != mode:
                self._db.execute(
                    "INSERT OR IGNORE INTO meme_memberships (meme_key, mode, scope) VALUES (?, ?, ?)",
                    (key, int(mode), scope),
                )
            else:
                self._db.execute(
                    "DELETE FROM meme_memberships WHERE meme_key = ? AND mode = ? AND scope = ?",
                    (key, int(mode), scope),
                )

    def set_global(self, key: str, enabled: bool) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO meme_modes (meme_key, default_enabled) VALUES (?, ?) "
                "ON CONFLICT(meme_key) DO UPDATE SET default_enabled = excluded.default_enabled",
                (key, int(bool(enabled))),
            )

    def record(self, scope: str, user: str, key: str) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO generation_records (scope, user_id, meme_key, created_at) VALUES (?, ?, ?, ?)",
                (scope, user, key, time.time()),
            )

    def statistics(self, scope: str | None = None, user: str | None = None, since: float = 0, key: str | None = None, limit: int = 20) -> list[tuple[str, int]]:
        if limit <= 0:
            return []
        clauses = ["created_at >= ?"]
        parameters: list[str | float | int] = [since]
        for column, value in (("scope", scope), ("user_id", user), ("meme_key", key)):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        parameters.append(int(limit))
        rows = self._db.execute(
            "SELECT meme_key, COUNT(*) AS uses FROM generation_records WHERE "
            + " AND ".join(clauses)
            + " GROUP BY meme_key ORDER BY uses DESC, meme_key ASC LIMIT ?",
            parameters,
        ).fetchall()
        return [(str(meme_key), int(count)) for meme_key, count in rows]

    def close(self) -> None:
        self._db.close()
