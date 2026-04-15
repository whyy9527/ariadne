"""
Tests for ariadne.
Run: python3 -m pytest test_semantic_hint.py -v
or:  python3 test_semantic_hint.py
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalizer.normalizer import split_tokens, normalize
from scoring.engine import jaccard, compute_scores
from store.db import DB


# ──────────────────────────────────────────────
# 1. Normalizer
# ──────────────────────────────────────────────

def test_split_camel():
    assert split_tokens("createOrderItem") == ["create", "order", "item"]

def test_split_kebab():
    tokens = split_tokens("order-item-created")
    assert "order" in tokens and "item" in tokens and "created" in tokens

def test_split_pascal():
    tokens = split_tokens("GetUserProfile")
    assert "get" in tokens and "user" in tokens and "profile" in tokens

def test_normalize_fields():
    result = normalize("createOrder", ["orderId", "customerId", "orderType"])
    assert "create" in result["tokens"]
    assert "order" in result["tokens"]
    assert "order" in result["field_tokens"]
    assert "customer" in result["field_tokens"]


# ──────────────────────────────────────────────
# 2. Scoring
# ──────────────────────────────────────────────

def test_jaccard_exact():
    assert jaccard(["a", "b"], ["a", "b"]) == 1.0

def test_jaccard_empty():
    assert jaccard([], ["a"]) == 0.0

def test_jaccard_partial():
    j = jaccard(["create", "order"], ["create", "invoice"])
    assert 0 < j < 1

def test_compute_scores_complementary_cross_service():
    a = {
        "id": "gateway::gql::Mutation::createOrder",
        "type": "graphql_mutation",
        "service": "gateway",
        "tokens": ["create", "order"],
        "field_tokens": [],
        "method": None,
    }
    b = {
        "id": "orders-svc::kafka::topic::order-created",
        "type": "kafka_topic",
        "service": "orders-svc",
        "tokens": ["order", "created"],
        "field_tokens": [],
        "method": None,
    }
    scores, total = compute_scores(a, b)
    # Same domain tokens + complementary types + cross-service → meaningful score
    assert total > 0.25, f"Expected > 0.25, got {total}"
    assert scores["name_score"] > 0.25

def test_compute_scores_unrelated():
    a = {
        "id": "svc1::gql::q::getUser",
        "type": "graphql_query",
        "service": "svc1",
        "tokens": ["get", "user"],
        "field_tokens": [],
        "method": None,
    }
    b = {
        "id": "svc2::kafka::topic::payment-processed",
        "type": "kafka_topic",
        "service": "svc2",
        "tokens": ["payment", "processed"],
        "field_tokens": [],
        "method": None,
    }
    _, total = compute_scores(a, b)
    assert total == 0.0

def test_same_service_lower_score():
    """Same service should score lower than cross-service for identical token overlap."""
    a = {
        "id": "gateway::gql::m::createOrder",
        "type": "graphql_mutation",
        "service": "gateway",
        "tokens": ["create", "order"],
        "field_tokens": [],
        "method": None,
    }
    b_cross = {
        "id": "orders-svc::http::POST::/orders::createOrder",
        "type": "http_endpoint",
        "service": "orders-svc",
        "tokens": ["create", "order"],
        "field_tokens": [],
        "method": "POST",
    }
    b_same = {
        "id": "gateway::gql::type::CreateOrderResponse",
        "type": "graphql_type",
        "service": "gateway",
        "tokens": ["create", "order", "response"],
        "field_tokens": [],
        "method": None,
    }
    _, score_cross = compute_scores(a, b_cross)
    _, score_same = compute_scores(a, b_same)
    assert score_cross > score_same, (
        f"Cross-service {score_cross:.3f} should exceed same-service {score_same:.3f}"
    )


# ──────────────────────────────────────────────
# 3. DB
# ──────────────────────────────────────────────

def test_db_upsert_and_retrieve():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = DB(db_path)
    node = {
        "id": "test::gql::q::getOrder",
        "type": "graphql_query",
        "raw_name": "getOrder",
        "service": "gateway",
        "source_file": "/fake/path.gql",
        "method": None,
        "path": None,
    }
    db.upsert_node(node, ["get", "order"], ["order", "id"])
    db.commit()

    retrieved = db.get_node("test::gql::q::getOrder")
    assert retrieved is not None
    assert retrieved["raw_name"] == "getOrder"
    assert "get" in retrieved["tokens"]
    assert "order" in retrieved["field_tokens"]
    db.close()
    os.unlink(db_path)

def test_db_edge():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = DB(db_path)

    for i, (nid, rname) in enumerate([
        ("a::m::createOrder", "createOrder"),
        ("b::http::createOrder", "createOrder"),
    ]):
        db.upsert_node({
            "id": nid, "type": "graphql_mutation", "raw_name": rname,
            "service": f"svc{i}", "source_file": None, "method": None, "path": None
        }, ["create", "order"], [])
    db.upsert_edge("a::m::createOrder", "b::http::createOrder",
                   {"name_score": 0.8, "field_score": 0, "role_score": 0.3, "service_score": 1.25},
                   total=0.75)
    db.commit()
    edges = db.get_edges_for_node("a::m::createOrder")
    assert len(edges) == 1
    assert edges[0]["total_score"] == 0.75
    db.close()
    os.unlink(db_path)


# ──────────────────────────────────────────────
# 4. Embeddings
# ──────────────────────────────────────────────

def test_embedding_db_upsert_and_retrieve():
    from store.embedding_db import EmbeddingDB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        edb = EmbeddingDB(path)
        vec = [0.1, 0.2, 0.3, 0.4]
        edb.upsert("node::1", vec)
        edb.commit()
        all_vecs = edb.get_all()
        assert "node::1" in all_vecs
        retrieved = all_vecs["node::1"]
        assert len(retrieved) == 4
        assert abs(retrieved[0] - 0.1) < 1e-5
        edb.close()
    finally:
        os.unlink(path)


def test_embedding_db_stale_detection():
    from store.embedding_db import EmbeddingDB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        edb = EmbeddingDB(path)
        assert edb.is_stale(5)
        edb.upsert("n1", [0.1, 0.2])
        edb.upsert("n2", [0.3, 0.4])
        edb.commit()
        assert not edb.is_stale(2)
        assert edb.is_stale(3)
        edb.close()
    finally:
        os.unlink(path)


def test_embedder_cosine():
    from scoring.embedder import cosine
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert abs(cosine(a, b) - 1.0) < 1e-6
    c = [0.0, 1.0, 0.0]
    assert abs(cosine(a, c)) < 1e-6


def test_build_and_recall_embeddings():
    """build_embeddings + recall_by_embedding finds semantically related nodes."""
    from store.embedding_db import EmbeddingDB
    from scoring.embedder import build_embeddings, recall_by_embedding

    nodes = [
        {"id": "svc::a", "raw_name": "createOrder", "tokens": ["create", "order"], "type": "http_endpoint", "service": "svc"},
        {"id": "svc::b", "raw_name": "placePurchase", "tokens": ["place", "purchase"], "type": "graphql_mutation", "service": "svc"},
        {"id": "svc::c", "raw_name": "getWeatherForecast", "tokens": ["get", "weather", "forecast"], "type": "http_endpoint", "service": "svc"},
    ]

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        edb = EmbeddingDB(path)
        n = build_embeddings(nodes, edb)
        assert n == 3

        # "buy a product" should match createOrder/placePurchase more than weather
        results = recall_by_embedding("buy a product", nodes, edb, top_k=3, threshold=0.0)
        ids = [r["id"] for r in results]
        # weather should not rank first
        assert ids[0] != "svc::c", f"weather ranked first unexpectedly: {ids}"
        edb.close()
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 5. Feedback DB
# ──────────────────────────────────────────────

def test_feedback_db_log_and_count():
    from store.feedback_db import FeedbackDB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = FeedbackDB(path)
        assert fdb.count() == 0
        fdb.log("createOrder", 1, ["id1", "id2"], True)
        fdb.log("userProfile", 0, [], False)
        assert fdb.count() == 2
        fdb.close()
    finally:
        os.unlink(path)


def test_feedback_db_persistence():
    from store.feedback_db import FeedbackDB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        fdb = FeedbackDB(path)
        fdb.log("hint1", 1, ["n1"], True)
        fdb.close()

        fdb2 = FeedbackDB(path)
        assert fdb2.count() == 1
        fdb2.close()
    finally:
        os.unlink(path)


# ──────────────────────────────────────────────
# 6. Pluggable scanner registry
# ──────────────────────────────────────────────

def test_basescanner_abc():
    """BaseScanner cannot be instantiated directly (it's abstract)."""
    from scanner import BaseScanner
    try:
        BaseScanner()
        assert False, "Should have raised TypeError"
    except TypeError:
        pass  # expected — ABC with abstractmethod


def test_basescanner_subclass():
    """A concrete subclass of BaseScanner satisfies isinstance check."""
    from scanner import BaseScanner

    class DummyScanner(BaseScanner):
        def scan(self, repo_path: str, service: str) -> list[dict]:
            return [{"id": f"{service}::dummy::test", "type": "dummy",
                     "raw_name": "test", "service": service,
                     "source_file": None, "method": None, "path": None}]

    s = DummyScanner()
    assert isinstance(s, BaseScanner)
    result = s.scan("/tmp", "mysvc")
    assert len(result) == 1
    assert result[0]["service"] == "mysvc"


def test_resolve_scanner_builtin():
    """Built-in scanner names resolve to a bound scan method (class-based)."""
    from main import _resolve_scanner
    fn, is_class = _resolve_scanner("graphql", {})
    assert callable(fn)
    assert is_class is True


def test_resolve_scanner_unknown():
    """Unknown non-dotted names raise ValueError."""
    from main import _resolve_scanner
    try:
        _resolve_scanner("nonexistent_scanner", {})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "nonexistent_scanner" in str(e)


def test_resolve_scanner_dotted_path():
    """Dotted-path class reference is dynamically imported and instantiated."""
    import types
    import sys
    from scanner import BaseScanner

    # Build a tiny in-memory module with a scanner class
    mod = types.ModuleType("_test_custom_scanner_mod")

    class _CustomScanner(BaseScanner):
        def __init__(self, tag="default"):
            self.tag = tag

        def scan(self, repo_path: str, service: str) -> list[dict]:
            return [{"id": f"{service}::custom::{self.tag}", "type": "custom",
                     "raw_name": self.tag, "service": service,
                     "source_file": None, "method": None, "path": None,
                     "fields": []}]

    mod._CustomScanner = _CustomScanner
    sys.modules["_test_custom_scanner_mod"] = mod

    try:
        from main import _resolve_scanner
        fn, is_class = _resolve_scanner(
            "_test_custom_scanner_mod:_CustomScanner",
            {"tag": "hello"},
        )
        assert callable(fn)
        assert is_class is True
        result = fn("/tmp", "testsvc")
        assert len(result) == 1
        assert result[0]["raw_name"] == "hello"
        assert result[0]["service"] == "testsvc"
    finally:
        del sys.modules["_test_custom_scanner_mod"]


def test_pluggable_scanner_end_to_end():
    """Custom scanner declared by dotted path produces nodes in the DB via cmd_scan."""
    import types
    import sys
    import json
    import tempfile
    from scanner import BaseScanner

    # Register an in-memory scanner module
    mod = types.ModuleType("_e2e_custom_scanner")

    class _E2EScanner(BaseScanner):
        def __init__(self, label="node"):
            self.label = label

        def scan(self, repo_path: str, service: str) -> list[dict]:
            return [{
                "id": f"{service}::custom_e2e::{self.label}",
                "type": "custom_e2e",
                "raw_name": self.label,
                "service": service,
                "source_file": None,
                "method": None,
                "path": None,
                "fields": [],
            }]

    mod._E2EScanner = _E2EScanner
    sys.modules["_e2e_custom_scanner"] = mod

    try:
        with tempfile.TemporaryDirectory() as repo_dir:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as cfg_file:
                json.dump({
                    "repos": [{
                        "name": "e2e-svc",
                        "path": repo_dir,
                        "scanners": [{
                            "type": "_e2e_custom_scanner:_E2EScanner",
                            "label": "ping",
                        }],
                    }]
                }, cfg_file)
                cfg_path = cfg_file.name

            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as db_file:
                db_path = db_file.name

            import argparse
            from main import cmd_scan
            args = argparse.Namespace(
                config=cfg_path,
                db=db_path,
                force=True,
            )
            cmd_scan(args)

            from store.db import DB
            db = DB(db_path)
            nodes = db.get_nodes_by_service("e2e-svc")
            db.close()

            assert len(nodes) >= 1, f"Expected >=1 node, got {nodes}"
            assert any(n["raw_name"] == "ping" for n in nodes), \
                f"No 'ping' node found in {[n['raw_name'] for n in nodes]}"

            import os
            os.unlink(cfg_path)
            os.unlink(db_path)
    finally:
        del sys.modules["_e2e_custom_scanner"]


# ──────────────────────────────────────────────
# 7. Stale-scan warning
# ──────────────────────────────────────────────

def _make_db_with_scanned_at(scanned_at_iso: str) -> "tuple[DB, str]":
    """Helper: create a temp DB with one repo_state row at the given ISO timestamp."""
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = DB(f.name)
    db.upsert_repo_state("test-repo", "abc123", scanned_at_iso)
    db.commit()
    return db, f.name


def test_get_oldest_scanned_at_empty():
    """Empty DB returns None."""
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    try:
        db = DB(f.name)
        assert db.get_oldest_scanned_at() is None
        db.close()
    finally:
        os.unlink(f.name)


def test_get_oldest_scanned_at_single():
    """Single row returns that timestamp as an aware datetime."""
    from datetime import datetime, timezone
    ts = "2020-06-01T12:00:00+00:00"
    db, path = _make_db_with_scanned_at(ts)
    try:
        result = db.get_oldest_scanned_at()
        assert result is not None
        assert result.tzinfo is not None, "Should be timezone-aware"
        assert result == datetime(2020, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        db.close()
    finally:
        os.unlink(path)


def test_get_oldest_scanned_at_multiple():
    """Multiple rows returns the minimum."""
    from datetime import datetime, timezone
    import tempfile
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = DB(f.name)
    try:
        db.upsert_repo_state("repo-a", None, "2024-01-01T00:00:00+00:00")
        db.upsert_repo_state("repo-b", None, "2023-06-15T00:00:00+00:00")  # older
        db.upsert_repo_state("repo-c", None, "2024-05-01T00:00:00+00:00")
        db.commit()
        result = db.get_oldest_scanned_at()
        assert result == datetime(2023, 6, 15, tzinfo=timezone.utc)
        db.close()
    finally:
        os.unlink(f.name)


def test_get_oldest_scanned_at_unparseable(capsys=None):
    """Unparseable timestamp is treated as epoch (stale), no exception raised."""
    from datetime import datetime, timezone
    import tempfile, io, contextlib
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = DB(f.name)
    try:
        db.upsert_repo_state("bad-repo", None, "not-a-date")
        db.commit()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            result = db.get_oldest_scanned_at()
        assert result == datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert "unparseable" in buf.getvalue()
        db.close()
    finally:
        os.unlink(f.name)


def test_cli_stale_warning_emitted(capsys=None):
    """cmd_query emits stale warning to stderr when oldest scan > 7 days."""
    import tempfile, io, contextlib, argparse
    from datetime import datetime, timezone, timedelta
    from main import _stale_warning

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = DB(f.name)
    try:
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
        db.upsert_repo_state("repo", None, stale_ts)
        db.commit()

        args = argparse.Namespace(config="ariadne.config.json")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _stale_warning(db, args)
        output = buf.getvalue()
        assert "⚠" in output, f"Expected warning, got: {output!r}"
        assert "10 days ago" in output or "days ago" in output
        assert "scan" in output
        db.close()
    finally:
        os.unlink(f.name)


def test_cli_no_warning_when_fresh(capsys=None):
    """No warning when scan is 1 day old."""
    import tempfile, io, contextlib, argparse
    from datetime import datetime, timezone, timedelta
    from main import _stale_warning

    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = DB(f.name)
    try:
        fresh_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        db.upsert_repo_state("repo", None, fresh_ts)
        db.commit()

        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _stale_warning(db)
        assert buf.getvalue() == "", f"Expected no warning, got: {buf.getvalue()!r}"
        db.close()
    finally:
        os.unlink(f.name)


def test_mcp_stale_warning_in_payload():
    """_build_stale_warning returns a string when stale, None when fresh."""
    import tempfile
    from datetime import datetime, timezone, timedelta
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mcp_server import _build_stale_warning

    # Stale case
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db_stale = DB(f.name)
    try:
        stale_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec="seconds")
        db_stale.upsert_repo_state("repo", None, stale_ts)
        db_stale.commit()
        w = _build_stale_warning(db_stale)
        assert w is not None, "Expected stale warning"
        assert "⚠" in w
        assert "days ago" in w
        db_stale.close()
    finally:
        os.unlink(f.name)

    # Fresh case
    f2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f2.close()
    db_fresh = DB(f2.name)
    try:
        fresh_ts = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        db_fresh.upsert_repo_state("repo", None, fresh_ts)
        db_fresh.commit()
        w = _build_stale_warning(db_fresh)
        assert w is None, f"Expected no warning, got: {w!r}"
        db_fresh.close()
    finally:
        os.unlink(f2.name)


# ──────────────────────────────────────────────
# 8. Frontend REST scanner — file filter
# ──────────────────────────────────────────────

def _make_frontend_rest_repo(tmp_dir: str, files: "list[tuple[str, str]]") -> str:
    """Write files into tmp_dir and return the dir path."""
    import os
    for rel_path, content in files:
        full = os.path.join(tmp_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(content)
    return tmp_dir


def test_frontend_rest_tsx_component_scanned():
    """Dashboard.tsx with an axiosRequest.get call IS scanned and produces a node."""
    import tempfile
    from scanner.frontend_rest_scanner import scan_frontend_rest

    axios_call = (
        "class DashboardService {\n"
        "  async fetchData() {\n"
        "    return this.axiosRequest.get('/foo');\n"
        "  }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        _make_frontend_rest_repo(tmp, [("src/pages/Dashboard.tsx", axios_call)])
        nodes = scan_frontend_rest(tmp, "frontend")
    assert len(nodes) >= 1, f"Expected >=1 node from Dashboard.tsx, got {nodes}"
    paths = [n["path"] for n in nodes]
    assert "/foo" in paths, f"/foo not found in paths: {paths}"


def test_frontend_rest_test_file_skipped():
    """userService.test.ts with an axios call is SKIPPED."""
    import tempfile
    from scanner.frontend_rest_scanner import scan_frontend_rest

    axios_call = (
        "class X {\n"
        "  async run() {\n"
        "    return this.axiosRequest.get('/bar');\n"
        "  }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        _make_frontend_rest_repo(tmp, [("src/api/userService.test.ts", axios_call)])
        nodes = scan_frontend_rest(tmp, "frontend")
    assert nodes == [], f"Expected no nodes from .test.ts, got {nodes}"


def test_frontend_rest_dts_file_skipped():
    """api.d.ts is SKIPPED (type declaration)."""
    import tempfile
    from scanner.frontend_rest_scanner import scan_frontend_rest

    dts_content = "export declare function getUser(): Promise<void>;\n"
    with tempfile.TemporaryDirectory() as tmp:
        _make_frontend_rest_repo(tmp, [("src/types/api.d.ts", dts_content)])
        nodes = scan_frontend_rest(tmp, "frontend")
    assert nodes == [], f"Expected no nodes from .d.ts, got {nodes}"


def test_frontend_rest_stories_file_skipped():
    """Button.stories.tsx is SKIPPED (Storybook)."""
    import tempfile
    from scanner.frontend_rest_scanner import scan_frontend_rest

    stories_content = (
        "class StoryHelper {\n"
        "  async load() {\n"
        "    return this.axiosRequest.get('/story-api');\n"
        "  }\n"
        "}\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        _make_frontend_rest_repo(tmp, [("src/components/Button.stories.tsx", stories_content)])
        nodes = scan_frontend_rest(tmp, "frontend")
    assert nodes == [], f"Expected no nodes from .stories.tsx, got {nodes}"


# ──────────────────────────────────────────────
# Rescan MCP tool — end-to-end
# ──────────────────────────────────────────────

def _write(path: str, body: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)


def test_rescan_missing_manifest_returns_error():
    """No manifest.json → rescan returns error JSON, does not crash."""
    import asyncio
    import json as _json
    import mcp_server

    with tempfile.TemporaryDirectory() as workspace:
        data_dir = os.path.join(workspace, ".ariadne")
        os.makedirs(data_dir)
        original_db, original_emb = mcp_server._DB_PATH, mcp_server._EMB_PATH
        mcp_server._DB_PATH = os.path.join(data_dir, "ariadne.db")
        mcp_server._EMB_PATH = os.path.join(data_dir, "embeddings.db")
        try:
            result = asyncio.run(mcp_server._rescan())
            payload = _json.loads(result[0].text)
            assert "error" in payload
            assert "manifest" in payload["error"].lower()
        finally:
            mcp_server._DB_PATH = original_db
            mcp_server._EMB_PATH = original_emb


def test_rescan_refreshes_index_and_invalidates_cache():
    """
    End-to-end: install populates a temp workspace, we add a new GraphQL file
    to one of the scanned repos, _rescan() sees it, node count grows, and the
    cached DB handle is reset so the next query sees fresh data.
    """
    import asyncio
    import json as _json
    import mcp_server
    import main as _main

    with tempfile.TemporaryDirectory() as workspace:
        # 1. Build a minimal fake repo with one .graphql file
        repo_dir = os.path.join(workspace, "repo")
        graphql_dir = os.path.join(repo_dir, "schema")
        _write(
            os.path.join(graphql_dir, "order.graphql"),
            "type Mutation {\n  createOrder(input: String): String\n}\n",
        )

        # 2. Write config pointing at that repo
        config_path = os.path.join(workspace, "ariadne.config.json")
        _write(config_path, _json.dumps({
            "repos": [{"name": "fake", "path": repo_dir, "scanners": ["graphql"]}]
        }))

        # 3. Simulate an install: run scan_and_embed + write manifest
        data_dir = os.path.join(workspace, ".ariadne")
        os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "ariadne.db")
        emb_path = os.path.join(data_dir, "embeddings.db")
        manifest_path = os.path.join(data_dir, "manifest.json")

        _main.run_scan_and_embed(config_path, db_path, emb_path)
        _write(manifest_path, _json.dumps({"config_path": config_path}))

        # Verify install-time state
        from store.db import DB
        nodes_before = DB(db_path).node_count()
        assert nodes_before >= 1, "initial scan should find at least createOrder"

        # 4. Point mcp_server at this workspace and warm its DB cache
        original_db, original_emb = mcp_server._DB_PATH, mcp_server._EMB_PATH
        mcp_server._DB_PATH = db_path
        mcp_server._EMB_PATH = emb_path
        mcp_server._reset_db_cache()
        try:
            cached_before = mcp_server._get_db(db_path)
            assert cached_before is mcp_server._db, "cache should be warm"

            # 5. Add a new .graphql file — rescan must pick it up
            _write(
                os.path.join(graphql_dir, "user.graphql"),
                "type Query {\n  userProfile(id: ID): String\n}\n",
            )

            result = asyncio.run(mcp_server._rescan())
            payload = _json.loads(result[0].text)
            assert "error" not in payload, f"rescan errored: {payload}"
            assert "nodes" in payload and "duration_ms" in payload
            assert payload["nodes"] > nodes_before, (
                f"nodes should grow after adding a file: {nodes_before} → {payload['nodes']}"
            )

            # 6. Cache must be invalidated — _db is None until next _get_db()
            assert mcp_server._db is None, "rescan should reset cached DB handle"
            cached_after = mcp_server._get_db(db_path)
            assert cached_after is not cached_before, "re-opened handle must be a new object"
            assert cached_after.node_count() == payload["nodes"]
        finally:
            mcp_server._DB_PATH = original_db
            mcp_server._EMB_PATH = original_emb
            mcp_server._reset_db_cache()


# ──────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_split_camel,
        test_split_kebab,
        test_split_pascal,
        test_normalize_fields,
        test_jaccard_exact,
        test_jaccard_empty,
        test_jaccard_partial,
        test_compute_scores_complementary_cross_service,
        test_compute_scores_unrelated,
        test_same_service_lower_score,
        test_db_upsert_and_retrieve,
        test_db_edge,
        test_embedding_db_upsert_and_retrieve,
        test_embedding_db_stale_detection,
        test_embedder_cosine,
        test_build_and_recall_embeddings,
        test_feedback_db_log_and_count,
        test_feedback_db_persistence,
        test_basescanner_abc,
        test_basescanner_subclass,
        test_resolve_scanner_builtin,
        test_resolve_scanner_unknown,
        test_resolve_scanner_dotted_path,
        test_pluggable_scanner_end_to_end,
        test_get_oldest_scanned_at_empty,
        test_get_oldest_scanned_at_single,
        test_get_oldest_scanned_at_multiple,
        test_get_oldest_scanned_at_unparseable,
        test_cli_stale_warning_emitted,
        test_cli_no_warning_when_fresh,
        test_mcp_stale_warning_in_payload,
        test_frontend_rest_tsx_component_scanned,
        test_frontend_rest_test_file_skipped,
        test_frontend_rest_dts_file_skipped,
        test_frontend_rest_stories_file_skipped,
        test_rescan_missing_manifest_returns_error,
        test_rescan_refreshes_index_and_invalidates_cache,
    ]

    passed = failed = 0
    for t in tests:
        try:
            print(f"  {t.__name__} ... ", end="", flush=True)
            t()
            print("OK")
            passed += 1
        except Exception as e:
            print(f"FAIL: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
