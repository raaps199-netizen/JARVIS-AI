"""Disk-backed JARVIS memory.

Inspired by AnythingLLM Open Computer's memory-manager and context-optimizer:
keep durable state on disk, retrieve only relevant snippets, and never keep the
whole conversation in the Python process.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path

DEFAULT_MAX_CHARS = 3500
DEFAULT_RESULTS = 5

def _memory_dir() -> Path:
    root = os.getenv("JARVIS_MEMORY_DIR", "").strip()
    if root:
        return Path(os.path.expandvars(os.path.expanduser(root))).resolve()
    local = os.getenv("LOCALAPPDATA")
    if local:
        return Path(local) / "JARVIS" / "memory"
    return Path.home() / ".jarvis" / "memory"

class JarvisMemory:
    """Small SQLite-backed memory store with bounded retrieval."""

    def __init__(self, root: Path | None = None):
        self.root = (root or _memory_dir()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "memory.db"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=FILE")
        return conn

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL DEFAULT 'turn',
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used REAL NOT NULL DEFAULT 0
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())}

    def add(self, content: str, kind: str = "turn") -> None:
        content = " ".join(str(content).split()).strip()
        if not content:
            return
        now = time.time()
        with self._connect() as db:
            db.execute(
                "INSERT INTO memories(kind, content, created_at) VALUES (?, ?, ?)",
                (str(kind), content[:8000], now),
            )
            db.execute("""
                DELETE FROM memories
                WHERE id NOT IN (
                    SELECT id FROM memories ORDER BY created_at DESC LIMIT 1500
                )
            """)

    def search(self, query: str, limit: int = DEFAULT_RESULTS, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []

        with self._connect() as db:
            rows = db.execute(
                "SELECT id, content FROM memories ORDER BY created_at DESC LIMIT 1500"
            ).fetchall()

        scored = []
        for row_id, content in rows:
            tokens = self._tokens(content)
            overlap = len(query_tokens & tokens)
            if not overlap:
                continue
            score = overlap / max(1, len(query_tokens))
            scored.append((score, row_id, content))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen, used, ids = [], 0, []
        for _score, row_id, content in scored[: max(1, int(limit))]:
            if used + len(content) > max_chars:
                continue
            chosen.append(content)
            used += len(content)
            ids.append(row_id)

        if ids:
            now = time.time()
            with self._connect() as db:
                db.executemany("UPDATE memories SET last_used=? WHERE id=?", [(now, i) for i in ids])
        return chosen

    def context(self, query: str, limit: int = DEFAULT_RESULTS, max_chars: int = DEFAULT_MAX_CHARS) -> str:
        entries = self.search(query, limit=limit, max_chars=max_chars)
        return "\n".join(f"- {entry}" for entry in entries)

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_state(self, key: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def forget_state(self, key: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM state WHERE key=?", (key,))

    def stats(self) -> dict[str, int | str]:
        with self._connect() as db:
            count = int(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        return {"path": str(self.db_path), "entries": count}
