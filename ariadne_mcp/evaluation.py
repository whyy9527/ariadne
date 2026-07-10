"""
Offline evaluation helpers for Ariadne query ranking.

Judgments are JSONL records:

    {"hint": "createOrder", "expected_node_ids": ["svc::gql::m::createOrder"], "k": 3}

The evaluator treats each returned cluster as a ranked document. A judgment is
a hit when any expected node id appears in a top-k cluster. Set
``"match": "all"`` when all expected node ids must appear in the same cluster.
Internally, eval fetches a stable candidate depth before slicing to top-k so
separate top-k runs are comparable.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


class JudgmentError(ValueError):
    """Raised when an eval judgment file is malformed."""


def load_judgments(path: str | Path) -> list[dict[str, Any]]:
    """Load and validate JSONL judgments from *path*."""
    judgments: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise JudgmentError(f"{path}:{lineno}: invalid JSON: {exc.msg}") from exc
            judgments.append(_normalize_judgment(item, path, lineno))

    if not judgments:
        raise JudgmentError(f"{path}: no judgments found")
    return judgments


def _normalize_judgment(item: Any, path: str | Path, lineno: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise JudgmentError(f"{path}:{lineno}: expected a JSON object")

    hint = item.get("hint")
    if not isinstance(hint, str) or not hint.strip():
        raise JudgmentError(f"{path}:{lineno}: 'hint' must be a non-empty string")

    expected = item.get("expected_node_ids")
    if isinstance(expected, str):
        expected = [expected]
    if (
        not isinstance(expected, list)
        or not expected
        or not all(isinstance(node_id, str) and node_id for node_id in expected)
    ):
        raise JudgmentError(
            f"{path}:{lineno}: 'expected_node_ids' must be a non-empty string list"
        )

    match = item.get("match", "any")
    if match not in {"any", "all"}:
        raise JudgmentError(f"{path}:{lineno}: 'match' must be 'any' or 'all'")

    k = item.get("k")
    if k is not None:
        try:
            k = int(k)
        except (TypeError, ValueError) as exc:
            raise JudgmentError(f"{path}:{lineno}: 'k' must be an integer") from exc
        if k < 1:
            raise JudgmentError(f"{path}:{lineno}: 'k' must be >= 1")

    normalized = dict(item)
    normalized["hint"] = hint.strip()
    normalized["expected_node_ids"] = expected
    normalized["match"] = match
    if k is not None:
        normalized["k"] = k
    return normalized


def evaluate_judgments(
    db: Any,
    judgments: list[dict[str, Any]],
    *,
    top_k: int = 5,
    retrieval_depth: int | None = None,
    query_fn: Callable[..., list[dict[str, Any]]] | None = None,
    fdb: Any = None,
) -> dict[str, Any]:
    """Run judgments against Ariadne query results and return metrics + rows."""
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    if retrieval_depth is not None and retrieval_depth < top_k:
        raise ValueError("retrieval_depth must be >= top_k")
    if not judgments:
        raise ValueError("judgments must not be empty")

    if query_fn is None:
        from ariadne_mcp.query.query import query as query_fn

    rows: list[dict[str, Any]] = []
    hits = 0
    reciprocal_rank_total = 0.0
    base_depth = retrieval_depth or max(10, top_k)

    for judgment in judgments:
        hint = judgment["hint"]
        k = int(judgment.get("k") or top_k)
        depth = max(k, base_depth)
        results = query_fn(db, hint, top_n=depth, fdb=fdb)
        rank, matched_node_ids = _find_hit_rank(
            results[:k],
            set(judgment["expected_node_ids"]),
            judgment["match"],
        )

        hit = rank is not None
        if hit:
            hits += 1
            reciprocal_rank_total += 1.0 / rank

        rows.append({
            "hint": hint,
            "k": k,
            "match": judgment["match"],
            "expected_node_ids": judgment["expected_node_ids"],
            "hit": hit,
            "rank": rank,
            "matched_node_ids": sorted(matched_node_ids),
        })

    total = len(judgments)
    return {
        "metrics": {
            "total": total,
            "hits": hits,
            "hit_rate": hits / total,
            "mrr": reciprocal_rank_total / total,
            "k": top_k,
            "retrieval_depth": base_depth,
        },
        "results": rows,
    }


def _find_hit_rank(
    results: list[dict[str, Any]],
    expected_node_ids: set[str],
    match: str,
) -> tuple[int | None, set[str]]:
    for rank, cluster in enumerate(results, 1):
        cluster_node_ids = set(_cluster_node_ids(cluster))
        matched = expected_node_ids & cluster_node_ids
        if match == "all":
            if expected_node_ids <= cluster_node_ids:
                return rank, matched
        elif matched:
            return rank, matched
    return None, set()


def _cluster_node_ids(cluster: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for node_id in cluster.get("node_ids", []) or []:
        if node_id:
            ids.append(node_id)
    for node in cluster.get("nodes", []) or []:
        node_id = node.get("id")
        if node_id:
            ids.append(node_id)
    return list(dict.fromkeys(ids))


def format_eval_report(report: dict[str, Any], *, path: str | None = None) -> str:
    """Format a compact human-readable evaluation report."""
    metrics = report["metrics"]
    lines = []
    if path:
        lines.append(f"Eval: {path}")
    lines.extend([
        f"Queries: {metrics['total']}",
        f"Depth: {metrics['retrieval_depth']}",
        f"Hits: {metrics['hits']}/{metrics['total']} = {metrics['hit_rate']:.3f}",
        f"MRR: {metrics['mrr']:.3f}",
        "",
        "Results:",
    ])

    for row in report["results"]:
        status = "PASS" if row["hit"] else "FAIL"
        rank = row["rank"] if row["rank"] is not None else "-"
        expected = ", ".join(row["expected_node_ids"])
        matched = ", ".join(row["matched_node_ids"]) or "-"
        lines.append(
            f"  {status} {row['hint']!r} rank={rank} "
            f"k={row['k']} match={row['match']} matched=[{matched}] expected=[{expected}]"
        )
    return "\n".join(lines)
