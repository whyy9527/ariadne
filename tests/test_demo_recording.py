import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_demo_setup_and_example_pin_the_same_source_revision():
    setup = (ROOT / "docs" / "demo" / "setup.py").read_text(encoding="utf-8")
    metadata_path = ROOT / "examples" / "spring-petclinic" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["revision"]
    assert '"metadata.json"' in setup
    assert 'metadata["repository"]' in setup
    assert 'metadata["revision"]' in setup


def test_demo_tape_shows_real_scan_query_and_product_boundary():
    tape = (ROOT / "docs" / "demo.tape").read_text(encoding="utf-8")
    assert "ariadne-mcp --db petclinic.db scan" in tape
    assert "/usr/bin/time -p ariadne-mcp --db petclinic.db query owner" in tape
    assert "No model call, embeddings, vector database" in tape
    assert "python examples/run.py spring-petclinic" in tape
