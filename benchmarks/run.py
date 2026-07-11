#!/usr/bin/env python3
"""Reproducible Ariadne versus rg/grep benchmark on pinned public stacks."""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

REPETITIONS = 5
TOP_K = 3

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "benchmarks"
EXAMPLES_DIR = ROOT / "examples"


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


def load_samples(path: Path) -> dict[str, dict[str, Any]]:
    samples = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(samples, dict) or not samples:
        raise ValueError(f"{path}: expected a non-empty sample object")
    for name, sample in samples.items():
        for key in ("example", "judgments", "source_globs"):
            if not sample.get(key):
                raise ValueError(f"{path}: sample {name!r} is missing {key!r}")
    return samples


def load_example_runner():
    path = EXAMPLES_DIR / "run.py"
    spec = importlib.util.spec_from_file_location("ariadne_example_runner", path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"cannot load example runner: {path}")
    spec.loader.exec_module(module)
    return module


def prepare_sample(name: str, sample: dict[str, Any], work_root: Path) -> dict[str, Any]:
    example_dir = EXAMPLES_DIR / sample["example"]
    metadata = json.loads((example_dir / "metadata.json").read_text(encoding="utf-8"))
    work_dir = work_root / name
    checkout = work_dir / metadata["checkout_dir"]
    load_example_runner().ensure_checkout(metadata, checkout)
    work_dir.mkdir(parents=True, exist_ok=True)
    config = work_dir / "ariadne.config.json"
    shutil.copyfile(example_dir / "ariadne.config.json", config)
    return {
        "name": name,
        "metadata": metadata,
        "work_dir": work_dir,
        "checkout": checkout,
        "config": config,
        "db": work_dir / "ariadne.db",
        "judgments": load_judgments(BENCHMARK_DIR / sample["judgments"]),
        "source_globs": sample["source_globs"],
    }


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


def normalize_matches(stdout: str, checkout: Path) -> list[dict[str, Any]]:
    rows = []
    prefix = str(checkout) + "/"
    for raw in stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path = parts[0]
        if path.startswith(prefix):
            path = path[len(prefix):]
        rows.append({"path": path, "line": int(parts[1]), "text": parts[2].strip()})
    return sorted(rows, key=lambda row: (row["path"], row["line"], row["text"]))


def run_baseline(
    backend: str,
    hint: str,
    checkout: Path,
    source_globs: list[str],
) -> list[dict[str, Any]]:
    if backend == "rg":
        command = ["rg", "--no-heading", "--line-number", "--color", "never", "-i", "-F"]
        for source_glob in source_globs:
            command.extend(["-g", source_glob])
        command.extend([hint, str(checkout)])
    elif backend == "grep":
        command = ["grep", "-R", "-I", "-n", "-i", "-F", "--exclude-dir=.git"]
        command.extend(f"--include={source_glob}" for source_glob in source_globs)
        command.extend([hint, str(checkout)])
    else:
        raise ValueError(f"unknown baseline: {backend}")
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode not in (0, 1):
        raise RuntimeError(f"{backend} failed: {process.stderr.strip()}")
    return normalize_matches(process.stdout, checkout)


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
        if not total:
            continue
        summary[backend] = {
            "queries": total,
            "top_1_hit_rate": sum(row["rank"] == 1 for row in selected) / total,
            "top_3_hit_rate": sum(row["rank"] is not None and row["rank"] <= 3 for row in selected) / total,
            "mrr": sum(1 / row["rank"] if row["rank"] else 0 for row in selected) / total,
            "median_query_ms": statistics.median(row["query_ms"] for row in selected),
            "mean_output_tokens": statistics.mean(row["output_tokens"] for row in selected),
        }
    return summary


def markdown_table(summary: dict[str, dict[str, float]]) -> list[str]:
    lines = [
        "| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for backend in ("ariadne", "rg", "grep"):
        row = summary[backend]
        lines.append(
            f"| {backend} | {row['queries']} | {row['top_1_hit_rate']:.1%} | "
            f"{row['top_3_hit_rate']:.1%} | {row['mrr']:.3f} | "
            f"{row['median_query_ms']:.2f} ms | {row['mean_output_tokens']:.1f} |"
        )
    return lines


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Ariadne Benchmark",
        "",
        f"**{report['query_count']} manually reviewed queries across {len(report['samples'])} pinned public stacks.**",
        "",
        "All backends receive the same literal hints. Retrieval metrics use manually reviewed contract nodes/lines. "
        "Tokens count the exact serialized top-3 Ariadne payload or complete normalized baseline output with `cl100k_base`. "
        "Query time is the median of five warm runs; Ariadne indexing is reported separately.",
        "",
        "## Aggregate",
        "",
        *markdown_table(report["summary"]),
        "",
        "## Per sample",
        "",
    ]
    for sample_name, sample in report["samples"].items():
        lines.extend([
            f"### {sample_name}",
            "",
            f"Pinned revision: `{sample['revision']}`",
            "",
            f"Queries: {sample['query_count']}",
            "",
            f"Ariadne index time: {sample['index_seconds']:.3f} s",
            "",
            *markdown_table(sample["summary"]),
            "",
        ])
    lines.extend([
        "## Reproduce",
        "",
        "```bash",
        "python -m pip install -e '.[benchmark]'",
        "python benchmarks/run.py",
        "```",
        "",
        "Raw per-query evidence is in [`benchmarks/results.json`](benchmarks/results.json). "
        "Results are local-machine evidence, not a universal performance claim. "
        "The corpus is operation-name-heavy with a smaller set of broader business hints; "
        "it measures deterministic contract lookup compatibility, not natural-language relevance. "
        "No ranking was tuned for this run.",
        "",
    ])
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    for binary in ("git", "rg", "grep"):
        if shutil.which(binary) is None:
            raise RuntimeError(f"required executable not found: {binary}")
    count_tokens = token_counter()
    sample_specs = load_samples(args.samples)
    rows = []
    sample_reports = {}

    from ariadne_mcp.query.query import query
    from ariadne_mcp.store.db import DB

    for sample_name, sample_spec in sample_specs.items():
        sample = prepare_sample(sample_name, sample_spec, args.work_dir.resolve())
        index_seconds = build_index(sample["config"], sample["db"])
        db = DB(str(sample["db"]))
        sample_rows = []
        for judgment in sample["judgments"]:
            hint = judgment["hint"]
            expected = set(judgment["expected_node_ids"])
            ariadne_results, ariadne_ms = timed(lambda: query(db, hint, top_n=TOP_K))
            ariadne_text = serialize_ariadne(ariadne_results)
            ariadne_rank = rank_of(ariadne_results, lambda item: cluster_is_relevant(item, expected))
            sample_rows.append({
                "sample": sample_name,
                "hint": hint,
                "backend": "ariadne",
                "rank": ariadne_rank,
                "query_ms": ariadne_ms,
                "output_tokens": count_tokens(ariadne_text),
                "result_count": len(ariadne_results),
                "serialized_output": ariadne_text,
            })
            for backend in ("rg", "grep"):
                matches, query_ms = timed(
                    lambda backend=backend: run_baseline(
                        backend, hint, sample["checkout"], sample["source_globs"]
                    )
                )
                serialized = "\n".join(
                    f"{row['path']}:{row['line']}:{row['text']}" for row in matches
                )
                rank = rank_of(
                    matches,
                    lambda item: line_is_relevant(item, judgment["baseline_locators"]),
                )
                sample_rows.append({
                    "sample": sample_name,
                    "hint": hint,
                    "backend": backend,
                    "rank": rank,
                    "query_ms": query_ms,
                    "output_tokens": count_tokens(serialized),
                    "result_count": len(matches),
                    "serialized_output": serialized,
                })
        db.close()
        rows.extend(sample_rows)
        sample_reports[sample_name] = {
            "revision": sample["metadata"]["revision"],
            "query_count": len(sample["judgments"]),
            "index_seconds": index_seconds,
            "summary": summarize(sample_rows),
        }

    return {
        "schema_version": 2,
        "query_repetitions": REPETITIONS,
        "top_k": TOP_K,
        "query_count": sum(sample["query_count"] for sample in sample_reports.values()),
        "summary": summarize(rows),
        "samples": sample_reports,
        "results": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=BENCHMARK_DIR / "samples.json")
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
