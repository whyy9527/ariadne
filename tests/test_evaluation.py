"""
Tests for offline query-ranking evaluation.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_jsonl(rows: list[dict]) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        return f.name


def test_load_judgments_normalizes_jsonl():
    from ariadne_mcp.evaluation import load_judgments

    path = _write_jsonl([
        {"hint": " createOrder ", "expected_node_ids": "node:ORDER", "k": "3"},
        {"hint": "getUser", "expected_node_ids": ["node:USER"], "match": "all"},
    ])
    try:
        judgments = load_judgments(path)
        assert judgments[0]["hint"] == "createOrder"
        assert judgments[0]["expected_node_ids"] == ["node:ORDER"]
        assert judgments[0]["k"] == 3
        assert judgments[1]["match"] == "all"
    finally:
        os.unlink(path)


def test_load_judgments_rejects_bad_match_mode():
    import pytest
    from ariadne_mcp.evaluation import JudgmentError, load_judgments

    path = _write_jsonl([
        {"hint": "createOrder", "expected_node_ids": ["node:ORDER"], "match": "partial"},
    ])
    try:
        with pytest.raises(JudgmentError, match="'match' must be 'any' or 'all'"):
            load_judgments(path)
    finally:
        os.unlink(path)


def test_evaluate_judgments_hit_rate_and_mrr():
    from ariadne_mcp.evaluation import evaluate_judgments

    def fake_query(_db, hint, top_n=5, fdb=None):
        results = {
            "createOrder": [
                {"nodes": [{"id": "node:OTHER"}]},
                {"nodes": [{"id": "node:ORDER"}, {"id": "node:EVENT"}]},
            ],
            "getUser": [
                {"nodes": [{"id": "node:USER"}]},
            ],
            "missing": [
                {"nodes": [{"id": "node:OTHER"}]},
            ],
        }
        return results[hint][:top_n]

    report = evaluate_judgments(
        object(),
        [
            {"hint": "createOrder", "expected_node_ids": ["node:ORDER"], "match": "any", "k": 2},
            {"hint": "getUser", "expected_node_ids": ["node:USER"], "match": "any", "k": 1},
            {"hint": "missing", "expected_node_ids": ["node:MISSING"], "match": "any", "k": 1},
        ],
        query_fn=fake_query,
    )

    assert report["metrics"]["total"] == 3
    assert report["metrics"]["hits"] == 2
    assert report["metrics"]["hit_rate"] == 2 / 3
    assert report["metrics"]["mrr"] == (0.5 + 1.0) / 3
    assert report["results"][0]["rank"] == 2
    assert report["results"][2]["hit"] is False


def test_evaluate_judgments_all_match_requires_same_cluster():
    from ariadne_mcp.evaluation import evaluate_judgments

    def fake_query(_db, hint, top_n=5, fdb=None):
        return [
            {"nodes": [{"id": "node:ORDER"}]},
            {"nodes": [{"id": "node:ORDER"}, {"id": "node:EVENT"}]},
        ][:top_n]

    report = evaluate_judgments(
        object(),
        [
            {
                "hint": "createOrder",
                "expected_node_ids": ["node:ORDER", "node:EVENT"],
                "match": "all",
                "k": 2,
            },
        ],
        query_fn=fake_query,
    )

    assert report["metrics"]["hits"] == 1
    assert report["results"][0]["rank"] == 2
    assert report["results"][0]["matched_node_ids"] == ["node:EVENT", "node:ORDER"]


def test_evaluate_judgments_uses_stable_default_retrieval_depth():
    from ariadne_mcp.evaluation import evaluate_judgments

    calls = []

    def fake_query(_db, hint, top_n=5, fdb=None):
        calls.append(top_n)
        return [
            {"nodes": [{"id": "node:OTHER"}]},
            {"nodes": [{"id": "node:ORDER"}]},
        ][:top_n]

    report = evaluate_judgments(
        object(),
        [{"hint": "createOrder", "expected_node_ids": ["node:ORDER"], "match": "any"}],
        top_k=3,
        query_fn=fake_query,
    )

    assert calls == [10]
    assert report["metrics"]["retrieval_depth"] == 10
    assert report["metrics"]["hits"] == 1
    assert report["results"][0]["rank"] == 2


def test_evaluate_judgments_rejects_depth_below_top_k():
    import pytest
    from ariadne_mcp.evaluation import evaluate_judgments

    with pytest.raises(ValueError, match="retrieval_depth must be >= top_k"):
        evaluate_judgments(
            object(),
            [{"hint": "createOrder", "expected_node_ids": ["node:ORDER"], "match": "any"}],
            top_k=5,
            retrieval_depth=3,
            query_fn=lambda *_args, **_kwargs: [],
        )


def test_cli_parser_accepts_eval_args():
    from ariadne_mcp.cli import build_parser

    parser = build_parser()
    args = parser.parse_args([
        "--db",
        "ariadne.db",
        "eval",
        "judgments.jsonl",
        "--top",
        "3",
        "--feedback-db",
        "feedback.db",
        "--min-hit-rate",
        "0.8",
        "--min-mrr",
        "0.6",
    ])

    assert args.command == "eval"
    assert args.judgments == "judgments.jsonl"
    assert args.top == 3
    assert args.feedback_db == "feedback.db"
    assert args.min_hit_rate == 0.8
    assert args.min_mrr == 0.6
