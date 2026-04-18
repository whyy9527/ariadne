"""Tests for scanner.auto_detect + _normalize_config (config zero-config path)."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ariadne_mcp.scanner.auto_detect import detect_scanners
from ariadne_mcp.cli import _normalize_config


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_detect_jvm_backend(tmp_path: Path) -> None:
    _write(tmp_path / "pom.xml", "<project/>")
    assert detect_scanners(str(tmp_path)) == ["http", "kafka", "backend_clients"]


def test_detect_jvm_bff_prepends_graphql(tmp_path: Path) -> None:
    _write(tmp_path / "build.gradle.kts")
    _write(tmp_path / "src/schema.graphql", "type Query { _: Int }")
    assert detect_scanners(str(tmp_path)) == ["graphql", "http", "kafka", "backend_clients"]


def test_detect_ts_bff_via_apollo(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"@apollo/server": "^4.0.0"}
    }))
    assert detect_scanners(str(tmp_path)) == ["graphql", "ts_http_outbound"]


def test_detect_ts_bff_via_sdl(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", "{}")
    _write(tmp_path / "schema.graphql", "type Query { _: Int }")
    assert detect_scanners(str(tmp_path)) == ["graphql", "ts_http_outbound"]


def test_detect_ts_bff_non_apollo_framework_via_sdl(tmp_path: Path) -> None:
    """Pins the SDL fallback: a TS repo using Mercurius/Yoga (not Apollo)
    still classifies as BFF because a .graphql file is present. Guards the
    silent-drift hazard where changing GraphQL server lib flips detection
    to the frontend default."""
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"graphql-yoga": "^5.0.0", "graphql": "^16.0.0"}
    }))
    _write(tmp_path / "src/schema/schema.graphql", "type Query { _: Int }")
    assert detect_scanners(str(tmp_path)) == ["graphql", "ts_http_outbound"]


def test_detect_frontend(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"next": "14.0.0", "react": "18.0.0"}
    }))
    assert detect_scanners(str(tmp_path)) == ["frontend_graphql", "frontend_rest"]


def test_detect_cube(tmp_path: Path) -> None:
    _write(tmp_path / "package.json", json.dumps({
        "dependencies": {"@cubejs-backend/server": "0.34.0"}
    }))
    assert detect_scanners(str(tmp_path)) == ["cube"]


def test_detect_unknown(tmp_path: Path) -> None:
    assert detect_scanners(str(tmp_path)) == []


def test_normalize_fills_name_and_scanners(tmp_path: Path) -> None:
    repo = tmp_path / "orders-svc"
    _write(repo / "pom.xml", "<project/>")

    cfg = {"repos": [{"path": "./orders-svc"}]}
    diag = _normalize_config(cfg, str(tmp_path))

    entry = cfg["repos"][0]
    assert entry["name"] == "orders-svc"
    assert entry["scanners"] == ["http", "kafka", "backend_clients"]
    assert diag["inferred_scanners"]["orders-svc"] == ["http", "kafka", "backend_clients"]
    # No private markers leaked onto the entry.
    assert not any(k.startswith("_") for k in entry.keys())


def test_normalize_preserves_explicit_scanners(tmp_path: Path) -> None:
    repo = tmp_path / "svc"
    _write(repo / "pom.xml")
    cfg = {"repos": [{"path": "./svc", "scanners": ["http"]}]}
    diag = _normalize_config(cfg, str(tmp_path))
    assert cfg["repos"][0]["scanners"] == ["http"]
    assert diag["inferred_scanners"] == {}


def test_normalize_reports_detect_failure_for_unknown_repo(tmp_path: Path) -> None:
    """Fowler: a repo that can't be classified should be loud, not silent."""
    (tmp_path / "mystery").mkdir()
    cfg = {"repos": [{"path": "./mystery"}]}
    diag = _normalize_config(cfg, str(tmp_path))
    assert diag["detect_failures"] == ["mystery"]
    assert cfg["repos"][0]["scanners"] == []


def test_normalize_infers_bff_services(tmp_path: Path) -> None:
    bff = tmp_path / "gateway"
    _write(bff / "package.json", json.dumps({"dependencies": {"@apollo/server": "*"}}))
    backend = tmp_path / "svc"
    _write(backend / "pom.xml")
    cfg = {"repos": [{"path": "./gateway"}, {"path": "./svc"}]}
    diag = _normalize_config(cfg, str(tmp_path))
    assert cfg["bff_services"] == ["gateway"]
    assert diag["inferred_bff"] == ["gateway"]


def test_normalize_preserves_explicit_bff_services(tmp_path: Path) -> None:
    bff = tmp_path / "gateway"
    _write(bff / "package.json", json.dumps({"dependencies": {"@apollo/server": "*"}}))
    cfg = {"repos": [{"path": "./gateway"}], "bff_services": ["other"]}
    diag = _normalize_config(cfg, str(tmp_path))
    assert cfg["bff_services"] == ["other"]
    assert diag["inferred_bff"] is None
