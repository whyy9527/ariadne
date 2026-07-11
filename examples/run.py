#!/usr/bin/env python3
"""Clone, scan, and verify a pinned public Ariadne example."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def ensure_checkout(metadata: dict[str, Any], checkout: Path) -> None:
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(checkout)], check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git", "-C", str(checkout), "fetch", "--depth", "1",
                metadata["repository"], metadata["revision"],
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(checkout), "checkout", "--detach", "FETCH_HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if revision != metadata["revision"]:
        raise RuntimeError(f"checkout revision is {revision}; expected {metadata['revision']}")


def matching_rank(results: list[dict[str, Any]], expected: set[str], match: str) -> int | None:
    for rank, cluster in enumerate(results, 1):
        node_ids = {node.get("id") for node in cluster.get("nodes", []) if node.get("id")}
        if match == "all" and expected <= node_ids:
            return rank
        if match == "any" and expected & node_ids:
            return rank
    return None


def run_example(name: str, work_root: Path) -> int:
    example_dir = EXAMPLES / name
    if not example_dir.is_dir():
        available = ", ".join(sorted(path.name for path in EXAMPLES.iterdir() if (path / "metadata.json").exists()))
        raise ValueError(f"unknown example {name!r}; available: {available}")

    metadata = load_json(example_dir / "metadata.json")
    expected = load_json(example_dir / "expected.json")
    work_dir = work_root / name
    checkout = work_dir / metadata["checkout_dir"]
    ensure_checkout(metadata, checkout)

    config = work_dir / "ariadne.config.json"
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_dir / "ariadne.config.json", config)
    db_path = work_dir / "ariadne.db"
    db_path.unlink(missing_ok=True)
    subprocess.run(
        [
            sys.executable, "-m", "ariadne_mcp.cli", "--db", str(db_path),
            "scan", "--config", str(config),
        ],
        cwd=ROOT,
        check=True,
    )

    from ariadne_mcp.query.query import print_results, query
    from ariadne_mcp.store.db import DB

    db = DB(str(db_path))
    results = query(db, expected["hint"], top_n=int(expected.get("top", 5)))
    db.close()
    rank = matching_rank(results, set(expected["expected_node_ids"]), expected.get("match", "any"))
    print(f"\nQuery: {expected['hint']}\n" + "=" * 50)
    print_results(results)
    if rank is None:
        print("ERROR: reviewed expected nodes were not returned", file=sys.stderr)
        return 1
    print(f"\nPASS: reviewed expected nodes matched cluster rank {rank}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", help="example directory name")
    parser.add_argument("--work-dir", type=Path, default=EXAMPLES / ".work")
    args = parser.parse_args()
    raise SystemExit(run_example(args.example, args.work_dir.resolve()))


if __name__ == "__main__":
    main()
