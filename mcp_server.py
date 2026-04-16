#!/usr/bin/env python3
"""
Ariadne MCP Server

Exposes five tools to AI assistants (Claude Code, Cursor, etc.):
  - query_chains:  business term → cross-service chain clusters
  - expand_node:   node name → direct neighbors with scores
  - rate_result:   record whether results were useful (writes to feedback.db)
  - rescan:        refresh index from source repos
  - show_help:     setup and usage guide

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
from datetime import datetime, timezone

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


def _build_stale_warning(db) -> "str | None":
    """Return a stale-scan warning string if the oldest scan is older than _STALE_DAYS, else None."""
    try:
        oldest = db.get_oldest_scanned_at()
    except Exception:
        return None
    if oldest is None:
        return None
    now = datetime.now(timezone.utc)
    age_days = (now - oldest).days
    if age_days >= _STALE_DAYS:
        return (
            f"⚠ Oldest scan: {age_days} days ago. "
            "Re-scan: python3 main.py scan --config <path>"
        )
    return None


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


def _reset_db_cache() -> None:
    """
    Drop cached DB / embedding handles so the next tool call re-opens them
    against the freshly rescanned files. Feedback DB is unaffected by rescan,
    so it stays warm.
    """
    global _db, _edb
    _db = None
    _edb = None


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
                "One-hop neighbours of a known node (endpoint / Kafka topic / "
                "GraphQL operation / frontend call), with similarity scores and "
                "file paths. Read-only; no writes except an implicit positive "
                "feedback row if called within 10 min of a matching "
                "query_chains. Returns up to 3 matched source nodes × up to 10 "
                "neighbours (edges with score ≥ 0.08), plus a `stale_warning` "
                "field — call `rescan` if non-null.\n\n"
                "Use AFTER query_chains when you already have a concrete node "
                "name and want to trace one hop further. Use query_chains "
                "(not this) when starting from a business term or when you "
                "don't yet know a node name. Partial, case-insensitive match "
                "against node id and raw_name; ambiguous inputs return "
                "multiple source groups."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 2,
                        "description": (
                            "Node id or raw name (endpoint method, Kafka "
                            "topic, GraphQL operation, frontend call). "
                            "Case-insensitive substring match against both "
                            "id and raw_name. Prefer exact names copied from "
                            "a prior query_chains result to avoid ambiguity; "
                            "short strings (e.g. 'get') will match many "
                            "nodes and only the first 3 are expanded."
                        )
                    }
                },
                "required": ["name"]
            }
        ),
        Tool(
            name="show_help",
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
            name="rescan",
            description=(
                "Refresh the Ariadne index from inside the conversation. Call this "
                "when query_chains or expand_node returned a `stale_warning`, or "
                "after you know the user's code has changed. Re-scans every repo "
                "listed in the install-time ariadne.config.json, rebuilds embeddings "
                "if nodes changed, and invalidates cached DB handles so the next "
                "query sees fresh data. No arguments; zero configuration."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            }
        ),
        Tool(
            name="rate_result",
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
    if name == "show_help":
        return [TextContent(type="text", text=_build_help_text())]
    elif name == "query_chains":
        db = _get_db(_DB_PATH)
        edb = _get_edb(_EMB_PATH, db)
        return await _query_chains(db, edb, arguments)
    elif name == "expand_node":
        db = _get_db(_DB_PATH)
        return await _expand_node(db, arguments)
    elif name == "rate_result":
        fdb = _get_fdb(_FB_PATH)
        return await _rate_result(fdb, arguments)
    elif name == "rescan":
        return await _rescan()
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _rescan() -> list[TextContent]:
    """
    Refresh the index by re-running scan + embed against the install-time
    config, then drop cached DB handles. Reads config_path from the manifest
    written by `install`.
    """
    manifest_path = os.path.join(os.path.dirname(_DB_PATH), "manifest.json")
    if not os.path.isfile(manifest_path):
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": (
                    "No manifest at "
                    f"{manifest_path}. This index was built by an older install "
                    "that didn't persist the config path. Re-run "
                    "`python3 main.py install <config> <workspace>` from a shell "
                    "once to enable in-conversation rescan."
                )
            })
        )]

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
        config_path = manifest["config_path"]
    except (json.JSONDecodeError, KeyError) as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Manifest unreadable: {e}"})
        )]

    if not os.path.isfile(config_path):
        return [TextContent(
            type="text",
            text=json.dumps({
                "error": (
                    f"Config file moved or deleted: {config_path}. "
                    "Re-run `install` with the new path."
                )
            })
        )]

    import main as _main
    try:
        summary = _main.run_scan_and_embed(config_path, _DB_PATH, _EMB_PATH)
    except SystemExit as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Scan aborted (exit {e.code}); check config and repo paths."})
        )]
    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"error": f"Rescan failed: {type(e).__name__}: {e}"})
        )]

    _reset_db_cache()
    return [TextContent(type="text", text=json.dumps(summary))]


def _detect_config_issues() -> list[str]:
    """
    Runtime config/state sanity checks for show_help.

    Returns a list of short human-readable issue strings. Empty list means
    everything looks healthy. Never raises — a broken help tool is worse than
    a silent one.
    """
    issues: list[str] = []

    # DB file existence
    if not os.path.exists(_DB_PATH):
        issues.append(
            f"DB not found at {_DB_PATH}. Run "
            f"`python3 main.py scan --config ariadne.config.json` to build it."
        )
        return issues  # nothing else meaningful without a DB

    # DB contents
    try:
        db = _get_db(_DB_PATH)
        node_count = db.node_count()
        if node_count == 0:
            issues.append(
                "DB exists but contains 0 nodes. Scan probably ran against "
                "empty repos or with a config that matched no files. "
                "Check ariadne.config.json paths and scanner list."
            )
    except Exception as e:
        issues.append(f"DB open failed: {e}")
        return issues

    # Staleness
    try:
        stale = _build_stale_warning(db)
        if stale:
            issues.append(stale.lstrip("⚠ ").strip())
    except Exception:
        pass

    # Embeddings DB (optional, only a hint — not a hard failure)
    if not os.path.exists(_EMB_PATH):
        issues.append(
            f"embeddings.db missing at {_EMB_PATH}. Semantic recall fallback "
            "is disabled until the first query rebuilds it (~30s)."
        )
    else:
        try:
            from store.embedding_db import EmbeddingDB
            edb = EmbeddingDB(_EMB_PATH)
            if edb.count() == 0:
                issues.append("embeddings.db exists but is empty.")
        except Exception:
            pass

    return issues


_GOLDEN_PATH = """\
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

  4. rate_result(hint, accepted=False, ...) ONLY when a result was
     misleading. Most feedback is captured implicitly in step 2;
     rate_result is the manual escape hatch for thumbs-down.

  Staleness: if query_chains or expand_node return a non-null
  `stale_warning` field, call rescan() once — it re-scans the repos
  listed in the install-time config, rebuilds embeddings if needed,
  and clears the warning. Then retry your original query."""


_SCANNERS = """\
| Scanner            | Looks for                                                          |
|--------------------|--------------------------------------------------------------------|
| `graphql`          | `.graphql` / `.gql` SDL → Query / Mutation / Subscription / Type   |
| `http`             | Spring `@RestController` (Java/Kotlin) → HTTP endpoints            |
| `kafka`            | Spring `application.yaml` topics + `@KafkaListener` + producers    |
| `backend_clients`  | Spring `RestClient` / `RestTemplate` outbound calls in `*Client.*` |
| `frontend_graphql` | TypeScript `gql\\`\\`` literals → frontend Query/Mutation            |
| `frontend_rest`    | `axios`/`fetch` calls in TS/TSX files, excluding tests/mocks/types |
| `cube`             | cube.js `cube(...)` definitions                                    |"""


def _install_usage() -> str:
    """Install subcommand usage line, derived from main.build_parser()."""
    try:
        from main import build_parser
        parser = build_parser()
        install = parser._ariadne_subparsers["install"]
        install.prog = "python3 main.py install"
        usage = install.format_usage().strip()
        if usage.lower().startswith("usage:"):
            usage = usage[6:].strip()
        return " ".join(usage.split())
    except Exception:
        return "python3 main.py install <config> <workspace-dir> [flags]"


_HELP_TEMPLATE = """\
Ariadne — cross-service API dependency graph for microservice codebases.
{issues_block}
WHAT IT DOES
  Given a business term or endpoint name, returns the chain of GraphQL
  operations, HTTP endpoints, Kafka topics, and frontend queries that
  participate in that feature across all your services. For per-tool
  semantics, read each tool's own description in the MCP tool list —
  this message only covers workflow + setup + diagnostics.

{golden_path}

QUICK SETUP (for your own codebase)
  1. Create ariadne.config.json in your workspace:
       {{
         "repos": [
           {{ "name": "gateway",    "path": "../gateway",    "scanners": ["graphql"] }},
           {{ "name": "orders-svc", "path": "../orders-svc", "scanners": ["http", "kafka"] }},
           {{ "name": "web",        "path": "../web",        "scanners": ["frontend_graphql", "frontend_rest"] }}
         ]
       }}
  2. {install_usage}
     (e.g. workspace=~/Desktop/work — scans repos, builds <workspace>/.ariadne/,
      writes <workspace>/.mcp.json, injects <workspace>/CLAUDE.md snippet)
  3. Restart Claude Code — ariadne shows up as an MCP server.

SUPPORTED SCANNERS
{scanners}

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


def _build_help_text() -> str:
    issues = _detect_config_issues()
    if issues:
        lines = ["", "⚠ DETECTED ISSUES"]
        for i, msg in enumerate(issues, 1):
            lines.append(f"  {i}. {msg}")
        lines.append("")
        issues_block = "\n".join(lines)
    else:
        issues_block = ""
    return _HELP_TEMPLATE.format(
        issues_block=issues_block,
        install_usage=_install_usage(),
        golden_path=_GOLDEN_PATH,
        scanners=_SCANNERS,
    )


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

    stale_warning = _build_stale_warning(db)

    if not results:
        payload = {"chains": [], "stale_warning": stale_warning}
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    # Cache this query for potential implicit feedback from a follow-up expand_node
    clusters = _extract_cluster_node_names(results)
    if clusters:
        _PendingQueries.append({
            "hint": hint,
            "ts": time.time(),
            "clusters": clusters,
        })

    payload = {"chains": results, "stale_warning": stale_warning}
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


async def _expand_node(db, arguments: dict) -> list[TextContent]:
    from query.query import expand

    name = arguments["name"]
    results = expand(db, name)

    stale_warning = _build_stale_warning(db)

    if not results:
        payload = {
            "neighbors": [],
            "stale_warning": stale_warning,
            "next_step": "No matches. Try an exact node name from a query_chains result, or call query_chains with a broader business term.",
        }
        return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

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

    # Build next-step guidance based on state
    if stale_warning:
        next_step = "Index is stale — call rescan() first, then retry."
    else:
        next_step = (
            "Read the source files listed in the neighbour nodes. "
            "If results were misleading, call rate_result(hint=<name>, accepted=false)."
        )

    payload = {"neighbors": results, "stale_warning": stale_warning, "next_step": next_step}
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]


async def _rate_result(fdb, arguments: dict) -> list[TextContent]:
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
