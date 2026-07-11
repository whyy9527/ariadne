# Ariadne Benchmark

**48 manually reviewed queries across 4 pinned public stacks.**

All backends receive the same literal hints. Retrieval metrics use manually reviewed contract nodes/lines. Tokens count the exact serialized top-3 Ariadne payload or complete normalized baseline output with `cl100k_base`. Query time is the median of five warm runs; Ariadne indexing is reported separately.

## Aggregate

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 48 | 64.6% | 70.8% | 0.677 | 0.25 ms | 157.0 |
| rg | 48 | 37.5% | 56.2% | 0.510 | 9.19 ms | 590.6 |
| grep | 48 | 37.5% | 56.2% | 0.510 | 9.08 ms | 590.6 |

## Per sample

### spring-petclinic

Pinned revision: `305a1f13e4f961001d4e6cb50a9db51dc3fc5967`

Queries: 12

Ariadne index time: 0.119 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 12 | 75.0% | 83.3% | 0.792 | 0.25 ms | 181.7 |
| rg | 12 | 75.0% | 83.3% | 0.796 | 8.89 ms | 859.8 |
| grep | 12 | 75.0% | 83.3% | 0.796 | 8.96 ms | 859.8 |

### one-platform

Pinned revision: `4abf36c30380647e077c140096e715dd07e997a3`

Queries: 18

Ariadne index time: 0.062 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 18 | 66.7% | 77.8% | 0.722 | 0.84 ms | 223.9 |
| rg | 18 | 0.0% | 16.7% | 0.179 | 16.04 ms | 323.8 |
| grep | 18 | 0.0% | 16.7% | 0.179 | 36.78 ms | 323.8 |

### kafka-microservices

Pinned revision: `a4752cdb21d1d06bffb398fd5d128d14a87c6cec`

Queries: 6

Ariadne index time: 0.054 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 6 | 100.0% | 100.0% | 1.000 | 0.15 ms | 143.0 |
| rg | 6 | 16.7% | 50.0% | 0.380 | 8.97 ms | 1878.3 |
| grep | 6 | 16.7% | 50.0% | 0.380 | 9.10 ms | 1878.3 |

### fastapi-microservices

Pinned revision: `262bd1b7a97d6a6375067abac778bb8d75bb5edc`

Queries: 12

Ariadne index time: 0.047 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 12 | 33.3% | 33.3% | 0.333 | 0.09 ms | 38.9 |
| rg | 12 | 66.7% | 91.7% | 0.785 | 5.86 ms | 77.7 |
| grep | 12 | 66.7% | 91.7% | 0.785 | 3.39 ms | 77.7 |

## Reproduce

```bash
python -m pip install -e '.[benchmark]'
python benchmarks/run.py
```

Raw per-query evidence is in [`benchmarks/results.json`](benchmarks/results.json). Results are local-machine evidence, not a universal performance claim. The corpus is operation-name-heavy with a smaller set of broader business hints; it measures deterministic contract lookup compatibility, not natural-language relevance. No ranking was tuned for this run.
