"""Exercise actual MCP handlers rather than reimplementing their matching logic."""
import asyncio
import json
import sqlite3

from ariadne_mcp.store.feedback_db import FeedbackDB


def call(server, name, args):
    return json.loads(asyncio.run(server.call_tool(name, args))[0].text)


def test_query_expand_rate_keeps_association_without_double_count(server_session, business):
    server = server_session
    result = call(server, "query_chains", {"hint": "createOrder"})
    ids = [n["id"] for n in result["chains"][0]["nodes"]]
    call(server, "expand_node", {"name": ids[0]})
    count = business.feedback.count()
    call(server, "expand_node", {"name": ids[0]})
    assert business.feedback.count() == count
    rated = call(server, "rate_result", {"hint": "createOrder", "accepted": False})
    assert rated["node_ids_count"] == len(ids)
    rows = business.feedback.conn.execute("SELECT node_ids,accepted,source FROM feedback").fetchall()
    assert json.loads(rows[-1][0]) == ids
    assert rows[-1][1:] == (0, "manual")
    assert all(set(json.loads(row[0])) <= set(ids) for row in rows[:-1])


def test_expansion_rating_rank_zero_and_expired_queries(server_session, business):
    server = server_session
    call(server, "query_chains", {"hint": "createOrder"})
    call(server, "expand_node", {"name": "orders::http::POST::/orders::createOrder"})
    rated = call(server, "rate_result", {"hint": "orders::http::POST::/orders::createOrder", "cluster_rank": 0, "accepted": False})
    assert rated["node_ids_count"] > 0
    for entry in server._PendingQueries:
        entry["ts"] -= server._PENDING_TTL + 1
    count = business.feedback.count()
    call(server, "expand_node", {"name": "orders::http::POST::/orders::createOrder"})
    assert business.feedback.count() == count
    rated = call(server, "rate_result", {"hint": "createOrder", "accepted": False})
    assert rated["node_ids_count"] == 0


def test_migrates_legacy_feedback_without_losing_rows(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE feedback(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, hint TEXT NOT NULL, cluster_rank INTEGER NOT NULL, node_ids TEXT NOT NULL, accepted INTEGER NOT NULL)")
    conn.execute("INSERT INTO feedback VALUES(1,1,'x',1,'[\"a\"]',1)")
    conn.commit()
    conn.close()
    fdb = FeedbackDB(str(path))
    assert fdb.count() == 1
    assert fdb.conn.execute("SELECT source FROM feedback").fetchone()[0] == "manual"
    fdb.close()
