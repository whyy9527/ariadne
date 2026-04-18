"""
Tests for Level-1 feedback-boost reranking.

Run:
    python3 -m pytest test_feedback_boost.py -v
or:
    python3 test_feedback_boost.py
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


def _make_clusters(*node_id_lists):
    """Build minimal cluster dicts with confidence=0.5 each."""
    clusters = []
    for i, nids in enumerate(node_id_lists):
        clusters.append({
            "node_ids": list(nids),
            "confidence": 0.5,
        })
    return clusters


# ──────────────────────────────────────────────
# 1. FeedbackDB.get_accepted_node_ids
# ──────────────────────────────────────────────

def test_get_accepted_node_ids_basic():
    """Accepted rows contribute counts; rejected rows do not."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("createOrder", 1, ["node:A", "node:B"], True)
        fdb.log("createOrder", 1, ["node:A"], True)
        fdb.log("createOrder", 1, ["node:B"], False)  # rejected — should NOT count

        counts = fdb.get_accepted_node_ids("createOrder")
        assert counts.get("node:A") == 2, f"Expected 2, got {counts.get('node:A')}"
        assert counts.get("node:B") == 1, f"Expected 1, got {counts.get('node:B')}"
        fdb.close()
    finally:
        os.unlink(path)


def test_get_accepted_node_ids_max_age_days():
    """Records older than max_age_days should be excluded."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        # Insert a row with ts far in the past (200 days ago)
        old_ts = int(time.time()) - 200 * 86400
        fdb.conn.execute(
            "INSERT INTO feedback (ts, hint, cluster_rank, node_ids, accepted, source) "
            "VALUES (?,?,?,?,?,?)",
            (old_ts, "createOrder", 1, '["node:OLD"]', 1, "manual"),
        )
        fdb.conn.commit()
        # Recent row
        fdb.log("createOrder", 1, ["node:NEW"], True)

        counts_90 = fdb.get_accepted_node_ids("createOrder", max_age_days=90)
        assert "node:OLD" not in counts_90, "Old record should be excluded with max_age_days=90"
        assert counts_90.get("node:NEW") == 1

        counts_300 = fdb.get_accepted_node_ids("createOrder", max_age_days=300)
        assert counts_300.get("node:OLD") == 1, "Old record should appear with max_age_days=300"
        fdb.close()
    finally:
        os.unlink(path)


def test_get_accepted_node_ids_no_match():
    """Unknown hint returns empty dict — no error."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("someOtherHint", 1, ["node:X"], True)
        counts = fdb.get_accepted_node_ids("unknownHint")
        assert counts == {}, f"Expected empty dict, got {counts}"
        fdb.close()
    finally:
        os.unlink(path)


def test_get_accepted_node_ids_empty_node_ids():
    """Rows with empty node_ids array (implicit feedback) contribute nothing but don't crash."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("createOrder", 1, [], True)  # implicit feedback — empty node_ids
        counts = fdb.get_accepted_node_ids("createOrder")
        assert counts == {}, f"Empty node_ids should yield empty map, got {counts}"
        fdb.close()
    finally:
        os.unlink(path)


def test_get_accepted_node_ids_both_sources():
    """manual and implicit_expand sources both count."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        fdb.log("createOrder", 1, ["node:A"], True, source="manual")
        fdb.log("createOrder", 1, ["node:A"], True, source="implicit_expand")
        counts = fdb.get_accepted_node_ids("createOrder")
        assert counts.get("node:A") == 2, f"Both sources should count, got {counts}"
        fdb.close()
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 2. Boost rerank logic (unit test the math)
# ──────────────────────────────────────────────

def test_boost_rerank_lifts_cluster():
    """
    Cluster with historically accepted nodes should score higher after boost.
    """
    from ariadne_mcp.query.query import _BOOST_ALPHA

    clusters = _make_clusters(
        ["svc::gql::m::createOrder", "kafka::topic::order-created"],  # cluster 0: will be boosted
        ["svc::gql::q::getUser"],                                      # cluster 1: not boosted
    )
    original_scores = [c["confidence"] for c in clusters]

    accepted_map = {"svc::gql::m::createOrder": 3, "kafka::topic::order-created": 1}

    for c in clusters:
        boost = sum(accepted_map.get(nid, 0) for nid in c["node_ids"])
        if boost:
            c["confidence"] = round(c["confidence"] + _BOOST_ALPHA * boost, 6)

    clusters.sort(key=lambda c: c["confidence"], reverse=True)

    # cluster 0 had boost=4 → confidence = 0.5 + 0.15*4 = 1.1
    # cluster 1 had boost=0 → confidence stays 0.5
    assert clusters[0]["node_ids"] == ["svc::gql::m::createOrder", "kafka::topic::order-created"], \
        "Boosted cluster should rank first"
    expected_boosted_score = round(0.5 + _BOOST_ALPHA * 4, 6)
    assert abs(clusters[0]["confidence"] - expected_boosted_score) < 1e-9, \
        f"Expected {expected_boosted_score}, got {clusters[0]['confidence']}"


def test_boost_rerank_no_overlap():
    """Clusters with no accepted nodes keep original order."""
    from ariadne_mcp.query.query import _BOOST_ALPHA

    clusters = _make_clusters(["node:A"], ["node:B"])
    clusters[0]["confidence"] = 0.8
    clusters[1]["confidence"] = 0.5

    accepted_map = {"node:C": 5}  # no overlap with either cluster

    original = [c["confidence"] for c in clusters]
    for c in clusters:
        boost = sum(accepted_map.get(nid, 0) for nid in c["node_ids"])
        if boost:
            c["confidence"] = round(c["confidence"] + _BOOST_ALPHA * boost, 6)

    clusters.sort(key=lambda c: c["confidence"], reverse=True)

    # Order unchanged: cluster 0 still first
    assert clusters[0]["node_ids"] == ["node:A"]
    assert clusters[0]["confidence"] == original[0]


# ──────────────────────────────────────────────
# 3. Feature flag ARIADNE_FEEDBACK_BOOST=0
# ──────────────────────────────────────────────

def _run_query_with_boost_flag(fdb, flag_value: str) -> list[dict]:
    """
    Run query() using a minimal fake DB so we can test flag behaviour without
    a full ariadne.db. We monkey-patch query internals to return fixed clusters.
    """
    import unittest.mock as mock
    from ariadne_mcp.query.query import query as q

    # Minimal fake DB for query() internals
    fake_db = mock.MagicMock()
    fake_db.get_token_idf.return_value = {}
    fake_db.get_all_nodes.return_value = []
    fake_db.get_edges_for_nodes.return_value = []

    # Stub out build_clusters and find_anchors to return one cluster
    stub_cluster = {
        "node_ids": ["node:BOOSTED"],
        "confidence": 0.5,
    }
    with mock.patch("ariadne_mcp.query.query.find_anchors", return_value=[]), \
         mock.patch("ariadne_mcp.query.query.build_clusters", return_value=[stub_cluster]), \
         mock.patch.dict(os.environ, {"ARIADNE_FEEDBACK_BOOST": flag_value}):
        results = q(fake_db, "createOrder", top_n=3, fdb=fdb)
    return results


def test_feature_flag_disabled():
    """ARIADNE_FEEDBACK_BOOST=0 disables boost even when feedback exists."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        # Log a lot of accepted feedback so boost would have a big effect if enabled
        for _ in range(10):
            fdb.log("createOrder", 1, ["node:BOOSTED"], True)

        results = _run_query_with_boost_flag(fdb, "0")
        # With boost disabled, confidence should remain at original 0.5
        if results:
            assert abs(results[0]["confidence"] - 0.5) < 1e-9, \
                f"Expected confidence=0.5 (no boost), got {results[0]['confidence']}"
        fdb.close()
    finally:
        os.unlink(path)


def test_feature_flag_enabled_by_default():
    """ARIADNE_FEEDBACK_BOOST=1 (default) applies boost when feedback exists."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = _make_fdb(path)
        for _ in range(3):
            fdb.log("createOrder", 1, ["node:BOOSTED"], True)

        results = _run_query_with_boost_flag(fdb, "1")
        from ariadne_mcp.query.query import _BOOST_ALPHA
        if results:
            expected = round(0.5 + _BOOST_ALPHA * 3, 6)
            assert abs(results[0]["confidence"] - expected) < 1e-9, \
                f"Expected confidence={expected}, got {results[0]['confidence']}"
        fdb.close()
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 4. No-feedback no-error path
# ──────────────────────────────────────────────

def test_no_feedback_no_crash():
    """query() with fdb that has zero matching rows must not raise."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        import unittest.mock as mock
        from ariadne_mcp.query.query import query as q

        fdb = _make_fdb(path)
        # No feedback written for "createOrder"

        fake_db = mock.MagicMock()
        fake_db.get_token_idf.return_value = {}
        fake_db.get_all_nodes.return_value = []
        fake_db.get_edges_for_nodes.return_value = []

        stub_cluster = {"node_ids": ["node:X"], "confidence": 0.5}
        with mock.patch("ariadne_mcp.query.query.find_anchors", return_value=[]), \
             mock.patch("ariadne_mcp.query.query.build_clusters", return_value=[stub_cluster]), \
             mock.patch.dict(os.environ, {"ARIADNE_FEEDBACK_BOOST": "1"}):
            results = q(fake_db, "createOrder", top_n=3, fdb=fdb)

        # Should return results without raising
        assert isinstance(results, list)
        fdb.close()
    finally:
        os.unlink(path)


def test_fdb_none_no_crash():
    """query() with fdb=None (default) must not raise."""
    import unittest.mock as mock
    from ariadne_mcp.query.query import query as q

    fake_db = mock.MagicMock()
    fake_db.get_token_idf.return_value = {}
    fake_db.get_all_nodes.return_value = []
    fake_db.get_edges_for_nodes.return_value = []

    stub_cluster = {"node_ids": ["node:X"], "confidence": 0.5}
    with mock.patch("ariadne_mcp.query.query.find_anchors", return_value=[]), \
         mock.patch("ariadne_mcp.query.query.build_clusters", return_value=[stub_cluster]), \
         mock.patch.dict(os.environ, {"ARIADNE_FEEDBACK_BOOST": "1"}):
        results = q(fake_db, "createOrder", top_n=3, fdb=None)

    assert isinstance(results, list)


# ──────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_get_accepted_node_ids_basic,
        test_get_accepted_node_ids_max_age_days,
        test_get_accepted_node_ids_no_match,
        test_get_accepted_node_ids_empty_node_ids,
        test_get_accepted_node_ids_both_sources,
        test_boost_rerank_lifts_cluster,
        test_boost_rerank_no_overlap,
        test_feature_flag_disabled,
        test_feature_flag_enabled_by_default,
        test_no_feedback_no_crash,
        test_fdb_none_no_crash,
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
