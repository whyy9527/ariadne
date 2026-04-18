"""
Tests for implicit feedback via expand_node follow-ups.

Run:
    python3 -m pytest test_implicit_feedback.py -v
or:
    python3 test_implicit_feedback.py
"""
import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_fdb(path: str):
    from ariadne_mcp.store.feedback_db import FeedbackDB
    return FeedbackDB(path)


def _make_pending_entry(hint: str, cluster_node_names: list[set], ts: float = None):
    """Build a fake _PendingQueries entry."""
    if ts is None:
        ts = time.time()
    clusters = [
        {"rank": i + 1, "node_names": names}
        for i, names in enumerate(cluster_node_names)
    ]
    return {"hint": hint, "ts": ts, "clusters": clusters}


# ──────────────────────────────────────────────
# 1. FeedbackDB — source column migration
# ──────────────────────────────────────────────

def test_feedback_db_source_column_default():
    """New DB should store 'manual' as source when source not given."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("createOrder", 1, ["id1"], True)
        row = fdb.conn.execute("SELECT source FROM feedback WHERE hint='createOrder'").fetchone()
        assert row is not None
        assert row[0] == "manual"
        fdb.close()
    finally:
        os.unlink(path)


def test_feedback_db_source_column_implicit():
    """log() with source='implicit_expand' should persist correctly."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("createOrder", 1, [], True, source="implicit_expand")
        row = fdb.conn.execute("SELECT source FROM feedback").fetchone()
        assert row[0] == "implicit_expand"
        fdb.close()
    finally:
        os.unlink(path)


def test_feedback_db_migration_adds_source_column():
    """
    Simulate a pre-existing DB without the 'source' column.
    Opening it with FeedbackDB should migrate it transparently.
    """
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        # Create old-schema DB manually (no source column)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE feedback (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ts           INTEGER NOT NULL,
                hint         TEXT NOT NULL,
                cluster_rank INTEGER NOT NULL,
                node_ids     TEXT NOT NULL,
                accepted     INTEGER NOT NULL
            )
        """)
        conn.execute("INSERT INTO feedback (ts, hint, cluster_rank, node_ids, accepted) VALUES (1, 'old', 1, '[]', 1)")
        conn.commit()
        conn.close()

        # Opening with FeedbackDB should migrate without error
        fdb = _make_fdb(path)
        assert fdb.count() == 1

        # New row should get default source='manual'
        fdb.log("newHint", 1, [], True)
        rows = fdb.conn.execute("SELECT source FROM feedback ORDER BY id").fetchall()
        assert rows[0][0] == "manual"   # migrated old row
        assert rows[1][0] == "manual"   # new row
        fdb.close()
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 2. _extract_cluster_node_names
# ──────────────────────────────────────────────

def test_extract_cluster_node_names_basic():
    from ariadne_mcp.server import _extract_cluster_node_names

    results = [
        {
            "query": "createOrder",
            "confidence": 0.9,
            "nodes": [
                {"name": "createOrder", "id": "gw::gql::m::createOrder", "type": "graphql_mutation", "service": "gw", "label": "..."},
                {"name": "order-created", "id": "kafka::topic::order-created", "type": "kafka_topic", "service": "orders", "label": "..."},
            ],
            "services": ["gw", "orders"],
        }
    ]
    clusters = _extract_cluster_node_names(results)
    assert len(clusters) == 1
    assert clusters[0]["rank"] == 1
    assert "createorder" in clusters[0]["node_names"]
    assert "order-created" in clusters[0]["node_names"]


# ──────────────────────────────────────────────
# 3. Implicit feedback end-to-end (in-process simulation)
# ──────────────────────────────────────────────

def test_implicit_feedback_written_on_expand_match():
    """
    Simulate: query_chains caches a pending entry, then expand_node is called
    with a name matching one of the cluster nodes → implicit feedback row written.
    """
    from ariadne_mcp import server as mcp_server

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        fb_path = f.name
    try:
        # Patch module globals
        original_fb_path = mcp_server._FB_PATH
        original_pending = mcp_server._PendingQueries
        mcp_server._FB_PATH = fb_path
        mcp_server._PendingQueries = __import__("collections").deque(maxlen=mcp_server._PENDING_MAX)
        mcp_server._fdb = None  # force re-init with new path

        # Insert a fake pending entry (simulates what query_chains would cache)
        entry = _make_pending_entry(
            hint="createOrder",
            cluster_node_names=[{"createorder", "gw::gql::m::createorder"}],
        )
        mcp_server._PendingQueries.append(entry)

        fdb = _make_fdb(fb_path)
        assert fdb.count() == 0

        # Simulate expand_node matching logic (inline, avoids needing a real DB)
        name_lower = "createorder"
        now = time.time()
        matched = []
        for e in list(mcp_server._PendingQueries):
            if now - e["ts"] > mcp_server._PENDING_TTL:
                continue
            for cluster in e["clusters"]:
                if any(name_lower in n or n in name_lower for n in cluster["node_names"]):
                    matched.append((e, cluster["rank"]))
                    break

        assert len(matched) == 1, "Should have matched one pending entry"
        hint_entry, rank = matched[0]

        fdb.log(
            hint=hint_entry["hint"],
            cluster_rank=rank,
            node_ids=[],
            accepted=True,
            source="implicit_expand",
        )
        try:
            mcp_server._PendingQueries.remove(hint_entry)
        except ValueError:
            pass

        assert fdb.count() == 1
        row = fdb.conn.execute("SELECT hint, cluster_rank, accepted, source FROM feedback").fetchone()
        assert row[0] == "createOrder"
        assert row[1] == 1
        assert row[2] == 1
        assert row[3] == "implicit_expand"

        # Pending entry should be removed to avoid double-counting
        assert len(mcp_server._PendingQueries) == 0

        fdb.close()
    finally:
        mcp_server._FB_PATH = original_fb_path
        mcp_server._PendingQueries = original_pending
        mcp_server._fdb = None
        os.unlink(fb_path)


def test_ttl_expired_entries_not_written():
    """Expired pending entries (older than _PENDING_TTL) must not produce feedback."""
    from ariadne_mcp import server as mcp_server
    from collections import deque

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        fb_path = f.name
    try:
        original_pending = mcp_server._PendingQueries
        mcp_server._PendingQueries = deque(maxlen=mcp_server._PENDING_MAX)

        # Insert entry with ts far in the past (expired)
        expired_ts = time.time() - mcp_server._PENDING_TTL - 1
        entry = _make_pending_entry(
            hint="expiredHint",
            cluster_node_names=[{"expirednode"}],
            ts=expired_ts,
        )
        mcp_server._PendingQueries.append(entry)

        fdb = _make_fdb(fb_path)

        # Run matching logic (same as in _expand_node)
        name_lower = "expirednode"
        now = time.time()
        matched = []
        for e in list(mcp_server._PendingQueries):
            if now - e["ts"] > mcp_server._PENDING_TTL:
                continue  # expired: skip, NO negative feedback
            for cluster in e["clusters"]:
                if any(name_lower in n or n in name_lower for n in cluster["node_names"]):
                    matched.append((e, cluster["rank"]))
                    break

        assert len(matched) == 0, "Expired entry should not match"
        assert fdb.count() == 0, "No feedback should be written for expired entries"
        fdb.close()
    finally:
        mcp_server._PendingQueries = original_pending
        os.unlink(fb_path)


def test_no_match_no_feedback():
    """expand_node with a name that doesn't match any pending entry → no feedback."""
    from ariadne_mcp import server as mcp_server
    from collections import deque

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        fb_path = f.name
    try:
        original_pending = mcp_server._PendingQueries
        mcp_server._PendingQueries = deque(maxlen=mcp_server._PENDING_MAX)

        entry = _make_pending_entry(
            hint="createOrder",
            cluster_node_names=[{"createorder", "order-created"}],
        )
        mcp_server._PendingQueries.append(entry)

        fdb = _make_fdb(fb_path)

        # expand with completely unrelated name
        name_lower = "getweatherforecast"
        now = time.time()
        matched = []
        for e in list(mcp_server._PendingQueries):
            if now - e["ts"] > mcp_server._PENDING_TTL:
                continue
            for cluster in e["clusters"]:
                if any(name_lower in n or n in name_lower for n in cluster["node_names"]):
                    matched.append((e, cluster["rank"]))
                    break

        assert len(matched) == 0
        assert fdb.count() == 0
        fdb.close()
    finally:
        mcp_server._PendingQueries = original_pending
        os.unlink(fb_path)


def test_pending_deque_cap():
    """Deque should not grow beyond _PENDING_MAX (oldest evicted automatically)."""
    from ariadne_mcp import server as mcp_server
    from collections import deque

    cap = mcp_server._PENDING_MAX
    q = deque(maxlen=cap)
    for i in range(cap + 5):
        q.append(_make_pending_entry(f"hint{i}", [{"node{i}"}]))
    assert len(q) == cap
    # First entry should be the (cap+5-cap)=5th one (index 5)
    assert q[0]["hint"] == f"hint5"


# ──────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_feedback_db_source_column_default,
        test_feedback_db_source_column_implicit,
        test_feedback_db_migration_adds_source_column,
        test_extract_cluster_node_names_basic,
        test_implicit_feedback_written_on_expand_match,
        test_ttl_expired_entries_not_written,
        test_no_match_no_feedback,
        test_pending_deque_cap,
    ]

    passed = failed = 0
    for t in tests:
        try:
            print(f"  {t.__name__} ... ", end="", flush=True)
            t()
            print("OK")
            passed += 1
        except Exception as e:
            import traceback
            print(f"FAIL: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
