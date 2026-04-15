# Ariadne

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-stdio-8A2BE2)](https://modelcontextprotocol.io)
[![Status](https://img.shields.io/badge/status-alpha-orange)](https://github.com/whyy9527/ariadne)

> Ariadne's thread — a way out of the microservice maze.

**Cross-service API dependency graph and semantic code navigation for microservice architectures.**
Zero-dependency Python 3.10 CLI; optional MCP server for AI coding assistants (Claude Code, Cursor, Windsurf).

Give it a business term or an endpoint name; it returns the most likely chain of GraphQL
operations, HTTP endpoints, Kafka topics, and frontend queries that participate in that
feature — across all your services at once.

Ariadne never modifies your repos. It is read-only static analysis built on
SQLite + TF-IDF + (optional) embeddings. The CLI has no external dependencies;
the MCP mode needs `pip install mcp`.

---

## Who is this for

- **Backend engineers** debugging a feature that spans 4+ microservices — find every
  endpoint, topic, and query involved without `grep`-ing each repo.
- **AI coding assistants** (Claude Code, Cursor) — attach Ariadne as an MCP server so
  the model gets a compact, structured view of your service dependency graph instead of
  raw grep output.
- **New team members** onboarding to a large microservice codebase — map any feature
  to its full API chain in seconds.
- **Code reviewers** doing cross-service impact analysis — see what else a change in
  one service might affect.

---

## Why

`grep` finds every *implementation* line that matches a token. Ariadne finds
the *interface layer*: the GraphQL mutation, the REST endpoint, the Kafka topic,
the frontend call. When you want to understand "what is involved in feature X
across N services", grep buries you in service / DTO / test files. Ariadne
gives you the API entry points, ranked, clustered, deduplicated.

For an AI assistant, the difference is dramatic: a query like `createOrder`
returns ~3 structured clusters (~500 tokens) instead of 40+ grep hits
(~2000 tokens), and the noise from implementation files is gone.

### Compared to other approaches

| Approach | What you get | Problem |
|---|---|---|
| `grep` / `rg` across repos | Every line mentioning the token | Drowns in DTOs, tests, configs |
| IDE "Find Usages" | Call sites within one service | Stops at service boundaries |
| Service mesh dashboards | Runtime traffic data | Needs production traffic; no feature mapping |
| Full AST / call-graph tools | Complete call graph | Slow to build; too much detail for feature navigation |
| **Ariadne** | Interface-layer API chains across services | Static analysis only; no runtime data |

Ariadne is intentionally narrow: it surfaces the *contract layer* (GraphQL, REST, Kafka,
frontend queries) and nothing else. That constraint is what makes results compact enough
for an AI context window.

---

## Example

```
$ python3 main.py query "createOrder"

Top Cluster #1  [confidence: 0.91]
  Services: gateway, orders-svc, billing-svc, web
  - [web]          Frontend Mutation: createOrder
  - [gateway]      GraphQL Mutation:  createOrder
  - [orders-svc]   HTTP POST /orders: createOrder
  - [orders-svc]   Kafka Topic:       order-created
  - [billing-svc]  Kafka Listener:    order-created → chargeCustomer

$ python3 main.py expand "order-created"

Source: [orders-svc] Kafka Topic: order-created
  → [billing-svc] Kafka Listener: chargeCustomer       (score=0.71)
  → [gateway]     GraphQL Subscription: orderUpdates    (score=0.62)
  → [web]         Frontend Subscription: OrderUpdates   (score=0.60)
```

---

## Quick start

```bash
# Python 3.10+
# CLI mode: no extra deps. MCP mode: pip install mcp

# 1. Describe your repos in a config file (see ariadne.config.example.json)
cp ariadne.config.example.json ariadne.config.json
$EDITOR ariadne.config.json

# 2. Scan
python3 main.py scan --config ariadne.config.json

# 3. Query
python3 main.py query "createOrder"
python3 main.py query "user profile"

# 4. Expand from a known node
python3 main.py expand "order-created"

# 5. Stats
python3 main.py stats
```

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
| `frontend_rest`    | `axiosRequest.<verb>(...)` and `fetch(...)` calls in TS files      |
| `cube`             | cube.js `cube(...)` definitions                                    |

---

## Using Ariadne with AI coding assistants

Ariadne has two integration modes. **CLI mode is the default** — it has zero external
dependencies and works with any AI tool that can run shell commands (Claude Code, Cursor,
Aider, Codex, Continue).

### Mode 1: CLI (recommended, zero deps)

Just let your AI assistant run the CLI via Bash. Drop this snippet into `CLAUDE.md`
(Claude Code), `.cursorrules` (Cursor), or equivalent:

```markdown
## Cross-service API navigation — Ariadne

When debugging or exploring a feature that spans multiple microservices, prefer
`python3 /abs/path/to/ariadne/main.py` over `grep`-ing individual repos:

- Find the full API chain for a feature:
  `python3 /abs/path/to/ariadne/main.py query "createOrder"`
- Expand from a known node (topic, endpoint, mutation):
  `python3 /abs/path/to/ariadne/main.py expand "order-created"`

Results are ranked clusters of GraphQL / REST / Kafka / frontend nodes — ~¼ the
tokens of a grep-based search.
```

No install, no server process, no MCP dependency. Just Python 3.10+.

### Mode 2: MCP server (optional, structured tool schema)

If you prefer native tool calls over shell commands, Ariadne also ships as a
[Model Context Protocol (MCP)](https://modelcontextprotocol.io) stdio server. This
exposes `query_chains`, `expand_node`, and `log_feedback` as first-class MCP tools
so the assistant sees them in its tool list automatically.

**One-shot setup:**

```bash
pip install mcp sentence-transformers
python3 main.py install --config ariadne.config.json
```

`install` scans your repos, builds `embeddings.db`, writes `.mcp.json` in the current
directory, and injects a usage snippet into `CLAUDE.md` — Claude Code picks it up
automatically on next launch.

**Manual setup:**

```bash
pip install mcp
python3 main.py scan --config ariadne.config.json   # build the DB once
python3 mcp_server.py                                # stdio MCP server
```

Claude Code config (`~/.claude.json` or project-level `.mcp.json`):

```json
{
  "mcpServers": {
    "ariadne": {
      "command": "python3",
      "args": ["/abs/path/to/ariadne/mcp_server.py"]
    }
  }
}
```

Tools exposed:

| Tool           | Args                                  | Purpose                                |
|----------------|---------------------------------------|----------------------------------------|
| `query_chains` | `hint`, `top_n` (default 3)           | Business term → cross-service clusters |
| `expand_node`  | `name` (partial match supported)      | One-hop neighbours of a known node     |
| `log_feedback` | `hint`, `accepted`, `node_ids`, ...   | Record whether results were useful     |

---

## FAQ

**Q: How do I find all services involved in a feature?**

Give Ariadne a business term or endpoint name:

```bash
python3 main.py query "checkout"
```

It returns a ranked list of clusters — each cluster is a set of GraphQL mutations,
REST endpoints, Kafka topics, and frontend queries that likely belong to that feature,
grouped by cross-service relationship.

---

**Q: How do I trace all consumers of a Kafka topic across services?**

Use `expand` with the topic name:

```bash
python3 main.py expand "order-created"
```

Returns one-hop neighbours — every `@KafkaListener`, downstream GraphQL subscription,
and frontend query that connects to that topic.

---

**Q: I want Claude / Cursor to understand my microservice architecture. How?**

Run `python3 main.py install` once. It registers Ariadne as an MCP server so Claude
Code and Cursor can call `query_chains` and `expand_node` tools mid-conversation —
they get back compact structured clusters instead of raw file grep results.

---

**Q: Does Ariadne require a running cluster or database?**

No. Pure static analysis. It reads your source files, indexes them into a local
SQLite database (`ariadne.db` + `embeddings.db`), and queries offline. No network
calls, no agents, no external services.

---

**Q: Which languages and frameworks are supported?**

Current scanners cover:
- **GraphQL** — `.graphql` / `.gql` SDL files
- **Java / Kotlin** — Spring `@RestController`, `@KafkaListener`, `application.yaml`, `RestClient`
- **TypeScript** — Apollo `gql\`\`` literals, `axiosRequest`, `fetch`
- **cube.js** — `cube(...)` model definitions

More scanners can be added by implementing the `BaseScanner` interface.

---

**Q: How is this different from just grepping across repos?**

`grep` returns every line that contains a token — service classes, DTOs, tests,
configs, comments. Ariadne only indexes the *interface layer*: GraphQL schema
definitions, REST controller routes, Kafka topic declarations, and frontend API
calls. A query that returns 40+ grep hits typically returns 3–5 structured
clusters in Ariadne, at ~¼ the token count.

---

**Q: Can I use this without an AI assistant — just as a CLI tool?**

Yes. The CLI (`python3 main.py query / expand / stats`) has zero dependencies
beyond Python 3.10. The `mcp` and `sentence-transformers` packages are only
needed for MCP mode and semantic (embedding) recall.

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
├── main.py                       # CLI
├── mcp_server.py                 # MCP stdio server
└── test_semantic_hint.py         # unit + integration + embedding tests
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

The factors are multiplicative, so `base = 0` always means `score = 0`. Service
and role only amplify real lexical overlap; they cannot fabricate a link.

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

TF-IDF is the primary recall channel. `bge-small-en-v1.5` is used for two
narrow jobs:

- **Recall fallback**: when token overlap is weak, find synonyms (e.g.
  `assignHomework` ↔ `assignStudentsToTask`) and add them to the anchor set.
- **Reranking**: build `top_n × 2` clusters first, then re-sort by
  `0.6 · confidence + 0.4 · max_cos(hint, cluster_nodes)` and truncate to
  `top_n`.

The model is ~130 MB and runs on CPU. Vectors are cached in `embeddings.db`;
only the query hint is embedded at query time.

---

## Tests

```bash
python3 test_semantic_hint.py
```

Covers normalizer, scoring, store, query/expand integration, embeddings, and
the feedback DB.

---

## Roadmap

- More Kafka sources (already covers `application.yaml` + `@KafkaListener` +
  `KafkaTemplate.send`)
- Wider TS scan (currently limited to files matching `service|api|hook|client|request`
  or `index.ts`)
- TF-IDF weight tuning for very high-frequency domain tokens
- Pair re-ranker trained on real usage feedback (only after we have enough
  `log_feedback` data)

### Non-goals

- LLM as the primary judge (slow, costly, non-reproducible)
- Visualization / graph database backend
- Full AST call-graph extraction

---

## License

MIT — see `LICENSE`.
