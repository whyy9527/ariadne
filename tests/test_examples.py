import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _runner_module():
    path = ROOT / "examples" / "run.py"
    spec = importlib.util.spec_from_file_location("ariadne_examples", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_public_example_manifests_are_complete():
    for name in ("spring-petclinic", "one-platform", "kafka-microservices"):
        example = ROOT / "examples" / name
        metadata = json.loads((example / "metadata.json").read_text(encoding="utf-8"))
        expected = json.loads((example / "expected.json").read_text(encoding="utf-8"))
        config = json.loads((example / "ariadne.config.json").read_text(encoding="utf-8"))

        assert metadata["repository"].startswith("https://github.com/")
        assert len(metadata["revision"]) == 40
        assert metadata["license"] in {"Apache-2.0", "MIT"}
        assert metadata["checkout_dir"]
        assert expected["hint"]
        assert expected["match"] in {"any", "all"}
        assert expected["expected_node_ids"]
        assert config["repos"]


def test_matching_rank_supports_any_and_all():
    runner = _runner_module()
    results = [
        {"nodes": [{"id": "node:a"}]},
        {"nodes": [{"id": "node:a"}, {"id": "node:b"}]},
    ]
    assert runner.matching_rank(results, {"node:b"}, "any") == 2
    assert runner.matching_rank(results, {"node:a", "node:b"}, "all") == 2
    assert runner.matching_rank(results, {"node:c"}, "any") is None
