#!/usr/bin/env python3
"""Fixed-input, leave-one-component-out quality experiment. No live telemetry."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack, redirect_stdout
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import statistics
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ariadne_mcp.cli import run_scan
from ariadne_mcp.query.query import query
from ariadne_mcp.store.db import DB
from ariadne_mcp.store.feedback_db import FeedbackDB
from ariadne_mcp.scanner import EXCLUDED_DIRS
from run import load_samples, prepare_sample, token_counter


@contextmanager
def source_policy(enabled):
    """Experimental removal of the shared exclusion policy, not a product flag."""
    with ExitStack() as stack:
        if not enabled:
            legacy = lambda root, pattern="*": Path(root).rglob(pattern)
            stack.enter_context(patch("ariadne_mcp.scanner.source_files", legacy))
            for path in (ROOT / "ariadne_mcp/scanner").glob("*_scanner.py"):
                module = importlib.import_module("ariadne_mcp.scanner." + path.stem)
                stack.enter_context(patch.object(module, "source_files", legacy))
        yield


def scan(config, db_path, exclusions=True):
    with source_policy(exclusions), redirect_stdout(io.StringIO()):
        run_scan(str(config), str(db_path), force=True)


def rank(results, judgment):
    expected = set(judgment["expected_node_ids"])
    if not expected:
        return None
    for i, cluster in enumerate(results, 1):
        ids = {n["id"] for n in cluster["nodes"]}
        if (expected <= ids if judgment.get("match") == "all" else bool(expected & ids)):
            return i
    return None


def metrics(rows):
    positives = [r for r in rows if not r["expect_empty"]]
    negatives = [r for r in rows if r["expect_empty"]]
    source_nodes = [n for r in rows for c in r["results"] for n in c["nodes"]]
    return {
        "positive_queries": len(positives), "negative_queries": len(negatives),
        "top1": sum(r["rank"] == 1 for r in positives)/len(positives),
        "top3": sum(r["rank"] is not None and r["rank"] <= 3 for r in positives)/len(positives),
        "mrr": sum(1/r["rank"] if r["rank"] else 0 for r in positives)/len(positives),
        "unsupported_false_positive_rate": (sum(bool(r["results"]) for r in negatives)/len(negatives) if negatives else None),
        "forbidden_node_results": sum(r["forbidden_present"] for r in rows),
        "source_evidence_rate": (sum(bool(n.get("source_file")) and Path(n["source_file"]).is_file() and not set(Path(n["source_file"]).relative_to(r["source_root"]).parts) & EXCLUDED_DIRS for r in rows for c in r["results"] for n in c["nodes"])/len(source_nodes) if source_nodes else None),
        "mean_output_tokens": statistics.mean(r["tokens"] for r in rows),
        "median_query_ms": statistics.median(r["query_ms"] for r in rows),
        "max_score": max((c["confidence"] for r in rows for c in r["results"]), default=0),
    }


def evaluate(db_path, judgments, source_root, *, singleton=True, evidence=True, fdb=None):
    count_tokens = token_counter()
    db = DB(str(db_path))
    rows = []
    for judgment in judgments:
        times = []
        outputs = []
        for _ in range(2):
            started = time.perf_counter()
            result = query(db, judgment["hint"], top_n=3, fdb=fdb, singleton_fallback=singleton, evidence_gate=evidence)
            times.append((time.perf_counter()-started)*1000)
            outputs.append(result)
        if outputs[0] != outputs[1]:
            raise AssertionError(f"non-deterministic query: {judgment['hint']}")
        payload = json.dumps({"chains": result, "stale_warning": None}, sort_keys=True)
        ids = {n["id"] for c in result for n in c["nodes"]}
        rows.append({"hint": judgment["hint"], "expect_empty": judgment.get("expect_empty", False),
                     "rank": rank(result, judgment), "forbidden_present": bool(ids & set(judgment.get("forbidden_node_ids", []))),
                     "results": result, "tokens": count_tokens(payload), "query_ms": statistics.median(times), "source_root": str(source_root)})
    db.close()
    return {"metrics": metrics(rows), "queries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=ROOT / "benchmarks/.work/ablation")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    fixture = work / "business"
    shutil.copytree(ROOT / "tests/fixtures/business", fixture, dirs_exist_ok=True)
    for directory, source in {
        "dist": 'type StudentProfileSummary { removedBuildField: String }\ntype Query { staleGeneratedOperation: String }',
        "node_modules/dependency": 'type Query { dependencyOnlyOperation: String }',
    }.items():
        path = fixture / "gateway" / directory / "schema.gql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    fixture_digest = hashlib.sha256()
    for path in sorted(fixture.rglob("*")):
        if path.is_file():
            fixture_digest.update(path.relative_to(fixture).as_posix().encode() + b"\0" + path.read_bytes())
    datasets = {"business": {
        "revision": fixture_digest.hexdigest(),
        "config": fixture / "ariadne.config.json", "source_root": fixture,
        "judgments": [json.loads(line) for line in (ROOT / "benchmarks/business-judgments.jsonl").read_text().splitlines()],
    }}
    for name, spec in load_samples(ROOT / "benchmarks/samples.json").items():
        sample = prepare_sample(name, spec, ROOT / "benchmarks/.work")
        datasets[name] = {"revision": sample["metadata"]["revision"], "config": sample["config"], "source_root": sample["checkout"], "judgments": sample["judgments"]}
    reports = {}
    for name, dataset in datasets.items():
        normal = work / f"{name}.db"
        unfiltered = work / f"{name}-unfiltered.db"
        scan(dataset["config"], normal)
        scan(dataset["config"], unfiltered, exclusions=False)
        fdb = FeedbackDB(str(work / f"{name}-feedback.db"))
        # Deliberately noisy *exploration*, never evaluation labels. The full
        # ranking must ignore it. No positive gold labels are used for training.
        fdb.conn.execute("DELETE FROM feedback")
        if name == "business":
            for _ in range(100):
                fdb.log("createOrder", 1, ["gateway::gql::Mutation::createSubscription"], True, source="implicit_expand")
        from ariadne_mcp.scoring import engine
        route_score = engine._node_hint_score
        def without_route(node, tokens):
            return route_score({k: v for k, v in node.items() if k != "path"}, tokens)
        modes = {
            "full": (normal, True, True, fdb),
            "without_feedback": (normal, True, True, None),
            "without_singleton": (normal, False, True, fdb),
            "without_evidence_gate": (normal, True, False, fdb),
            "without_source_exclusion": (unfiltered, True, True, fdb),
        }
        reports[name] = {mode: evaluate(db_path, dataset["judgments"], dataset["source_root"], singleton=singleton, evidence=evidence, fdb=feedback) for mode,(db_path,singleton,evidence,feedback) in modes.items()}
        with patch.object(engine, "_node_hint_score", without_route):
            reports[name]["without_route_evidence"] = evaluate(normal, dataset["judgments"], dataset["source_root"], fdb=fdb)
        fdb.close()
    report = {"protocol": "Fixed source checkouts; leave one component out; each query repeated twice; exploration-only feedback stress; no evaluation labels used as feedback.",
              "source_revisions": {name: d["revision"] for name, d in datasets.items()},
              "judgment_sha256": {name: hashlib.sha256(json.dumps(d["judgments"], sort_keys=True).encode()).hexdigest() for name,d in datasets.items()},
              "datasets": reports}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    for name, modes in reports.items():
        for mode, result in modes.items():
            print(name, mode, json.dumps(result["metrics"], sort_keys=True))


if __name__ == "__main__":
    main()
