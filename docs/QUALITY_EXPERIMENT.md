# Quality upgrade experiment

Baseline: `ee0965d`. Both versions scan the same four public repositories at the revisions in `benchmarks/samples.json`, with the same 48 judgments. Baseline code was archived separately; only its output serializer was updated to include the same complete chain envelope. No evaluation labels were inserted as feedback. These are development regression results, not an independent estimate of production accuracy.

| Version | Top-1 | Top-3 | MRR | Mean output tokens | Median warm query |
|---|---:|---:|---:|---:|---:|
| Baseline | 64.6% | 70.8% | 0.677 | 187.4 | 0.249 ms |
| Upgrade | 89.6% | 95.8% | 0.927 | 374.5 | 0.385 ms |

The upgrade adds 12 Top-3 hits with no Top-3 regressions on this corpus. Two queries still miss: `validateAPIKey` and `read_users`. Source paths and directed edges are counted in both versions; additional recovered results and source evidence increase output size. Tokens use `cl100k_base` before paths are anonymized, so checkout location affects the exact count. The benchmark counts the chain envelope, excluding empty-result guidance and protocol framing. Timings cover warm retrieval, excluding process startup, MCP transport and stale-repository checks.

The synthetic business suite has six positive and six negative judgments. Positive judgments cover frontend → GraphQL → REST, Kafka producer → consumer, profile contracts, structural subscriptions and an isolated health route. Negatives include generated dependencies, unsupported infrastructure hints and billing/subscription ambiguity. Separate executable tests cover all built-in scanners, real MCP stdio, explicit feedback after expansion, stale HEAD/missing repositories, refresh/deletion, scanner configuration changes, duplicate contracts and concurrent legacy-schema migration.

| Removed component | Business positive Top-3 | Unsupported false positives | Additional observation |
|---|---:|---:|---|
| None | 6/6 | 0/6 | All returned source files pass the path eligibility check |
| Feedback | 6/6 | 0/6 | Same rankings; exploratory clicks correctly have no effect |
| Singleton fallback | 5/6 | 0/6 | Isolated health route disappears |
| Evidence eligibility | 5/6 | 2/6 | Billing contaminates structural subscription lookup |
| Shared source exclusion | 6/6 | 2/6 | Generated/vendor operations leak; 17/19 returned nodes pass path eligibility |
| REST route relevance | 6/6 | 0/6 | No effect on this synthetic corpus; exercised by route regression tests |

Across the 48 public queries, removing singleton fallback reduces Top-3 from 46/48 to 34/48. Other ablations leave their ranks unchanged. This corpus does **not** demonstrate a retrieval gain from manual feedback; the experiment supplies only misleading exploratory events. Behavioral tests independently verify the ±0.10 bound, rejection, expiry, explicit-only eligibility and attribution. Source eligibility measures file existence and excluded-directory policy, not semantic truth. Returned graph edges remain static-analysis candidates requiring source inspection.

Each query was evaluated twice, and the complete experiment was independently repeated. Outputs, ranks and token counts were identical; timing varies. Per-query results, judgment hashes and source revisions are in [`benchmarks/ablation-results.json`](../benchmarks/ablation-results.json); the frozen baseline is in [`benchmarks/baseline-results.json`](../benchmarks/baseline-results.json); current public benchmark evidence is in [`benchmarks/results.json`](../benchmarks/results.json).

```bash
python -m pip install -e '.[benchmark]'
python -m pip install pytest
python -m pytest -q
python benchmarks/run.py
python benchmarks/ablation.py --output /tmp/ariadne-ablation.json
```

Source freshness checks repository HEAD, missing paths and scan age. Uncommitted edits require a forced rescan. Spring extraction handles literal mappings, including multiline/named arguments; dynamic constants and general Spring routing semantics remain outside its parser coverage. SDK dependencies retain the v1 MCP API (`mcp>=1,<2`). No new service or model dependency is introduced.
