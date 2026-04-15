#!/usr/bin/env python3
"""
Ariadne MCP Server

Exposes three tools to AI assistants (Claude Code, Cursor, etc.):
  - query_chains:  business term → cross-service chain clusters
  - expand_node:   node name → direct neighbors with scores
  - log_feedback:  record whether results were useful (writes to feedback.db)

Usage (stdio transport):
  python3 mcp_server.py [--db PATH] [--fb PATH]

Claude Code config (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "ariadne": {
        "command": "python3",
        "args": ["/abs/path/to/mcp_server.py"],
        "env": {}
      }
    }
  }
"""
import asyncio
import json
import os
import sys
import argparse
import time
from collections import deque

# Resolve DB path before changing directory
_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_DIR, "ariadne.db")
DEFAULT_FB = os.path.join(_DIR, "feedback.db")
DEFAULT_EMB = os.path.join(_DIR, "embeddings.db")

sys.path.insert(0, _DIR)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# ── DB bootstrap ───────────────────────────────────────────────────────────────

_STALE_DAYS = 7


def _ensure_db(db_path: str):
    """Warn if the local DB is missing or stale. No remote fetch."""
    import time

    path = os.path.abspath(db_path)

    if not os.path.exists(path):
        print(
            f"[ariadne] DB not found at {path}. "
            "Run 'python3 main.py scan --config ariadne.config.json' to build it.",
            file=sys.stderr,
        )
        return

    age_days = (time.time() - os.path.getmtime(path)) / 86400
    if age_days > _STALE_DAYS:
        print(
            f"[ariadne] WARNING: DB is {age_days:.0f} days old (>{_STALE_DAYS}). "
            "Consider re-running 'python3 main.py scan'.",
            file=sys.stderr,
        )

app = Server("ariadne")

# DB handles — initialised once at startup
_db = None
_fdb = None
_edb = None

# ── Implicit feedback: pending query cache ─────────────────────────────────────
# Each entry: {"hint": str, "ts": float, "clusters": [{"rank": int, "node_names": set}]}
_PENDING_TTL = 600          # 10 min — queries older than this are silently dropped
_PENDING_MAX = 20           # max entries; oldest evicted when cap reached
_PendingQueries: deque = deque(maxlen=_PENDING_MAX)


def _get_db(db_path: str):
    global _db
    if _db is None:
        from store.db import DB
        _db = DB(db_path)
        idf = _db.get_token_idf()
        if idf:
            from scoring.engine import set_idf
            set_idf(idf)
    return _db


def _get_fdb(fb_path: str):
    global _fdb
    if _fdb is None:
        from store.feedback_db import FeedbackDB
        _fdb = FeedbackDB(fb_path)
    return _fdb


def _get_edb(emb_path: str, db):
    """Lazy-load EmbeddingDB. Auto-builds if missing or stale (node count changed)."""
    global _edb
    if _edb is None:
        from store.embedding_db import EmbeddingDB
        _edb = EmbeddingDB(emb_path)

    node_count = db.node_count()
    if _edb.is_stale(node_count):
        print(f"[ariadne] Embeddings stale (have {_edb.count()}, need {node_count}). "
              "Building... (first time may take ~30s)", file=sys.stderr)
        from scoring.embedder import build_embeddings
        all_nodes = db.get_all_nodes()
        n = build_embeddings(all_nodes, _edb)
        print(f"[ariadne] Built {n} embeddings.", file=sys.stderr)

    return _edb


# ── Tool declarations ──────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_chains",
            description=(
                "Query cross-service chains by business term or endpoint name. "
                "Returns candidate clusters of related GraphQL operations, HTTP endpoints, "
                "Kafka topics, and frontend queries across all services indexed by the "
                "local Ariadne DB. Use this when you need to understand which APIs, "
                "topics, or frontend operations are involved in a business feature."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hint": {
                        "type": "string",
                        "description": "Business term or endpoint name (e.g. 'createOrder', 'userProfile', 'subscription')"
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of clusters to return (default 3)",
                        "default": 3
                    }
                },
                "required": ["hint"]
            }
        ),
        Tool(
            name="expand_node",
            description=(
                "Expand from a specific node (endpoint, topic, or operation) to see "
                "its directly related nodes with similarity scores. Use this to trace "
                "one hop of the cross-service chain from a known starting point."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Endpoint method name, Kafka topic name, or GraphQL operation name (partial match supported)"
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="ariadne_help",
            description=(
                "Return a quick setup and usage guide for Ariadne. Call this first "
                "when you are unsure how to use Ariadne, how to index your own "
                "microservices, or why query_chains returned no results. Always "
                "safe to call — no DB required."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            }
        ),
        Tool(
            name="log_feedback",
            description=(
                "Record whether Ariadne results were useful. Call this after using "
                "query_chains or expand_node to log feedback for future improvement. "
                "Feedback is stored locally in feedback.db and survives DB rebuilds."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "hint": {
                        "type": "string",
                        "description": "The hint used in query_chains or the node name used in expand_node"
                    },
                    "cluster_rank": {
                        "type": "integer",
                        "description": "Which cluster was referenced (1-based). Use 0 for expand_node results.",
                        "default": 1
                    },
                    "node_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Node IDs from the result that were actually useful"
                    },
                    "accepted": {
                        "type": "boolean",
                        "description": "true if results helped locate files or understand the chain; false if irrelevant or misleading"
                    }
                },
                "required": ["hint", "accepted"]
            }
        ),
    ]


# ── Tool implementations ───────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "ariadne_help":
        return [TextContent(type="text", text=_HELP_TEXT)]
    elif name == "query_chains":
        db = _get_db(_DB_PATH)
        edb = _get_edb(_EMB_PATH, db)
        return await _query_chains(db, edb, arguments)
    elif name == "expand_node":
        db = _get_db(_DB_PATH)
        return await _expand_node(db, arguments)
    elif name == "log_feedback":
        fdb = _get_fdb(_FB_PATH)
        return await _log_feedback(fdb, arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


_HELP_TEXT = """\
Ariadne — cross-service API dependency graph for microservice codebases.

WHAT IT DOES
  Given a business term or endpoint name, returns the chain of GraphQL
  operations, HTTP endpoints, Kafka topics, and frontend queries that
  participate in that feature across all your services.

TOOLS
  query_chains(hint, top_n)  — Business term → ranked cross-service clusters.
                               Best for: "what does createOrder involve?"
  expand_node(name)          — Known node → direct neighbours with scores.
                               Best for: "what listens to order-created?"
  log_feedback(hint, ...)    — Record whether results were useful.
  ariadne_help()             — This message.

QUICK SETUP (for your own codebase)
  1. Create ariadne.config.json in your workspace:
       {
         "repos": [
           { "name": "gateway",    "path": "../gateway",    "scanners": ["graphql"] },
           { "name": "orders-svc", "path": "../orders-svc", "scanners": ["http", "kafka"] },
           { "name": "web",        "path": "../web",        "scanners": ["frontend_graphql", "frontend_rest"] }
         ]
       }
  2. python3 main.py install --config ariadne.config.json
     (scans repos, builds DB, writes .mcp.json, injects CLAUDE.md snippet)
  3. Restart Claude Code — ariadne shows up as an MCP server.

SUPPORTED SCANNERS
  graphql            .graphql / .gql SDL → Query / Mutation / Subscription
  http               Spring @RestController (Java/Kotlin) → HTTP endpoints
  kafka              application.yaml + @KafkaListener + KafkaTemplate.send
  backend_clients    Spring RestClient / RestTemplate outbound calls
  frontend_graphql   TypeScript gql`` literals → frontend Query/Mutation
  frontend_rest      axiosRequest.<verb>(...) and fetch(...) calls
  cube               cube.js cube(...) model definitions

WHY RESULTS MAY BE EMPTY
  - DB not built yet — run `python3 main.py scan --config ariadne.config.json`
  - The hosted demo DB only contains a small fictional microservice stack
    (orders-svc, billing-svc, users-svc, gateway, web). Try hints like
    "createOrder", "userProfile", "order-created", "refundPayment".
  - Your hint uses tokens not present in any node name or field.
    Try a broader business term or an exact endpoint name.

MORE INFO
  Repo:  https://github.com/whyy9527/ariadne
  Docs:  README.md sections "Quick start", "Available scanners", "FAQ"
"""


def _extract_cluster_node_names(results: list[dict]) -> list[dict]:
    """
    Convert query() result list into a compact structure for pending cache.
    Returns [{"rank": 1, "node_names": {name, ...}}, ...]
    Each cluster captures both the node "name" (raw_name) and "id" fields so
    expand_node partial-name matching works correctly.
    """
    clusters = []
    for i, cluster in enumerate(results, 1):
        names = set()
        for node in cluster.get("nodes", []):
            if node.get("name"):
                names.add(node["name"].lower())
            if node.get("id"):
                names.add(node["id"].lower())
        clusters.append({"rank": i, "node_names": names})
    return clusters


async def _query_chains(db, edb, arguments: dict) -> list[TextContent]:
    from query.query import query

    hint = arguments["hint"]
    top_n = int(arguments.get("top_n", 3))
    fdb = _get_fdb(_FB_PATH)

    results = query(db, hint, top_n=top_n, edb=edb, fdb=fdb)

    if not results:
        return [TextContent(type="text", text=f"No chains found for: {hint}")]

    # Cache this query for potential implicit feedback from a follow-up expand_node
    clusters = _extract_cluster_node_names(results)
    if clusters:
        _PendingQueries.append({
            "hint": hint,
            "ts": time.time(),
            "clusters": clusters,
        })

    return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False))]


async def _expand_node(db, arguments: dict) -> list[TextContent]:
    from query.query import expand

    name = arguments["name"]
    results = expand(db, name)

    if not results:
        return [TextContent(type="text", text=f"No node found matching: {name}")]

    # Implicit feedback: if this expand_node name matches a node from a recent
    # query_chains result, treat it as positive feedback for that cluster.
    name_lower = name.lower()
    now = time.time()
    matched_hints = []
    for entry in list(_PendingQueries):
        if now - entry["ts"] > _PENDING_TTL:
            continue  # silently skip expired entries (no negative feedback)
        for cluster in entry["clusters"]:
            # Substring match mirrors expand()'s own partial name logic
            if any(name_lower in n or n in name_lower for n in cluster["node_names"]):
                matched_hints.append((entry, cluster["rank"]))
                break  # one cluster match per pending query is enough

    if matched_hints:
        fdb = _get_fdb(_FB_PATH)
        for entry, rank in matched_hints:
            try:
                fdb.log(
                    hint=entry["hint"],
                    cluster_rank=rank,
                    node_ids=[],
                    accepted=True,
                    source="implicit_expand",
                )
            except Exception as e:
                print(f"[ariadne] implicit feedback write failed: {e}", file=sys.stderr)
            # Remove from pending to avoid double-counting
            try:
                _PendingQueries.remove(entry)
            except ValueError:
                pass  # already removed (race within same process is impossible in asyncio, but be safe)

    return [TextContent(type="text", text=json.dumps(results, ensure_ascii=False))]


async def _log_feedback(fdb, arguments: dict) -> list[TextContent]:
    hint = arguments["hint"]
    cluster_rank = int(arguments.get("cluster_rank", 1))
    node_ids = arguments.get("node_ids", [])
    accepted = bool(arguments["accepted"])

    fdb.log(hint=hint, cluster_rank=cluster_rank, node_ids=node_ids, accepted=accepted)
    total = fdb.count()

    return [TextContent(type="text", text=json.dumps({
        "logged": True,
        "hint": hint,
        "accepted": accepted,
        "total_feedback": total,
    }))]


# ── Entry point ────────────────────────────────────────────────────────────────

_DB_PATH = DEFAULT_DB
_FB_PATH = DEFAULT_FB
_EMB_PATH = DEFAULT_EMB


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ariadne MCP Server")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB path")
    parser.add_argument("--fb", default=DEFAULT_FB, help="Feedback DB path")
    parser.add_argument("--emb", default=DEFAULT_EMB, help="Embeddings DB path")
    args = parser.parse_args()
    _DB_PATH = args.db
    _FB_PATH = args.fb
    _EMB_PATH = args.emb

    _ensure_db(_DB_PATH)
    asyncio.run(main())
