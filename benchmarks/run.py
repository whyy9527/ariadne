#!/usr/bin/env python3
"""Reproducible Ariadne versus rg/grep benchmark on Spring Petclinic."""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPO_URL = "https://github.com/spring-petclinic/spring-petclinic-microservices.git"
REPO_REVISION = "305a1f13e4f961001d4e6cb50a9db51dc3fc5967"
REPETITIONS = 5
TOP_K = 3

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "benchmarks"


def load_judgments(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = json.loads(line)
        if not row.get("hint") or not row.get("expected_node_ids") or not row.get("baseline_locators"):
            raise ValueError(f"{path}:{line_number}: incomplete judgment")
        rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no judgments")
    return rows


def ensure_sample(sample: Path) -> None:
    if not (sample / ".git").exists():
        sample.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(sample)], check=True, capture_output=True, text=True)
        subprocess.run(
            ["git", "-C", str(sample), "fetch", "--depth", "1", REPO_URL, REPO_REVISION],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(sample), "checkout", "--detach", "FETCH_HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    revision = subprocess.run(
        ["git", "-C", str(sample), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != REPO_REVISION:
        raise RuntimeError(f"sample revision is {revision}; expected {REPO_REVISION}")


def write_config(sample: Path, config_path: Path) -> None:
    services = [
        "spring-petclinic-api-gateway",
        "spring-petclinic-customers-service",
        "spring-petclinic-vets-service",
        "spring-petclinic-visits-service",
        "spring-petclinic-genai-service",
    ]
    config = {"repos": [{"path": str(sample / service)} for service in services]}
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def build_index(config: Path, db_path: Path) -> float:
    db_path.unlink(missing_ok=True)
    started = time.perf_counter()
    subprocess.run(
        [sys.executable, "-m", "ariadne_mcp.cli", "--db", str(db_path), "scan", "--config", str(config)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return time.perf_counter() - started


def serialize_ariadne(results: list[dict[str, Any]]) -> str:
    payload = []
    for cluster in results[:TOP_K]:
        payload.append({
            "confidence": cluster.get("confidence"),
            "nodes": [
                {
                    "id": node.get("id"),
                    "service": node.get("service"),
                    "type": node.get("type"),
                    "label": node.get("label"),
                    "name": node.get("name"),
                }
                for node in cluster.get("nodes", [])
            ],
        })
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def normalize_matches(stdout: str, sample: Path) -> list[dict[str, Any]]:
    rows = []
    prefix = str(sample) + "/"
    for raw in stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path = parts[0]
        if path.startswith(prefix):
            path = path[len(prefix):]
        rows.append({"path": path, "line": int(parts[1]), "text": parts[2].strip()})
    return sorted(rows, key=lambda row: (row["path"], row["line"], row["text"]))


def run_baseline(backend: str, hint: str, sample: Path) -> list[dict[str, Any]]:
    if backend == "rg":
        command = ["rg", "--no-heading", "--line-number", "--color", "never", "-i", "-F", "-g", "*.java", hint, str(sample)]
    elif backend == "grep":
        command = [
            "grep", "-R", "-I", "-n", "-i", "-F",
            "--include=*.java", "--exclude-dir=.git", hint, str(sample),
        ]
    else:
        raise ValueError(f"unknown baseline: {backend}")
    proc = subprocess.run(command, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"{backend} failed: {proc.stderr.strip()}")
    return normalize_matches(proc.stdout, sample)


def line_is_relevant(row: dict[str, Any], locators: list[dict[str, str]]) -> bool:
    return any(
        row["path"].endswith(locator["path_suffix"])
        and locator["contains"].casefold() in row["text"].casefold()
        for locator in locators
    )


def cluster_is_relevant(cluster: dict[str, Any], expected: set[str]) -> bool:
    node_ids = set(cluster.get("node_ids", []))
    node_ids.update(node.get("id") for node in cluster.get("nodes", []) if node.get("id"))
    return bool(node_ids & expected)


def rank_of(items: list[Any], predicate: Callable[[Any], bool]) -> int | None:
    for rank, item in enumerate(items, 1):
        if predicate(item):
            return rank
    return None


def timed(call: Callable[[], Any], repetitions: int = REPETITIONS) -> tuple[Any, float]:
    values = []
    result = None
    for _ in range(repetitions):
        started = time.perf_counter()
        result = call()
        values.append((time.perf_counter() - started) * 1000)
    return result, statistics.median(values)


def token_counter() -> Callable[[str], int]:
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError("install benchmark dependencies with: pip install -e '.[benchmark]'") from exc
    encoding = tiktoken.get_encoding("cl100k_base")
    return lambda text: len(encoding.encode(text))


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    summary = {}
    for backend in ("ariadne", "rg", "grep"):
        selected = [row for row in rows if row["backend"] == backend]
        total = len(selected)
        summary[backend] = {
            "queries": total,
            "top_1_hit_rate": sum(row["rank"] == 1 for row in selected) / total,
            "top_3_hit_rate": sum(row["rank"] is not None and row["rank"] <= 3 for row in selected) / total,
            "mrr": sum(1 / row["rank"] if row["rank"] else 0 for row in selected) / total,
            "median_query_ms": statistics.median(row["query_ms"] for row in selected),
            "mean_output_tokens": statistics.mean(row["output_tokens"] for row in selected),
        }
    return summary


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Ariadne Benchmark",
        "",
        f"Pinned sample: [`spring-petclinic-microservices@{report['sample_revision'][:8]}`]({REPO_URL[:-4]}/commit/{report['sample_revision']})",
        "",
        "All backends receive the same literal hints. Retrieval metrics use manually reviewed contract nodes/lines. "
        "Tokens count the exact serialized top-3 Ariadne payload or complete normalized baseline output with `cl100k_base`. "
        "Query time is the median of five warm runs; Ariadne indexing is reported separately.",
        "",
        f"Ariadne index time: **{report['index_seconds']:.3f} s**",
        "",
        "| Backend | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for backend in ("ariadne", "rg", "grep"):
        row = report["summary"][backend]
        lines.append(
            f"| {backend} | {row['top_1_hit_rate']:.1%} | {row['top_3_hit_rate']:.1%} | "
            f"{row['mrr']:.3f} | {row['median_query_ms']:.2f} ms | {row['mean_output_tokens']:.1f} |"
        )
    lines.extend([
        "",
        "## Reproduce",
        "",
        "```bash",
        "python -m pip install -e '.[benchmark]'",
        "python benchmarks/run.py",
        "```",
        "",
        "Raw per-query evidence is in [`benchmarks/results.json`](benchmarks/results.json). "
        "Results are local-machine evidence, not a universal performance claim.",
        "",
    ])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    for binary in ("git", "rg", "grep"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"required executable not found: {binary}")
    count_tokens = token_counter()
    work = args.work_dir.resolve()
    sample = work / "spring-petclinic-microservices"
    work.mkdir(parents=True, exist_ok=True)
    ensure_sample(sample)
    config = work / "ariadne.config.json"
    db_path = work / "ariadne.db"
    write_config(sample, config)
    index_seconds = build_index(config, db_path)

    from ariadne_mcp.query.query import query
    from ariadne_mcp.store.db import DB

    db = DB(str(db_path))
    rows = []
    for judgment in load_judgments(args.judgments):
        hint = judgment["hint"]
        expected = set(judgment["expected_node_ids"])
        ariadne_results, ariadne_ms = timed(lambda: query(db, hint, top_n=TOP_K))
        ariadne_text = serialize_ariadne(ariadne_results)
        ariadne_rank = rank_of(ariadne_results, lambda item: cluster_is_relevant(item, expected))
        rows.append({
            "hint": hint,
            "backend": "ariadne",
            "rank": ariadne_rank,
            "query_ms": ariadne_ms,
            "output_tokens": count_tokens(ariadne_text),
            "result_count": len(ariadne_results),
            "serialized_output": ariadne_text,
        })
        for backend in ("rg", "grep"):
            matches, query_ms = timed(lambda backend=backend: run_baseline(backend, hint, sample))
            serialized = "\n".join(f"{row['path']}:{row['line']}:{row['text']}" for row in matches)
            rank = rank_of(matches, lambda item: line_is_relevant(item, judgment["baseline_locators"]))
            rows.append({
                "hint": hint,
                "backend": backend,
                "rank": rank,
                "query_ms": query_ms,
                "output_tokens": count_tokens(serialized),
                "result_count": len(matches),
                "serialized_output": serialized,
            })
    db.close()
    return {
        "schema_version": 1,
        "sample_url": REPO_URL,
        "sample_revision": REPO_REVISION,
        "query_repetitions": REPETITIONS,
        "top_k": TOP_K,
        "index_seconds": index_seconds,
        "summary": summarize(rows),
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--judgments", type=Path, default=BENCHMARK_DIR / "judgments.jsonl")
    parser.add_argument("--work-dir", type=Path, default=BENCHMARK_DIR / ".work")
    parser.add_argument("--json-output", type=Path, default=BENCHMARK_DIR / "results.json")
    parser.add_argument("--markdown-output", type=Path, default=ROOT / "BENCHMARKS.md")
    args = parser.parse_args()
    report = run(args)
    args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(f"wrote {args.json_output}")
    print(f"wrote {args.markdown_output}")


if __name__ == "__main__":
    main()
