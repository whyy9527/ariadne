"""Scanner -> graph -> query/expand -> MCP -> refresh business acceptance."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ariadne_mcp.cli import run_scan
from ariadne_mcp.query.query import expand, query
from test_implicit_feedback import call


def test_create_order_cross_service_contract_evidence(business):
    result = query(business.db, "createOrder", top_n=3)
    expected = {
        "web::frontend::mutation::CreateOrder",
        "gateway::gql::Mutation::createOrder",
        "orders::http::POST::/orders::createOrder",
    }
    assert any(expected <= {n["id"] for n in c["nodes"]} for c in result)
    for c in result:
        for node in c["nodes"]:
            assert Path(node["source_file"]).is_file()
            assert not {"dist", "node_modules"} & set(Path(node["source_file"]).parts)
    outbound = expand(business.db, "gateway::ts_outbound::OrderDs.createOrder::orders")
    assert outbound[0]["source"]["source_file"].endswith("OrderDs.ts")
    assert any(n["to_service"] == "orders" for n in outbound[0]["neighbors"])


def test_order_created_producer_consumer_and_direction(business):
    results = query(business.db, "order-created")
    expected = {"orders::kafka::produce::order-created", "fulfillment::kafka::consume::order-created"}
    assert any(expected <= {n["id"] for n in c["nodes"]} for c in results)
    expanded = expand(business.db, "orders::kafka::produce::order-created")
    assert any(n["from_service"] == "orders" and n["to_service"] == "fulfillment" for n in expanded[0]["neighbors"])


def test_structural_subscription_hint_cannot_select_commercial_subscription(business):
    results = query(business.db, "GraphQL subscription")
    assert results
    names = {n["name"] for c in results for n in c["nodes"]}
    assert "orderChanged" in names or "OrderChanged" in names
    assert "createSubscription" not in names


def test_isolated_anchor_and_unsupported_empty_explanation(server_session, business):
    result = query(business.db, "ping")
    assert len(result) == 1
    assert [n["id"] for n in result[0]["nodes"]] == ["health::http::GET::/ping::ping"]
    assert query(business.db, "ping", singleton_fallback=False) == []
    for hint in ("unindexedWidget", "nginx order cache purge", "STOMP authentication interceptor"):
        payload = call(server_session, "query_chains", {"hint": hint})
        assert payload["chains"] == []
        assert payload["reason"] and payload["next_step"]


def test_generated_schema_does_not_overwrite_source(business):
    node = business.db.get_node("gateway::gql::type::StudentProfileSummary")
    assert node["source_file"].endswith("gateway/src/schema.gql")
    assert "display" in node["field_tokens"]
    assert "removed" not in node["field_tokens"]
    assert query(business.db, "staleGeneratedOperation") == []
    assert query(business.db, "dependencyOnlyOperation") == []


def test_changed_head_rescan_deletion_and_feedback_survival(server_session, business):
    server = server_session
    call(server, "query_chains", {"hint": "createOrder"})
    call(server, "rate_result", {"hint": "createOrder", "accepted": True})
    count = business.feedback.count()
    path = business.root / "health/routes.py"
    path.write_text(path.read_text().replace('/ping', '/heartbeat').replace('def ping', 'def heartbeat'))
    subprocess.run(["git", "-C", str(business.root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(business.root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "new route"], check=True)
    payload = call(server, "query_chains", {"hint": "ping"})
    assert "HEAD changed" in payload["stale_warning"]
    refreshed = call(server, "rescan", {})
    assert refreshed["nodes"] > 0
    assert business.feedback.count() == count
    assert not server._PendingQueries
    payload = call(server, "query_chains", {"hint": "heartbeat"})
    assert payload["stale_warning"] is None
    assert payload["chains"][0]["nodes"][0]["name"] == "heartbeat"
    assert call(server, "query_chains", {"hint": "ping"})["chains"] == []
    assert "ping" not in business.db.get_token_idf()


def test_missing_repository_warns_then_forced_refresh_prunes(server_session, business):
    shutil.rmtree(business.root / "health")
    assert "missing repository" in call(server_session, "query_chains", {"hint": "ping"})["stale_warning"]
    call(server_session, "rescan", {})
    assert call(server_session, "query_chains", {"hint": "ping"})["chains"] == []


def test_real_stdio_mcp_protocol_complete_workflow(business):
    async def scenario():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "ariadne_mcp.server", "--db", str(business.db_path), "--fb", str(business.fb_path),
        ], env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        with anyio.fail_after(20):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    assert {t.name for t in (await session.list_tools()).tools} == {
                        "query_chains", "expand_node", "rate_result", "rescan", "show_help",
                    }
                    async def invoke(name, args):
                        response = await session.call_tool(name, args)
                        assert not response.isError
                        return json.loads(response.content[0].text)
                    result = await invoke("query_chains", {"hint": "createOrder"})
                    node = result["chains"][0]["nodes"][0]
                    assert Path(node["source_file"]).is_file()
                    await invoke("expand_node", {"name": node["id"]})
                    rated = await invoke("rate_result", {"hint": "createOrder", "accepted": True})
                    assert rated["node_ids_count"] > 0
                    invalid = await session.call_tool("query_chains", {"hint": "ping", "top_n": 0})
                    assert invalid.isError
                    await invoke("rescan", {})
                    assert (await invoke("query_chains", {"hint": "ping"}))["chains"]
    asyncio.run(scenario())


def test_multiline_media_annotations_and_nested_security_expression(business):
    expected = {
        "orders::http::PUT::/practices/{practiceId}/whiteboard::saveWhiteboard",
        "orders::http::GET::/practices/{practiceId}/whiteboard::getWhiteboard",
    }
    assert expected <= {n["id"] for n in business.db.get_all_nodes()}
    assert any(expected & {n["id"] for n in c["nodes"]} for c in query(business.db, "/practices/{practiceId}/whiteboard"))


def test_changed_scanner_target_map_invalidates_unchanged_head(business):
    config = json.loads(business.config.read_text())
    config["repos"][1]["scanners"][1]["settings_key_map"] = {"orders": "warehouse"}
    business.config.write_text(json.dumps(config))
    run_scan(str(business.config), str(business.db_path))
    assert business.db.get_node("gateway::ts_outbound::OrderDs.createOrder::warehouse")["target_service"] == "warehouse"
    assert business.db.get_node("gateway::ts_outbound::OrderDs.createOrder::orders") is None


def test_rest_client_and_analytics_contracts_are_discoverable(business):
    nodes = business.db.get_all_nodes()
    for kind, suffix in (("frontend_rest", "OrderApi.ts"), ("backend_client_call", "OrderClient.java"), ("cube_query", "Orders.js")):
        matches = [n for n in nodes if n["source_file"].endswith(suffix)]
        assert matches, (kind, suffix)
        for n in matches:
            assert n["type"] == kind
            results = query(business.db, n["raw_name"])
            assert any(n["id"] == found["id"] for c in results for found in c["nodes"])


def test_help_and_setup_failures_are_actionable(server_session, business):
    help_text = asyncio.run(server_session.call_tool("show_help", {}))[0].text
    assert "query_chains" in help_text and "rate_result" in help_text
    business.config.unlink()
    assert "error" in call(server_session, "rescan", {})
    assert business.db.node_count() > 0


def test_concurrent_clients_upgrade_legacy_index_without_duplicate_alter(tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from ariadne_mcp.store.db import DB
    path = tmp_path / "legacy-index.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE repo_state(name TEXT PRIMARY KEY, git_hash TEXT, scanned_at TEXT)")
    conn.execute("INSERT INTO repo_state VALUES('orders','old','2026-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    def connect(_):
        db = DB(str(path))
        state = db.get_repo_state("orders")
        db.close()
        return state["git_hash"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(connect, range(8))) == ["old"] * 8


def test_duplicate_contract_definition_cannot_change_graph_ranking(business):
    before = query(business.db, "order-created")
    schema = business.root / "gateway/src/schema.gql"
    (schema.parent / "duplicates.gql").write_text(schema.read_text())
    run_scan(str(business.config), str(business.db_path), force=True)
    assert query(business.db, "order-created") == before
