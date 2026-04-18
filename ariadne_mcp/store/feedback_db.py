"""
Local feedback store — survives semantic_hint.db rebuilds.
Records whether Ariadne results were useful, for future reranker training.
"""
import sqlite3
import json
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    hint          TEXT NOT NULL,
    cluster_rank  INTEGER NOT NULL,
    node_ids      TEXT NOT NULL,   -- JSON array
    accepted      INTEGER NOT NULL, -- 1=useful, 0=not useful
    source        TEXT NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_feedback_hint ON feedback(hint);
CREATE INDEX IF NOT EXISTS idx_feedback_ts   ON feedback(ts);
"""

_MIGRATE_ADD_SOURCE = "ALTER TABLE feedback ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'"


class FeedbackDB:
    def __init__(self, db_path: str = "feedback.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self):
        """Add 'source' column to existing DBs that pre-date this schema."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(feedback)")}
        if "source" not in cols:
            self.conn.execute(_MIGRATE_ADD_SOURCE)

    def log(self, hint: str, cluster_rank: int, node_ids: list[str], accepted: bool,
            source: str = "manual"):
        self.conn.execute(
            "INSERT INTO feedback (ts, hint, cluster_rank, node_ids, accepted, source) "
            "VALUES (?,?,?,?,?,?)",
            (int(time.time()), hint, cluster_rank, json.dumps(node_ids),
             1 if accepted else 0, source)
        )
        self.conn.commit()

    def get_accepted_node_ids(self, hint: str, max_age_days: int = 90) -> dict[str, int]:
        """
        Return {node_id: count} for all accepted feedback rows matching this hint.
        Only considers rows newer than max_age_days.
        node_ids column is a JSON array — we expand it in Python.
        """
        cutoff_ts = int(time.time()) - max_age_days * 86400
        rows = self.conn.execute(
            "SELECT node_ids FROM feedback "
            "WHERE hint = ? AND accepted = 1 AND ts >= ?",
            (hint, cutoff_ts),
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            try:
                ids = json.loads(row[0])
            except (ValueError, TypeError):
                continue
            for nid in ids:
                if nid:
                    counts[nid] = counts.get(nid, 0) + 1
        return counts

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    def close(self):
        self.conn.close()
