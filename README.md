# Ariadne

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
[reproducible Petclinic benchmark](BENCHMARKS.md) for measured retrieval,
serialized token, and timing results against `rg` and `grep`.

Supports: GraphQL · Spring HTTP/Kafka/RestClient · TypeScript
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

[petclinic]: https://github.com/spring-petclinic/spring-petclinic-microservices

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
Rust, anything) → [`docs/CUSTOM_SCANNERS.md`](docs/CUSTOM_SCANNERS.md).</sub>
