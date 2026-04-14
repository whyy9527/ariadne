"""
Local embedding store — persists across semantic_hint.db rebuilds.
Stores node vectors keyed by node_id. Auto-detected stale when node count changes.
"""
import sqlite3
import struct
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embeddings (
    node_id TEXT PRIMARY KEY,
    vector  BLOB NOT NULL   -- packed float32 array
);
"""

_FLOAT_FMT = "f"  # 4-byte float32


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}{_FLOAT_FMT}", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // struct.calcsize(_FLOAT_FMT)
    return list(struct.unpack(f"{n}{_FLOAT_FMT}", blob))


class EmbeddingDB:
    def __init__(self, db_path: str = "embeddings.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── meta ──────────────────────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value)
        )
        self.conn.commit()

    # ── vectors ───────────────────────────────────────────────────────────────

    def upsert(self, node_id: str, vector: list[float]):
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings (node_id, vector) VALUES (?,?)",
            (node_id, _pack(vector))
        )

    def commit(self):
        self.conn.commit()

    def get_all(self) -> dict[str, list[float]]:
        rows = self.conn.execute("SELECT node_id, vector FROM embeddings").fetchall()
        return {r["node_id"]: _unpack(r["vector"]) for r in rows}

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    def is_stale(self, expected_node_count: int) -> bool:
        """Return True if embedding count doesn't match current node count."""
        return self.count() != expected_node_count

    def close(self):
        self.conn.close()
