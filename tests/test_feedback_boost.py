"""Feedback changes real retrieval, with bounded influence and attributable labels."""
import time

from ariadne_mcp.query.query import query


def test_feedback_cannot_accumulate_unbounded_or_use_exploration(business):
    base = query(business.db, "createOrder")
    ids = [n["id"] for n in base[-1]["nodes"]]
    for _ in range(100):
        business.feedback.log("createOrder", 1, ids, True, source="implicit_expand")
    assert query(business.db, "createOrder", fdb=business.feedback) == base
    for _ in range(100):
        business.feedback.log("createOrder", 1, ids, True)
    boosted = query(business.db, "createOrder", fdb=business.feedback)
    assert all(0 <= c["confidence"] <= 1 for c in boosted)
    by_ids = {frozenset(n["id"] for n in c["nodes"]): c["confidence"] for c in base}
    for c in boosted:
        key = frozenset(n["id"] for n in c["nodes"])
        if key in by_ids:
            assert abs(c["confidence"] - by_ids[key]) <= 0.100001
    business.feedback.log("createOrder", 1, ids, True)
    assert query(business.db, "createOrder", fdb=business.feedback) == boosted


def test_rejection_demotes_and_disable_restores_base(business, monkeypatch):
    base = query(business.db, "createOrder")
    business.feedback.log("createOrder", 1, [n["id"] for n in base[0]["nodes"]], False)
    rated = query(business.db, "createOrder", fdb=business.feedback)
    assert rated != base
    monkeypatch.setenv("ARIADNE_FEEDBACK_BOOST", "0")
    assert query(business.db, "createOrder", fdb=business.feedback) == base


def test_old_empty_unknown_feedback_and_other_hints_do_not_change_results(business):
    base = query(business.db, "createOrder")
    business.feedback.log("createOrder", 1, [], True)
    business.feedback.log("unrelated", 1, [n["id"] for n in base[0]["nodes"]], False)
    business.feedback.log("createOrder", 1, [n["id"] for n in base[0]["nodes"]], False)
    business.feedback.conn.execute("UPDATE feedback SET ts=? WHERE id=3", (int(time.time()) - 100*86400,))
    business.feedback.conn.commit()
    assert query(business.db, "createOrder", fdb=business.feedback) == base


def test_counts_preserve_sources_for_audit_but_can_select_judgments(business):
    fdb = business.feedback
    fdb.log("x", 1, ["a"], True)
    fdb.log("x", 1, ["a"], True, source="implicit_expand")
    fdb.log("x", 1, ["a"], False)
    assert fdb.get_node_feedback_counts("x") == {"a": {"accepted": 2, "rejected": 1}}
    assert fdb.get_node_feedback_counts("x", explicit_only=True) == {"a": {"accepted": 1, "rejected": 1}}


def test_empty_feedback_preserves_serialized_payload(business):
    import json
    assert json.dumps(query(business.db, "createOrder")) == json.dumps(query(business.db, "createOrder", fdb=business.feedback))
