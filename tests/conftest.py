"""Isolated, source-backed business workspace; never touches an installed index."""
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ariadne_mcp.cli import run_scan
from ariadne_mcp.store.db import DB
from ariadne_mcp.store.feedback_db import FeedbackDB


@pytest.fixture
def business(tmp_path):
    root = tmp_path / "workspace"
    shutil.copytree(Path(__file__).parent / "fixtures" / "business", root)
    # Generated and vendor contracts deliberately collide with source IDs.
    for directory, text in {
        "dist": "type StudentProfileSummary { removedBuildField: String }\ntype Query { staleGeneratedOperation: String }",
        "node_modules/dependency": "type Query { dependencyOnlyOperation: String }",
    }.items():
        path = root / "gateway" / directory / "schema.gql"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    config = root / "ariadne.config.json"
    db_path, fb_path = tmp_path / "ariadne.db", tmp_path / "feedback.db"
    run_scan(str(config), str(db_path), force=True)
    (tmp_path / "manifest.json").write_text(json.dumps({"config_path": str(config)}))
    db, feedback = DB(str(db_path)), FeedbackDB(str(fb_path))
    yield SimpleNamespace(root=root, config=config, db_path=db_path, fb_path=fb_path, db=db, feedback=feedback)
    db.close()
    feedback.close()


@pytest.fixture
def server_session(business, monkeypatch):
    from ariadne_mcp import server
    from collections import deque
    monkeypatch.setattr(server, "_DB_PATH", str(business.db_path))
    monkeypatch.setattr(server, "_FB_PATH", str(business.fb_path))
    monkeypatch.setattr(server, "_db", None)
    monkeypatch.setattr(server, "_fdb", None)
    monkeypatch.setattr(server, "_PendingQueries", deque(maxlen=server._PENDING_MAX))
    yield server
    if server._db is not None:
        server._db.close()
    if server._fdb is not None:
        server._fdb.close()


@pytest.fixture(autouse=True)
def isolate_scoring_state(monkeypatch):
    from ariadne_mcp.scoring import engine
    monkeypatch.setattr(engine, "_IDF", {})
