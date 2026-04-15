# Ariadne

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio-8A2BE2)](https://modelcontextprotocol.io)
[![Status](https://img.shields.io/badge/status-alpha-orange)](https://github.com/whyy9527/ariadne)
[![ariadne MCP server](https://glama.ai/mcp/servers/whyy9527/ariadne/badges/score.svg)](https://glama.ai/mcp/servers/whyy9527/ariadne)
[![Awesome MCP Servers](https://img.shields.io/badge/Awesome-MCP%20Servers-FC60A8?logo=awesomelists)](https://github.com/punkpeye/awesome-mcp-servers#-developer-tools)

> Ariadne's thread — a way out of the microservice maze.

**Cross-service API dependency graph and semantic code navigation for microservice architectures.**
MCP stdio server for AI coding assistants (Claude Code, Cursor, Windsurf), with a
CLI twin for scripting. Read-only static analysis on SQLite + TF-IDF + embeddings.

---

## Who is this for

- **AI coding assistants** (Claude Code, Cursor, Windsurf) — a structured cross-service
  dependency view sized for the context window, in place of raw `grep` output.
- **Backend engineers** tracing a feature across 4+ services — GraphQL, REST, Kafka,
  and frontend calls resolved in one query.
- **Platform and reviewers** doing cross-service impact analysis — surface the full
  call chain a change in one service touches before it ships.
- **Onboarding engineers** mapping an unfamiliar microservice topology from a single
  business term.

---

## Why

Ariadne indexes only the *contract layer* — GraphQL mutations, REST endpoints,
Kafka topics, frontend queries — nothing else. That narrowness is what makes
results fit an AI context window.

| Approach | Problem Ariadne solves |
|---|---|
| `grep` / `rg` across repos | Drowns in DTOs, tests, configs |
| IDE "Find Usages" | Stops at service boundaries |
| Service mesh dashboards | Needs production traffic; no feature mapping |
| Full AST / call-graph tools | Slow to build; too much detail |

---

## Example

You ask Claude "where does createOrder live across the stack?" Claude calls
`query_chains` mid-conversation and gets back:

```
Top Cluster #1  [confidence: 0.91]
  Services: gateway, orders-svc, billing-svc, web
  - [web]          Frontend Mutation: createOrder
  - [gateway]      GraphQL Mutation:  createOrder
  - [orders-svc]   HTTP POST /orders: createOrder
  - [orders-svc]   Kafka Topic:       order-created
  - [billing-svc]  Kafka Listener:    order-created → chargeCustomer
```

Claude then summarises: *"createOrder is a GraphQL mutation in `gateway`,
forwarded to `orders-svc` via `POST /orders`, which publishes an
`order-created` Kafka event that `billing-svc` consumes to charge the
customer."*

~500 tokens round-trip. The equivalent `grep -r createOrder` across four
repos would return 40+ matches across DTOs, tests, and configs at ~2000
tokens, with the contract layer buried.

---

## Golden path

The intended workflow when an AI assistant drives Ariadne via the MCP server.

```
Golden path — driving Ariadne from an AI conversation:

  1. query_chains(hint="createOrder")
       → ranked clusters of GraphQL / REST / Kafka / frontend nodes across
         services. Use this first to build cross-service context.

  2. expand_node(name="order-created")
       → one-hop neighbours of a specific node you want to trace.
         If called within 10 minutes of a matching query_chains, Ariadne
         automatically writes a positive feedback row — no extra call
         needed. The follow-up expand IS the signal.

  3. Read the files the returned clusters / neighbours point at.

  4. log_feedback(hint, accepted=False, ...) ONLY when a result was
     misleading. Most feedback is captured implicitly in step 2;
     log_feedback is the manual escape hatch for thumbs-down.

  Staleness: if query_chains or expand_node return a non-null
  `stale_warning` field, call rescan() once — it re-scans the repos
  listed in the install-time config, rebuilds embeddings if needed,
  and clears the warning. Then retry your original query.
```

---

## Quick start

Ariadne is an [MCP](https://modelcontextprotocol.io) stdio server. You run
`install` once, restart Claude Code, and the assistant picks up Ariadne's
tools automatically — no Bash wrapping, no shell calls.

```bash
# Python 3.10+

# 1. Describe your repos in a config file
cp ariadne.config.example.json ariadne.config.json
$EDITOR ariadne.config.json

# 2. One shot: scan + build embeddings + register as MCP server
pip install mcp onnxruntime tokenizers huggingface_hub
python3 main.py install ariadne.config.json ~/your-workspace

# 3. Restart Claude Code. Done.
```

`install` scans your repos, builds `<workspace>/.ariadne/` (DB, embeddings,
manifest), writes `<workspace>/.mcp.json`, and injects a usage snippet into
`<workspace>/CLAUDE.md`. It's idempotent — re-run it to rescan after pulling
new code (or let the assistant call `rescan` from inside the conversation
when it sees a `stale_warning`).

Flags: `--no-scan`, `--force`, `--snippet PATH`, `--marker STRING`.

### Tools the assistant sees

| Tool           | Args                                  | Purpose                                |
|----------------|---------------------------------------|----------------------------------------|
| `query_chains` | `hint`, `top_n` (default 3)           | Business term → cross-service clusters |
| `expand_node`  | `name` (partial match supported)      | One-hop neighbours of a known node     |
| `rescan`       | *(none)*                              | Refresh the index in place when a response has a `stale_warning`; git-hash incremental, returns `{nodes, duration_ms}` |
| `ariadne_help` | *(none)*                              | Setup guide + runtime config diagnostics (missing DB, empty index, stale scan) |
| `log_feedback` | `hint`, `accepted`, `node_ids`, ...   | Manual thumbs-down (positive feedback is implicit — see Feedback loop) |

### Config format

```json
{
  "repos": [
    {
      "name": "gateway",
      "path": "../gateway",
      "scanners": ["graphql"]
    },
    {
      "name": "orders-svc",
      "path": "../orders-svc",
      "scanners": [
        "http",
        "kafka",
        {
          "type": "backend_clients",
          "client_target_map": { "billing": "billing-svc", "user": "user-svc" }
        }
      ]
    },
    {
      "name": "web",
      "path": "../web",
      "scanners": [
        "frontend_graphql",
        {
          "type": "frontend_rest",
          "base_class_service": { "OrdersApiService": "orders-svc" }
        }
      ]
    }
  ]
}
```

Paths are resolved relative to the config file. Each repo lists one or more
scanners — either by name (string) or as an object with extra options.

### Available scanners

| Scanner            | Looks for                                                          |
|--------------------|--------------------------------------------------------------------|
| `graphql`          | `.graphql` / `.gql` SDL → Query / Mutation / Subscription / Type   |
| `http`             | Spring `@RestController` (Java/Kotlin) → HTTP endpoints            |
| `kafka`            | Spring `application.yaml` topics + `@KafkaListener` + producers    |
| `backend_clients`  | Spring `RestClient` / `RestTemplate` outbound calls in `*Client.*` |
| `frontend_graphql` | TypeScript `gql\`\`` literals → frontend Query/Mutation            |
| `frontend_rest`    | `axios`/`fetch` calls in TS/TSX files, excluding tests/mocks/types |
| `cube`             | cube.js `cube(...)` definitions                                    |

### Custom scanners

Any language or framework not covered above can be added without touching
Ariadne's source code. Implement `scanner.BaseScanner`, put the module
somewhere Python can import it, and reference the class by dotted path in
`ariadne.config.json`:

```json
{
  "name": "my-go-service",
  "path": "../my-go-service",
  "scanners": [
    {
      "type": "my_scanners.go_scanner:GoRouteScanner",
      "route_file": "cmd/server/routes.go"
    }
  ]
}
```

The value of `"type"` must be `"module.path:ClassName"` — a Python importable
module path, a colon, then the class name.  Every key besides `"type"` is
passed as a keyword argument to `__init__`.

```python
# my_scanners/go_scanner.py
from scanner import BaseScanner

class GoRouteScanner(BaseScanner):
    def __init__(self, route_file: str = "routes.go"):
        self.route_file = route_file

    def scan(self, repo_path: str, service: str) -> list[dict]:
        # ... parse repo_path/self.route_file ...
        return [
            {
                "id": f"{service}::http::GET::/ping",
                "type": "http_endpoint",
                "raw_name": "ping",
                "service": service,
                "source_file": self.route_file,
                "method": "GET",
                "path": "/ping",
                "fields": [],
            }
        ]
```

Zero install required — no entry points, no `pyproject.toml` changes.
Just make sure the module is importable from wherever you run `python3 main.py`.

---

## Feedback loop

Ariadne gradually adapts cluster ranking to your team's vocabulary, with no
model training or uploads. `feedback.db` is **local only**; each developer
starts cold and builds their own signal.

Every `query_chains` call caches returned clusters in memory for 10 minutes.
A follow-up `expand_node(name)` that substring-matches a node in one of those
pending clusters auto-writes an `accepted=True` row — the expand IS the signal,
no extra call needed. `log_feedback(hint, accepted, ...)` is the manual escape
hatch for thumbs-down or CLI usage; the `source` column distinguishes
`'implicit_expand'` from `'manual'`.

On the next `query()` for the same hint, prior accepted nodes are counted per
cluster and applied as:

```
final_score = confidence + 0.15 * sum(prior_accepted_count per node in cluster)
```

Weight (`0.15`) and decay window (`90 days`) are intentionally conservative —
lexical confidence still dominates. Clusters with no history are unaffected.
Disable with `export ARIADNE_FEEDBACK_BOOST=0`; JSON shape is unchanged either way.

---

## FAQ

**Does Ariadne require a running cluster, server, or network?**
No. Pure static analysis. Source → local SQLite (`ariadne.db`, `embeddings.db`,
`feedback.db`). No network calls, no uploads.

**How does it know when to re-scan?**
If the oldest scan is >7 days old, MCP responses include a `stale_warning`
field (CLI prints the same warning to stderr). From an AI conversation, call
`rescan()`; from the shell, `python3 main.py scan --config <path>`.

**Results feel generic at first — will they improve?**
Yes. `expand_node` follow-ups implicitly log positive feedback; the boost rerank
step (`confidence + 0.15 * boost`) promotes clusters that have been useful for
similar hints. Day-one results are pure lexical ranking; after a few weeks they
reflect your team's navigation patterns. Count-based, not a learned model.

**Which languages and frameworks are supported?**
GraphQL SDL; Java / Kotlin Spring (`@RestController`, `@KafkaListener`,
`application.yaml`, `RestClient`); TypeScript (`gql\`\``, `axios`, `fetch`);
cube.js. Add more via the `BaseScanner` interface (see *Custom scanners*).

**Can I use it without an AI assistant — just as a CLI?**
Yes. Every MCP tool has a CLI twin (`python3 main.py scan / query / expand / stats`)
with zero dependencies beyond Python 3.10. `mcp`, `onnxruntime`, `tokenizers`,
`huggingface_hub` are only needed for MCP mode and semantic recall fallback.
MCP is the recommended path.

---

## Architecture

```
ariadne/
├── scanner/
│   ├── graphql_scanner.py        # GraphQL SDL → Query/Mutation/Type
│   ├── http_scanner.py           # Spring @RestController → HTTP endpoints
│   ├── kafka_scanner.py          # application.yaml + @KafkaListener + producer
│   ├── frontend_scanner.py       # TS gql`` → Frontend Query/Mutation
│   ├── frontend_rest_scanner.py  # axios/fetch → Frontend REST calls
│   ├── backend_client_scanner.py # RestClient + pathSegment → outbound calls
│   └── cube_scanner.py           # cube.js model/*.js → analytics cubes
├── normalizer/
│   └── normalizer.py             # camelCase/snake/kebab → tokens
├── scoring/
│   ├── engine.py                 # IDF-weighted Jaccard + clustering
│   └── embedder.py               # bge-small recall fallback + reranker
├── store/
│   ├── db.py                     # SQLite: nodes / edges / token_idf
│   ├── embedding_db.py           # SQLite: node_id → float32 vector
│   └── feedback_db.py            # SQLite: usage feedback
├── query/
│   └── query.py                  # query / expand entry points
├── mcp_server.py                 # MCP stdio server (primary interface)
├── main.py                       # CLI (twin of MCP tools)
└── tests/                        # pytest suite (semantic_hint, feedback_boost, ...)
```

### Scoring (the short version)

The math is information retrieval, not graph theory. Node names are tokenized
(`createOrder` → `["create", "order"]`) and compared with IDF-weighted Jaccard:

```
idf_jaccard(A, B) = Σ idf(t)  (t ∈ A ∩ B)  /  Σ idf(t)  (t ∈ A ∪ B)
idf(t)           = log(N / df(t))
```

Rare tokens dominate; high-frequency domain words (`task`, `id`, `service`)
self-dampen, no stopword list needed.

```
base  = idf_jaccard(name) * 0.55 + idf_jaccard(fields) * 0.45
score = min(base * role_mult * service_mult, 1.0)

role_mult    = 1.3   for complementary pairs
                     (GraphQL Mutation ↔ Kafka topic ↔ HTTP POST,
                      GraphQL Query ↔ Cube Query ↔ HTTP GET)
service_mult = 1.25  cross-service / 0.8 same-service
```

### Clustering

Two-stage, `O(anchors × neighbours)`, independent of repo count.

1. Tokenize the hint, score against all nodes, keep the top 30 anchors with
   `score ≥ 0.15`.
2. For each anchor, pull its edges from the DB (single `IN` query) and keep
   the top 12 neighbours with `edge_score ≥ 0.25`.
3. Merge anchor neighbourhoods that overlap by ≥ 25%.
4. Per cluster, take top 2 nodes per `(service, type)`, capped at 12.
5. Confidence = mean edge score · 0.6 + type diversity · 0.2 + service
   diversity · 0.2.

### Embeddings

TF-IDF is the primary recall channel. `bge-small-en-v1.5` (ONNX int8 quantized)
is used for two narrow jobs:

- **Recall fallback**: when token overlap is weak, find synonyms (e.g.
  `assignHomework` ↔ `assignStudentsToTask`) and add them to the anchor set.
- **Reranking**: build `top_n × 2` clusters first, then re-sort by
  `0.6 · confidence + 0.4 · max_cos(hint, cluster_nodes)` and truncate to
  `top_n`.

The ONNX model is ~34 MB (int8 quantized) and runs on CPU via `onnxruntime`.
Cold start is ~0.3s (vs ~13s with the previous PyTorch-based implementation).
Vectors are cached in `embeddings.db`; only the query hint is embedded at query time.

---

## Tests

```bash
python3 tests/test_semantic_hint.py
python3 tests/test_feedback_boost.py
python3 tests/test_implicit_feedback.py
python3 tests/test_onnx_embedder.py
```

Covers normalizer, scoring, store, query/expand integration, embeddings,
feedback boost, implicit feedback, and the ONNX embedder. A pre-commit hook
at `hooks/pre-commit` runs `test_semantic_hint.py` — enable once per clone with:

```bash
ln -sf ../../hooks/pre-commit .git/hooks/pre-commit
```

---

## Roadmap

- More Kafka sources beyond `application.yaml` + `@KafkaListener` + `KafkaTemplate.send`
- TF-IDF weight tuning for very high-frequency domain tokens
- Stronger feedback signal: decay tuning, per-service weighting, cross-hint
  generalisation (current boost is count-based within the same hint)
- Watch mode: hook into git post-commit / file events to auto-trigger
  `rescan` instead of waiting for a stale_warning
- `expand_node` product polish: clearer trigger conditions, smaller input
  surface, output that points at the next step
- Parameter pass across all tools: task-oriented names over implementation
  names; unify verb prefixes for naming consistency

### Non-goals

- LLM as the primary judge (slow, costly, non-reproducible)
- Visualization / graph database backend
- Full AST call-graph extraction

---

## License

MIT — see `LICENSE`.
