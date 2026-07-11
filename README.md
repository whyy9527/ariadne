# Ariadne

<!-- mcp-name: io.github.whyy9527/ariadne -->

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-8A2BE2)](https://modelcontextprotocol.io)
[![ariadne MCP server](https://glama.ai/mcp/servers/whyy9527/ariadne/badges/score.svg)](https://glama.ai/mcp/servers/whyy9527/ariadne)
[![Awesome MCP Servers](https://img.shields.io/badge/Awesome-MCP%20Servers-FC60A8?logo=awesomelists)](https://github.com/punkpeye/awesome-mcp-servers#-developer-tools)

> Ariadne's thread — a way out of the microservice maze.

Cross-service API dependency graph for Spring Boot + TypeScript
microservice stacks. MCP stdio server for AI coding assistants
(Claude Code, Cursor, Windsurf), with a CLI twin. Local SQLite + TF-IDF.
Zero ML dependencies.

![Ariadne demo — scan Spring PetClinic microservices and ask "owner"](docs/demo.gif)

<sub>70-second deterministic terminal walkthrough. Reproduce it from
[`docs/demo.tape`](docs/demo.tape).</sub>

---

## What it does

Indexes the *contract layer* — GraphQL mutations, REST endpoints, Kafka
topics, frontend queries. Nothing else. That's why results fit an AI
context window.

Ask Claude *"where does createOrder live across the stack?"* and
`query_chains` returns:

```
Top Cluster #1  [confidence: 0.91]
  Services: gateway, orders-svc, billing-svc, web
  - [web]          Frontend Mutation: createOrder
  - [gateway]      GraphQL Mutation:  createOrder
  - [orders-svc]   HTTP POST /orders: createOrder
  - [orders-svc]   Kafka Topic:       order-created
  - [billing-svc]  Kafka Listener:    order-created → chargeCustomer
```

The response is intentionally bounded for an AI context window. See the
[reproducible public-stack benchmark](BENCHMARKS.md) for measured retrieval,
serialized token, and timing results against `rg` and `grep`.

Current public-stack benchmark (48 reviewed queries across Spring REST,
GraphQL/TypeScript, Kafka, and FastAPI):

| Backend | Top-1 | Top-3 | MRR | Warm query | Mean output |
|---|---:|---:|---:|---:|---:|
| Ariadne | 64.6% | 70.8% | 0.677 | <0.3 ms | 157 tokens |
| `rg` | 37.5% | 56.2% | 0.510 | ~9 ms | 591 tokens |
| `grep` | 37.5% | 56.2% | 0.510 | ~9 ms | 591 tokens |

[Full methodology and per-stack results](BENCHMARKS.md) ·
[raw JSON evidence](benchmarks/results.json)

This corpus is operation-name-heavy and measures deterministic contract lookup
compatibility. It is not yet a natural-language relevance benchmark.

Supports: GraphQL · Spring HTTP/Kafka/RestClient · Python FastAPI · TypeScript
Apollo/fetch/axios · Cube.js.

---

## Try it in 30 seconds (zero config)

```bash
pip install ariadne-mcp
ariadne-mcp demo
```

Clones [`spring-petclinic-microservices`][petclinic] into
`~/.cache/ariadne-mcp/demo`, scans it, and prints the top cluster for
`owner` — a real cross-service call chain. No config file, no workspace
setup.

Did Ariadne find the chain you expected? Share one minute of
[structured feedback][feedback-form]. Ariadne sends no usage data automatically;
the form opens only when you choose to submit it.

[petclinic]: https://github.com/spring-petclinic/spring-petclinic-microservices
[feedback-form]: https://github.com/whyy9527/ariadne/issues/new?template=usage-feedback.yml

---

## Install on your own workspace

```bash
pip install ariadne-mcp
cp "$(python -c 'import ariadne_mcp, os; print(os.path.join(os.path.dirname(ariadne_mcp.__file__), "ariadne.config.example.json"))')" ariadne.config.json
# edit ariadne.config.json (list the repos you want indexed)
ariadne-mcp install ariadne.config.json ~/your-workspace
```

Restart Claude Code. `install` is idempotent — re-run after pulling new
code, or let the assistant call `rescan` on a `stale_warning`.

After your first real query, you can optionally send
[closed-ended usage feedback][feedback-form]. No source, query, or usage data is
transmitted by Ariadne itself.

---

## Config

```json
{ "repos": [
    { "path": "../gateway" },
    { "path": "../orders-svc" },
    { "path": "../web" }
]}
```

Scanners are inferred from each repo's top-level files
(`pom.xml` / `build.gradle` / `package.json` / SDL). See
[`docs/CONFIG.md`](docs/CONFIG.md) for the detection table and override
syntax.

---

## Reproducible public samples

Each sample pins an upstream commit, scans real service source, runs one query,
and verifies manually reviewed node IDs:

| Example | Contract path |
|---|---|
| [`spring-petclinic`](examples/spring-petclinic/) | Spring REST gateway → service |
| [`one-platform`](examples/one-platform/) | GraphQL/TypeScript services |
| [`kafka-microservices`](examples/kafka-microservices/) | Kafka producer → consumer |
| [`fastapi-microservices`](examples/fastapi-microservices/) | Python FastAPI routes |

Run one from a source checkout:

```bash
python examples/run.py kafka-microservices
```

---

## Evaluate ranking

Keep a JSONL judgment list for queries that matter to your workspace:

```jsonl
{"hint":"createOrder","expected_node_ids":["gateway::gql::m::createOrder"],"k":3}
{"hint":"owner","expected_node_ids":["customers::http::GET /owners/{ownerId}"],"match":"any","k":5}
```

Run it against a built DB:

```bash
ariadne-mcp --db .ariadne/ariadne.db eval eval/queries.jsonl --top 3 --min-hit-rate 0.8
```

The command evaluates top-k hit rate and MRR using a stable internal candidate
depth, and exits non-zero when a configured threshold fails. Add
`--feedback-db .ariadne/feedback.db` to include local feedback reranking in the
eval.

---

<sub>Architecture, MCP tools, scoring math, feedback boost →
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Custom scanners (Go,
Rust, anything) → [`docs/CUSTOM_SCANNERS.md`](docs/CUSTOM_SCANNERS.md).
Maintainer adoption snapshots → [`docs/ADOPTION_METRICS.md`](docs/ADOPTION_METRICS.md).</sub>
