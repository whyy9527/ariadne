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
    accepted      INTEGER NOT NULL -- 1=useful, 0=not useful
);

CREATE INDEX IF NOT EXISTS idx_feedback_hint ON feedback(hint);
CREATE INDEX IF NOT EXISTS idx_feedback_ts   ON feedback(ts);
"""


class FeedbackDB:
    def __init__(self, db_path: str = "feedback.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def log(self, hint: str, cluster_rank: int, node_ids: list[str], accepted: bool):
        self.conn.execute(
            "INSERT INTO feedback (ts, hint, cluster_rank, node_ids, accepted) VALUES (?,?,?,?,?)",
            (int(time.time()), hint, cluster_rank, json.dumps(node_ids), 1 if accepted else 0)
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

    def close(self):
        self.conn.close()
