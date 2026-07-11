from datetime import datetime, timezone
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _snapshot_module():
    path = ROOT / "scripts" / "adoption_snapshot.py"
    spec = importlib.util.spec_from_file_location("adoption_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classifies_external_maintainer_and_bot_issues():
    module = _snapshot_module()
    issues = [
        {"created_at": "2026-07-10T00:00:00Z", "user": {"login": "reader", "type": "User"}},
        {"created_at": "2026-07-09T00:00:00Z", "user": {"login": "whyy9527", "type": "User"}},
        {"created_at": "2026-07-08T00:00:00Z", "user": {"login": "helper[bot]", "type": "Bot"}},
        {"created_at": "2026-07-07T00:00:00Z", "user": {"login": "reader", "type": "User"}, "pull_request": {}},
        {"created_at": "2026-01-01T00:00:00Z", "user": {"login": "old", "type": "User"}},
    ]
    counts = module.classify_issue_activity(
        issues,
        maintainer="whyy9527",
        since=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert counts == {"total": 3, "external": 1, "maintainer": 1, "bot": 1}


def test_markdown_reports_windows_and_caveat():
    module = _snapshot_module()
    snapshot = {
        "generated_at": "2026-07-11T00:00:00+00:00",
        "repository": "whyy9527/ariadne",
        "package": "ariadne-mcp",
        "github_clones": {"window_days": 14, "count": 20, "uniques": 7},
        "pypi_downloads": {"last_day": 2, "last_week": 10, "last_month": 30},
        "github_issues": {"window_days": 30, "total": 3, "external": 1, "maintainer": 1, "bot": 1},
    }
    report = module.render_markdown(snapshot)
    assert "GitHub unique cloners | 14 days | 7" in report
    assert "External issues opened | 30 days | 1" in report
    assert "CI, mirrors, bots" in report
    assert "Stars are intentionally not used" in report
