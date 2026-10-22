# Ariadne Benchmark

**48 manually reviewed queries across 4 pinned public stacks.**

All backends receive the same literal hints. Retrieval metrics use manually reviewed contract nodes/lines. Tokens count the complete top-3 chain envelope (including source paths and directed edges) or normalized baseline output with `cl100k_base`; empty-result guidance and transport framing are excluded. Query time is the median of five warm runs; Ariadne indexing is reported separately.

## Aggregate

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 48 | 89.6% | 95.8% | 0.927 | 0.38 ms | 374.5 |
| rg | 48 | 37.5% | 56.2% | 0.510 | 13.64 ms | 590.6 |
| grep | 48 | 37.5% | 56.2% | 0.510 | 13.81 ms | 590.6 |

## Per sample

### fastapi-microservices

Pinned revision: `262bd1b7a97d6a6375067abac778bb8d75bb5edc`

Queries: 12

Ariadne index time: 0.072 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 12 | 91.7% | 91.7% | 0.917 | 0.17 ms | 142.4 |
| rg | 12 | 66.7% | 91.7% | 0.785 | 7.59 ms | 77.7 |
| grep | 12 | 66.7% | 91.7% | 0.785 | 4.56 ms | 77.7 |

### kafka-microservices

Pinned revision: `a4752cdb21d1d06bffb398fd5d128d14a87c6cec`

Queries: 6

Ariadne index time: 0.083 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 6 | 100.0% | 100.0% | 1.000 | 0.15 ms | 390.0 |
| rg | 6 | 16.7% | 50.0% | 0.380 | 10.60 ms | 1878.3 |
| grep | 6 | 16.7% | 50.0% | 0.380 | 10.73 ms | 1878.3 |

### one-platform

Pinned revision: `4abf36c30380647e077c140096e715dd07e997a3`

Queries: 18

Ariadne index time: 0.127 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 18 | 83.3% | 94.4% | 0.889 | 0.98 ms | 487.9 |
| rg | 18 | 0.0% | 16.7% | 0.179 | 17.49 ms | 323.8 |
| grep | 18 | 0.0% | 16.7% | 0.179 | 41.79 ms | 323.8 |

### spring-petclinic

Pinned revision: `305a1f13e4f961001d4e6cb50a9db51dc3fc5967`

Queries: 12

Ariadne index time: 0.186 s

| Backend | Queries | Top-1 | Top-3 | MRR | Median warm query | Mean output tokens |
|---|---:|---:|---:|---:|---:|---:|
| ariadne | 12 | 91.7% | 100.0% | 0.958 | 0.37 ms | 428.8 |
| rg | 12 | 75.0% | 83.3% | 0.796 | 13.64 ms | 859.8 |
| grep | 12 | 75.0% | 83.3% | 0.796 | 13.81 ms | 859.8 |

## Reproduce

```bash
uv run --locked --extra benchmark python benchmarks/run.py
```

The first `tiktoken` encoding use downloads its encoding data, so the initial
run needs network access. Ariadne's benchmark path also uses the system `git`
command for clone and freshness checks; the `rg` and `grep` baselines use those
system commands directly. Ensure all three are available on `PATH`.

Raw per-query evidence is in [`benchmarks/results.json`](benchmarks/results.json).
Results are local-machine evidence, not a universal performance claim. The
corpus is operation-name-heavy with a smaller set of broader business hints; it
measures deterministic contract lookup compatibility, not natural-language
relevance. These fixed labels are a development regression corpus, not a
held-out natural-language benchmark. See `docs/QUALITY_EXPERIMENT.md` for
upgrade and ablation results.
