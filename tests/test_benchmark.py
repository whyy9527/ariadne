import importlib.util
from pathlib import Path


def _benchmark_module():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "run.py"
    spec = importlib.util.spec_from_file_location("ariadne_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rank_and_summary_metrics():
    benchmark = _benchmark_module()
    assert benchmark.rank_of(["other", "hit"], lambda value: value == "hit") == 2
    assert benchmark.rank_of(["other"], lambda value: value == "hit") is None

    rows = []
    for backend in ("ariadne", "rg", "grep"):
        rows.extend([
            {"backend": backend, "rank": 1, "query_ms": 2.0, "output_tokens": 10},
            {"backend": backend, "rank": 3, "query_ms": 4.0, "output_tokens": 20},
            {"backend": backend, "rank": None, "query_ms": 6.0, "output_tokens": 30},
        ])
    summary = benchmark.summarize(rows)["ariadne"]
    assert summary["top_1_hit_rate"] == 1 / 3
    assert summary["top_3_hit_rate"] == 2 / 3
    assert summary["mrr"] == (1 + 1 / 3) / 3
    assert summary["median_query_ms"] == 4.0
    assert summary["mean_output_tokens"] == 20


def test_normalize_matches_is_stable(tmp_path):
    benchmark = _benchmark_module()
    stdout = "\n".join([
        f"{tmp_path}/b.java:9: owner later",
        f"{tmp_path}/a.java:12: owner second",
        f"{tmp_path}/a.java:2: owner first",
    ])
    assert benchmark.normalize_matches(stdout, tmp_path) == [
        {"path": "a.java", "line": 2, "text": "owner first"},
        {"path": "a.java", "line": 12, "text": "owner second"},
        {"path": "b.java", "line": 9, "text": "owner later"},
    ]


def test_relevance_uses_reviewed_locator():
    benchmark = _benchmark_module()
    locator = [{"path_suffix": "service/OwnerResource.java", "contains": "createOwner"}]
    assert benchmark.line_is_relevant(
        {"path": "service/OwnerResource.java", "text": "public Owner createOwner(...)"},
        locator,
    )
    assert not benchmark.line_is_relevant(
        {"path": "service/OwnerResource.java", "text": "public Owner updateOwner(...)"},
        locator,
    )


def test_public_benchmark_corpus_has_exact_reviewed_distribution():
    benchmark = _benchmark_module()
    samples = benchmark.load_samples(benchmark.BENCHMARK_DIR / "samples.json")
    counts = {
        name: len(benchmark.load_judgments(benchmark.BENCHMARK_DIR / sample["judgments"]))
        for name, sample in samples.items()
    }
    assert counts == {
        "spring-petclinic": 12,
        "one-platform": 18,
        "kafka-microservices": 6,
        "fastapi-microservices": 12,
    }
    assert sum(counts.values()) == 48


def test_every_judgment_has_unique_hint_and_reviewable_locators():
    benchmark = _benchmark_module()
    samples = benchmark.load_samples(benchmark.BENCHMARK_DIR / "samples.json")
    for sample in samples.values():
        judgments = benchmark.load_judgments(benchmark.BENCHMARK_DIR / sample["judgments"])
        assert len({row["hint"].casefold() for row in judgments}) == len(judgments)
        for row in judgments:
            assert all("::" in node_id for node_id in row["expected_node_ids"])
            assert all(locator["path_suffix"] and locator["contains"] for locator in row["baseline_locators"])
