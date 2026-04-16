"""
SQLite store for nodes, edges, clusters.
"""
import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

# Number of days before a scan is considered stale.
STALE_SCAN_DAYS = 7


SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    raw_name    TEXT NOT NULL,
    service     TEXT NOT NULL,
    source_file TEXT,
    method      TEXT,
    path        TEXT,
    tokens      TEXT,   -- JSON array
    field_tokens TEXT   -- JSON array
);

CREATE TABLE IF NOT EXISTS edges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    total_score     REAL NOT NULL,
    name_score      REAL DEFAULT 0,
    field_score     REAL DEFAULT 0,
    role_score      REAL DEFAULT 0,
    service_score   REAL DEFAULT 0,
    UNIQUE(source_id, target_id)
);

CREATE TABLE IF NOT EXISTS clusters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hint  TEXT,
    confidence  REAL,
    node_ids    TEXT   -- JSON array of node ids
);

CREATE TABLE IF NOT EXISTS token_idf (
    token   TEXT PRIMARY KEY,
    idf     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS repo_state (
    name        TEXT PRIMARY KEY,
    git_hash    TEXT,
    scanned_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type   ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_service ON nodes(service);
"""


class DB:
    def __init__(self, db_path: str = "semantic_hint.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def upsert_node(self, node: dict, tokens: list[str], field_tokens: list[str]):
        self.conn.execute("""
            INSERT OR REPLACE INTO nodes
              (id, type, raw_name, service, source_file, method, path, tokens, field_tokens)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            node["id"], node["type"], node["raw_name"], node["service"],
            node.get("source_file"), node.get("method"), node.get("path"),
            json.dumps(tokens), json.dumps(field_tokens),
        ))

    def upsert_edge(self, source_id: str, target_id: str, scores: dict, total: float = None):
        if total is None:
            total = sum(scores.values())
        self.conn.execute("""
            INSERT OR REPLACE INTO edges
              (source_id, target_id, total_score, name_score, field_score, role_score, service_score)
            VALUES (?,?,?,?,?,?,?)
        """, (
            source_id, target_id, total,
            scores.get("name_score", 0),
            scores.get("field_score", 0),
            scores.get("role_score", 0),
            scores.get("service_score", 0),
        ))

    def insert_cluster(self, query_hint: str, confidence: float, node_ids: list[str]):
        self.conn.execute("""
            INSERT INTO clusters (query_hint, confidence, node_ids)
            VALUES (?,?,?)
        """, (query_hint, confidence, json.dumps(node_ids)))

    def clear_clusters(self):
        self.conn.execute("DELETE FROM clusters")

    def upsert_token_idf(self, idf_map: dict[str, float]):
        self.conn.executemany(
            "INSERT OR REPLACE INTO token_idf (token, idf) VALUES (?,?)",
            idf_map.items()
        )

    def get_token_idf(self) -> dict[str, float]:
        rows = self.conn.execute("SELECT token, idf FROM token_idf").fetchall()
        return {r["token"]: r["idf"] for r in rows}

    def get_all_nodes(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_nodes_by_service(self, service: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE service=?", (service,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def delete_nodes_by_service(self, service: str) -> int:
        cur = self.conn.execute("DELETE FROM nodes WHERE service=?", (service,))
        return cur.rowcount

    def delete_all_edges(self):
        self.conn.execute("DELETE FROM edges")

    def get_repo_state(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT name, git_hash, scanned_at FROM repo_state WHERE name=?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_repo_state(self, name: str, git_hash: str | None, scanned_at: str):
        self.conn.execute(
            """INSERT OR REPLACE INTO repo_state (name, git_hash, scanned_at)
               VALUES (?,?,?)""",
            (name, git_hash, scanned_at),
        )

    def get_oldest_scanned_at(self) -> "datetime | None":
        """Return the oldest scanned_at timestamp across all repos, or None if empty.

        Unparseable timestamps are treated as stale (epoch) and a debug note is
        printed to stderr.
        """
        rows = self.conn.execute(
            "SELECT scanned_at FROM repo_state WHERE scanned_at IS NOT NULL"
        ).fetchall()
        if not rows:
            return None

        import sys
        oldest: datetime | None = None
        for row in rows:
            raw = row[0]
            try:
                dt = datetime.fromisoformat(raw)
                # Normalise to aware UTC so comparisons are apples-to-apples.
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError) as exc:
                print(
                    f"[ariadne] WARNING: unparseable scanned_at {raw!r} ({exc}); "
                    "treating as stale (epoch).",
                    file=sys.stderr,
                )
                dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if oldest is None or dt < oldest:
                oldest = dt
        return oldest

    def get_node(self, node_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_edges_for_nodes(self, node_ids: list[str], min_score: float = 0.25) -> list[dict]:
        """Fetch all edges where at least one endpoint is in node_ids. Scales with anchor count."""
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        sql = f"""
            SELECT * FROM edges
            WHERE (source_id IN ({placeholders}) OR target_id IN ({placeholders}))
              AND total_score >= ?
            ORDER BY total_score DESC
        """
        rows = self.conn.execute(sql, node_ids + node_ids + [min_score]).fetchall()
        return [dict(r) for r in rows]

    def get_edges_for_node(self, node_id: str, min_score: float = 0.05) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM edges
            WHERE (source_id=? OR target_id=?) AND total_score >= ?
            ORDER BY total_score DESC
        """, (node_id, node_id, min_score)).fetchall()
        return [dict(r) for r in rows]

    def get_clusters(self, query_hint: str = None) -> list[dict]:
        if query_hint:
            rows = self.conn.execute(
                "SELECT * FROM clusters WHERE query_hint=? ORDER BY confidence DESC",
                (query_hint,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM clusters ORDER BY confidence DESC"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["node_ids"] = json.loads(d["node_ids"])
            result.append(d)
        return result

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    def node_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def edge_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]


def _row_to_dict(row) -> dict:
    d = dict(row)
    if "tokens" in d and d["tokens"]:
        d["tokens"] = json.loads(d["tokens"])
    if "field_tokens" in d and d["field_tokens"]:
        d["field_tokens"] = json.loads(d["field_tokens"])
    return d
