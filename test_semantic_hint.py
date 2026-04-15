"""
Tests for ariadne.
Run: python3 -m pytest test_semantic_hint.py -v
or:  python3 test_semantic_hint.py
"""
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

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
    """Built-in scanner names resolve to callable functions."""
    from main import _resolve_scanner
    fn, is_class = _resolve_scanner("graphql", {})
    assert callable(fn)
    assert is_class is False


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
