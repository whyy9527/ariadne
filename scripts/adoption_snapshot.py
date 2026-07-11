#!/usr/bin/env python3
"""Write a local weekly Ariadne adoption snapshot from aggregate platform APIs."""
from __future__ import annotations

import argparse
import json
import subprocess
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def github_api(endpoint: str) -> Any:
    process = subprocess.run(
        ["gh", "api", endpoint, "--paginate"],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    if len(documents) == 1:
        return documents[0]
    merged = []
    for document in documents:
        if not isinstance(document, list):
            raise ValueError(f"paginated GitHub response is not a list: {endpoint}")
        merged.extend(document)
    return merged


def public_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "ariadne-adoption-snapshot"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def classify_issue_activity(
    issues: list[dict[str, Any]],
    *,
    maintainer: str,
    since: datetime,
) -> dict[str, int]:
    counts = {"total": 0, "external": 0, "maintainer": 0, "bot": 0}
    for issue in issues:
        if "pull_request" in issue:
            continue
        created_at = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        if created_at < since:
            continue
        login = issue.get("user", {}).get("login", "")
        user_type = issue.get("user", {}).get("type", "")
        counts["total"] += 1
        if user_type == "Bot" or login.endswith("[bot]"):
            counts["bot"] += 1
        elif login.casefold() == maintainer.casefold():
            counts["maintainer"] += 1
        else:
            counts["external"] += 1
    return counts


def build_snapshot(
    *,
    repository: str,
    package: str,
    maintainer: str,
    now: datetime,
) -> dict[str, Any]:
    clones = github_api(f"repos/{repository}/traffic/clones")
    issues = github_api(f"repos/{repository}/issues?state=all&per_page=100")
    pypi = public_json(f"https://pypistats.org/api/packages/{package}/recent")["data"]
    issue_window_days = 30
    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "repository": repository,
        "package": package,
        "github_clones": {
            "window_days": 14,
            "count": int(clones["count"]),
            "uniques": int(clones["uniques"]),
        },
        "pypi_downloads": {
            "last_day": int(pypi["last_day"]),
            "last_week": int(pypi["last_week"]),
            "last_month": int(pypi["last_month"]),
        },
        "github_issues": {
            "window_days": issue_window_days,
            **classify_issue_activity(
                issues,
                maintainer=maintainer,
                since=now - timedelta(days=issue_window_days),
            ),
        },
    }


def render_markdown(snapshot: dict[str, Any]) -> str:
    clones = snapshot["github_clones"]
    downloads = snapshot["pypi_downloads"]
    issues = snapshot["github_issues"]
    return "\n".join([
        f"# Ariadne adoption snapshot — {snapshot['generated_at'][:10]}",
        "",
        f"Repository: `{snapshot['repository']}`  ",
        f"Package: `{snapshot['package']}`",
        "",
        "| Signal | Window | Value |",
        "|---|---:|---:|",
        f"| GitHub unique cloners | {clones['window_days']} days | {clones['uniques']} |",
        f"| GitHub total clones | {clones['window_days']} days | {clones['count']} |",
        f"| PyPI downloads | 1 day | {downloads['last_day']} |",
        f"| PyPI downloads | 7 days | {downloads['last_week']} |",
        f"| PyPI downloads | 30 days | {downloads['last_month']} |",
        f"| External issues opened | {issues['window_days']} days | {issues['external']} |",
        f"| Maintainer issues opened | {issues['window_days']} days | {issues['maintainer']} |",
        f"| Bot issues opened | {issues['window_days']} days | {issues['bot']} |",
        "",
        "Clone and download totals can include CI, mirrors, bots, and repeated installs. "
        "Interpret them with external issues and opt-in qualitative feedback.",
        "",
        "Stars are intentionally not used as the primary adoption signal.",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="whyy9527/ariadne")
    parser.add_argument("--package", default="ariadne-mcp")
    parser.add_argument("--maintainer", default="whyy9527")
    parser.add_argument("--output-dir", type=Path, default=Path("adoption-snapshots"))
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    snapshot = build_snapshot(
        repository=args.repository,
        package=args.package,
        maintainer=args.maintainer,
        now=now,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ariadne-adoption-{now.date().isoformat()}"
    json_path = args.output_dir / f"{stem}.json"
    markdown_path = args.output_dir / f"{stem}.md"
    json_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(snapshot), encoding="utf-8")
    print(markdown_path)
    print(json_path)


if __name__ == "__main__":
    main()
