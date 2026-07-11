# Ariadne Benchmark

Pinned sample: [`spring-petclinic-microservices@305a1f13`](https://github.com/spring-petclinic/spring-petclinic-microservices/commit/305a1f13e4f961001d4e6cb50a9db51dc3fc5967)

All backends receive the same literal hints. Retrieval metrics use manually reviewed contract nodes/lines. Tokens count the exact serialized top-3 Ariadne payload or complete normalized baseline output with `cl100k_base`. Query time is the median of five warm runs; Ariadne indexing is reported separately.

Ariadne index time: **0.118 s**

| Backend | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|
| ariadne | 33.3% | 50.0% | 0.417 | 0.18 ms | 166.7 |
| rg | 33.3% | 50.0% | 0.458 | 8.82 ms | 1856.7 |
| grep | 33.3% | 50.0% | 0.458 | 8.85 ms | 1856.7 |

## Reproduce

```bash
python -m pip install -e '.[benchmark]'
python benchmarks/run.py
```

Raw per-query evidence is in [`benchmarks/results.json`](benchmarks/results.json). Results are local-machine evidence, not a universal performance claim.
