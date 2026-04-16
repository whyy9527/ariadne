#!/usr/bin/env python3
"""
ariadne: local non-invasive cross-service chain hinter.

Usage:
  python main.py scan     [--config PATH] [--db PATH]
  python main.py query    <hint>  [--db PATH] [--top N]
  python main.py expand   <name>  [--db PATH]
  python main.py stats    [--db PATH]
  python main.py install  <config> <workspace> [--snippet PATH] [--no-scan]

Scan is config-driven. Pass --config PATH (default: ariadne.config.json in the
current working directory). See ariadne.config.example.json for the format.
"""
import argparse
import json
import sys
import os
import subprocess
from datetime import datetime, timezone

DEFAULT_DB = os.path.join(os.path.dirname(__file__), "ariadne.db")
DEFAULT_CONFIG = "ariadne.config.json"

STALE_SCAN_DAYS = 7  # must match store/db.STALE_SCAN_DAYS


def _stale_warning(db, args=None) -> None:
    """Print a warning to stderr if the oldest scan is older than STALE_SCAN_DAYS."""
    from store.db import STALE_SCAN_DAYS as _THRESHOLD
    oldest = db.get_oldest_scanned_at()
    if oldest is None:
        return
    now = datetime.now(timezone.utc)
    age_days = (now - oldest).days
    if age_days >= _THRESHOLD:
        config_hint = ""
        if args is not None and hasattr(args, "config"):
            config_hint = f" --config {args.config}"
        use_color = sys.stderr.isatty()
        prefix = "\033[33m" if use_color else ""
        suffix = "\033[0m" if use_color else ""
        print(
            f"{prefix}⚠ Oldest scan: {age_days} days ago. "
            f"Re-scan: python3 main.py scan{config_hint}{suffix}",
            file=sys.stderr,
        )


def _get_scanner_registry():
    """Return the built-in scanner registry (name → class).

    Imported lazily so scanner modules are only loaded when needed.
    """
    from scanner.graphql_scanner import GraphQLScanner
    from scanner.http_scanner import HTTPScanner
    from scanner.kafka_scanner import KafkaScanner
    from scanner.frontend_scanner import FrontendGraphQLScanner
    from scanner.frontend_rest_scanner import FrontendRESTScanner
    from scanner.backend_client_scanner import BackendClientScanner
    from scanner.cube_scanner import CubeScanner
    return {
        "graphql": GraphQLScanner,
        "http": HTTPScanner,
        "kafka": KafkaScanner,
        "frontend_graphql": FrontendGraphQLScanner,
        "frontend_rest": FrontendRESTScanner,
        "backend_clients": BackendClientScanner,
        "cube": CubeScanner,
    }


# Module-level registry (populated on first access via helper above).
# Kept as a module attribute so existing code that does
# ``from main import SCANNER_REGISTRY`` or ``SCANNER_REGISTRY.get(...)``
# continues to work — but we populate it lazily the first time it is needed
# to avoid import-time side effects during tests.
SCANNER_REGISTRY: dict = {}


def _resolve_scanner(sc_name: str, sc_opts: dict):
    """Return ``(bound_scan_method, is_class_based)`` where *is_class_based* is
    always ``True`` — every scanner is now a ``BaseScanner`` subclass.

    Resolution order:
    1. Built-in name (e.g. ``"graphql"``) → look up SCANNER_REGISTRY (class),
       instantiate with *sc_opts* as kwargs, return bound ``scan`` method.
    2. Dotted-path class reference (``"module.path:ClassName"``) not in the
       built-in registry → dynamic-import the class via importlib, instantiate
       with *sc_opts* as kwargs, return bound ``scan`` method.

    Raises ``ValueError`` for unknown names / malformed specs.
    """
    import importlib

    # Ensure SCANNER_REGISTRY is populated.
    global SCANNER_REGISTRY
    if not SCANNER_REGISTRY:
        SCANNER_REGISTRY.update(_get_scanner_registry())

    # --- 1. Built-in name ---
    cls = SCANNER_REGISTRY.get(sc_name)
    if cls is not None:
        instance = cls(**sc_opts)
        return instance.scan, True

    # --- 2. Dotted-path class reference ---
    if ":" in sc_name:
        mod_name, cls_name = sc_name.rsplit(":", 1)
        try:
            mod = importlib.import_module(mod_name)
        except ModuleNotFoundError as exc:
            raise ValueError(
                f"Cannot import module '{mod_name}' for scanner '{sc_name}': {exc}"
            ) from exc
        cls = getattr(mod, cls_name, None)
        if cls is None:
            raise ValueError(
                f"Module '{mod_name}' has no attribute '{cls_name}'"
            )
        instance = cls(**sc_opts)
        return instance.scan, True

    raise ValueError(
        f"Unknown scanner '{sc_name}'. "
        "Use a built-in name or a dotted-path class reference "
        "(e.g. 'my_pkg.my_scanner:MyScanner')."
    )


def _load_config(path: str) -> dict:
    if not os.path.exists(path):
        print(
            f"ERROR: config not found at {path}\n"
            f"Create one (see ariadne.config.example.json) or pass --config PATH.",
            file=sys.stderr,
        )
        sys.exit(1)
    with open(path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict) or "repos" not in cfg:
        print("ERROR: config must be a JSON object with a 'repos' array.", file=sys.stderr)
        sys.exit(1)
    return cfg


def _resolve_path(base: str, p: str) -> str:
    p = os.path.expanduser(p)
    if os.path.isabs(p):
        return p
    return os.path.abspath(os.path.join(base, p))


def _git_head_hash(repo_path: str) -> str | None:
    """Return current HEAD commit hash, or None if not a git repo / git missing."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def cmd_scan(args):
    from normalizer.normalizer import normalize
    from store.db import DB
    from scoring.engine import compute_idf, set_idf, score_all_pairs

    cfg_path = os.path.abspath(args.config)
    cfg = _load_config(cfg_path)
    cfg_dir = os.path.dirname(cfg_path)

    db = DB(args.db)
    force = getattr(args, "force", False)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    repos = cfg.get("repos", [])
    if not repos:
        print("ERROR: config has no repos.", file=sys.stderr)
        sys.exit(1)

    mode = "FULL rescan" if force else "incremental"
    print(f"[1/4] Scanning {len(repos)} repos ({mode}; config: {cfg_path}) ...")

    enriched: list[dict] = []
    any_rescanned = False

    for entry in repos:
        name = entry["name"]
        path = _resolve_path(cfg_dir, entry["path"])
        scanners = entry.get("scanners", [])
        if not os.path.isdir(path):
            print(f"  {name}: SKIP (not found at {path})")
            continue

        cur_hash = _git_head_hash(path)
        prev = db.get_repo_state(name)
        prev_hash = prev["git_hash"] if prev else None
        # Skip only when git tracking works on both sides, hashes match,
        # and the repo already has nodes in the DB.
        existing_nodes = db.get_nodes_by_service(name)
        reusable = (
            not force
            and cur_hash is not None
            and prev_hash is not None
            and cur_hash == prev_hash
            and len(existing_nodes) > 0
        )

        if reusable:
            # Reuse existing nodes without re-running scanners.
            for node in existing_nodes:
                # Existing rows are already normalized; tokens/field_tokens
                # come back as lists thanks to _row_to_dict.
                node.setdefault("tokens", [])
                node.setdefault("field_tokens", [])
                enriched.append(node)
            print(f"  {name}: REUSE {len(existing_nodes)} nodes (HEAD {cur_hash[:8]} unchanged)")
            continue

        # Re-scan: drop stale nodes for this service first so removed
        # endpoints / topics actually disappear from the DB.
        removed = db.delete_nodes_by_service(name)
        any_rescanned = True

        counts = []
        repo_new_nodes: list[dict] = []
        for sc in scanners:
            if isinstance(sc, str):
                sc_name, sc_opts = sc, {}
            else:
                sc_name = sc.get("type")
                sc_opts = {k: v for k, v in sc.items() if k != "type"}
            try:
                fn, _ = _resolve_scanner(sc_name, sc_opts)
            except ValueError as exc:
                print(f"  {name}: WARN {exc}", file=sys.stderr)
                continue
            # All scanners are now class-based: opts consumed by __init__,
            # scan(repo_path, service) takes no extra kwargs.
            nodes = fn(path, name)
            repo_new_nodes.extend(nodes)
            counts.append(f"{sc_name}={len(nodes)}")

        # Normalize + upsert immediately so repo state reflects reality
        # even if a later repo fails.
        for node in repo_new_nodes:
            norm = normalize(node["raw_name"], node.get("fields", []))
            node["tokens"] = norm["tokens"]
            node["field_tokens"] = norm["field_tokens"]
            db.upsert_node(node, norm["tokens"], norm["field_tokens"])
            enriched.append(node)

        db.upsert_repo_state(name, cur_hash, now)
        db.commit()

        hash_tag = cur_hash[:8] if cur_hash else "no-git"
        prev_tag = f" (was {prev_hash[:8]})" if prev_hash and cur_hash and prev_hash != cur_hash else ""
        drop_tag = f" [dropped {removed}]" if removed else ""
        summary = ", ".join(counts) if counts else "no scanners"
        print(f"  {name}: RESCAN [{hash_tag}]{prev_tag} {summary}{drop_tag}")

    if not enriched:
        print("ERROR: no nodes in DB after scan. Check config paths and scanner types.", file=sys.stderr)
        sys.exit(1)

    if not any_rescanned:
        print(f"\n[2/4] All repos unchanged — skipping normalize/IDF/scoring.")
        print(f"Done. DB: {args.db}")
        print(f"  Nodes: {db.node_count()}, Edges: {db.edge_count()}")
        return

    print(f"\n[2/4] Normalizing — {len(enriched)} nodes total")
    # Re-normalize reused nodes too so IDF sees consistent token lists.
    for node in enriched:
        if not node.get("tokens") or not node.get("field_tokens"):
            norm = normalize(node["raw_name"], node.get("fields", []))
            node["tokens"] = norm["tokens"]
            node["field_tokens"] = norm["field_tokens"]

    print("[3/4] Computing TF-IDF weights...")
    idf = compute_idf(enriched)
    db.upsert_token_idf(idf)
    db.commit()
    set_idf(idf)
    top_common = sorted(idf.items(), key=lambda x: x[1])[:8]
    print(f"  Most common (dampened): {[t for t,_ in top_common]}")

    print("[4/4] Scoring pairs (full re-score — edges depend on global IDF)...")
    db.delete_all_edges()
    edges = score_all_pairs(enriched, min_score=0.12)
    print(f"  Generated {len(edges)} edges above threshold")

    for src_id, tgt_id, scores, total in edges:
        db.upsert_edge(src_id, tgt_id, scores, total)
    db.commit()

    print(f"Done. DB: {args.db}")
    print(f"  Nodes: {db.node_count()}, Edges: {db.edge_count()}")


def cmd_query(args):
    from store.db import DB
    from query.query import query, print_results

    db = DB(args.db)
    _stale_warning(db, args)
    hint = " ".join(args.hint)
    print(f"\nQuery: {hint}\n" + "=" * 50)
    results = query(db, hint, top_n=args.top)
    print_results(results)


def cmd_expand(args):
    from store.db import DB
    from query.query import expand, print_expand

    db = DB(args.db)
    _stale_warning(db, args)
    name = " ".join(args.name)
    print(f"\nExpand: {name}\n" + "=" * 50)
    results = expand(db, name)
    print_expand(results)


PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SNIPPET = os.path.join(PKG_DIR, "claude-md-snippet.md")
MCP_SERVER_PATH = os.path.join(PKG_DIR, "mcp_server.py")


def run_scan_and_embed(config_path: str, db_path: str, emb_path: str = None, force: bool = False) -> dict:
    """
    Shared worker: scan repos, build TF-IDF token edges.
    Used by both `install` (first-time setup) and the MCP `rescan` tool.
    Returns a summary dict {nodes, duration_ms} — no stdout assumptions, no sys.exit.
    The emb_path parameter is accepted but ignored (embeddings removed).
    """
    import time
    t0 = time.monotonic()

    scan_args = argparse.Namespace(config=config_path, db=db_path, force=force)
    cmd_scan(scan_args)

    from store.db import DB as _DB
    _db = _DB(db_path)
    n_nodes = _db.node_count()

    return {
        "nodes": n_nodes,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }


def cmd_install(args):
    """All-in-one setup: scan repos, write <workspace>/.mcp.json, inject CLAUDE.md."""
    config_path = os.path.abspath(args.config)
    workspace = os.path.abspath(args.workspace)
    snippet_path = os.path.abspath(args.snippet) if args.snippet else DEFAULT_SNIPPET

    if not os.path.isfile(config_path):
        print(f"ERROR: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(workspace):
        print(f"ERROR: workspace dir not found: {workspace}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(snippet_path):
        print(f"ERROR: snippet not found: {snippet_path}", file=sys.stderr)
        sys.exit(1)

    data_dir = os.path.join(workspace, ".ariadne")
    os.makedirs(data_dir, exist_ok=True)
    db_path  = os.path.join(data_dir, "ariadne.db")
    fb_path  = os.path.join(data_dir, "feedback.db")
    manifest_path = os.path.join(data_dir, "manifest.json")

    # 1. Scan (via shared helper)
    if args.no_scan:
        print(f"==> --no-scan; expecting DB at {db_path}")
        if not os.path.isfile(db_path):
            print(f"ERROR: --no-scan but DB missing at {db_path}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"==> Scanning via {config_path}")
        run_scan_and_embed(config_path, db_path, force=args.force)

    # 2. Persist manifest so the MCP `rescan` tool can find the config later.
    with open(manifest_path, "w") as f:
        json.dump({"config_path": config_path}, f, indent=2)
        f.write("\n")
    print(f"==> Wrote {manifest_path}")

    # 3. Write .mcp.json
    mcp_json = os.path.join(workspace, ".mcp.json")
    cfg = {}
    if os.path.exists(mcp_json):
        with open(mcp_json) as f:
            try:
                cfg = json.load(f)
            except json.JSONDecodeError:
                cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    servers["ariadne"] = {
        "command": "python3",
        "args": [MCP_SERVER_PATH, "--db", db_path, "--fb", fb_path],
    }
    with open(mcp_json, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    print(f"==> Wrote {mcp_json}")

    # 4. Inject CLAUDE.md (idempotent via marker)
    with open(snippet_path) as f:
        snippet = f.read()
    marker = args.marker
    claude_md = os.path.join(workspace, "CLAUDE.md")
    if os.path.isfile(claude_md):
        with open(claude_md) as f:
            existing = f.read()
        if marker in existing:
            print(f"==> CLAUDE.md: SKIP (marker '{marker}' present)")
        else:
            with open(claude_md, "a") as f:
                f.write("\n---\n")
                f.write(snippet)
            print(f"==> CLAUDE.md: APPENDED to {claude_md}")
    else:
        with open(claude_md, "w") as f:
            f.write(snippet)
        print(f"==> CLAUDE.md: CREATED {claude_md}")

    from store.db import DB
    try:
        n_nodes = DB(db_path).node_count()
        n_str = f"{n_nodes} nodes"
    except Exception:
        n_str = "unknown size"

    print(f"""
Done. Restart Claude Code to activate the Ariadne MCP server.

  DB:        {db_path}  ({n_str})
  Built from repos listed in: {config_path}
  Scan mode: in-place (reads each repo's working tree; nothing is cloned)

To rebuild the DB:
  - After pulling new code in your work repos, re-run:
      python3 {sys.argv[0]} install {config_path} {workspace}
  - To add/remove repos or change paths, edit ariadne.config.json
    (paths are relative to the config file's directory)
  - To skip the scan and reuse the existing DB, pass --no-scan
""")


def cmd_config_validate(args):
    """Static sanity check on ariadne.config.json."""
    global SCANNER_REGISTRY
    if not SCANNER_REGISTRY:
        SCANNER_REGISTRY.update(_get_scanner_registry())

    cfg_path = os.path.abspath(args.config)
    cfg = _load_config(cfg_path)
    cfg_dir = os.path.dirname(cfg_path)

    errors: list[str] = []
    warnings: list[str] = []

    repos = cfg.get("repos", [])
    if not isinstance(repos, list) or not repos:
        errors.append("`repos` must be a non-empty array")
        print_issues(errors, warnings)
        sys.exit(1)

    declared_names = set()
    for i, entry in enumerate(repos):
        loc = f"repos[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{loc}: not an object")
            continue
        name = entry.get("name")
        path = entry.get("path")
        scanners = entry.get("scanners", [])

        if not name or not isinstance(name, str):
            errors.append(f"{loc}: missing/invalid `name`")
            continue
        loc = f"repos[{i}]({name})"
        if name in declared_names:
            errors.append(f"{loc}: duplicate repo name")
        declared_names.add(name)

        if not path or not isinstance(path, str):
            errors.append(f"{loc}: missing/invalid `path`")
        else:
            abs_path = _resolve_path(cfg_dir, path)
            if not os.path.isdir(abs_path):
                errors.append(f"{loc}: path not found → {abs_path}")

        if not isinstance(scanners, list):
            errors.append(f"{loc}: `scanners` must be an array")
            continue
        if not scanners:
            warnings.append(f"{loc}: empty `scanners` — repo will be skipped")

        for j, sc in enumerate(scanners):
            sc_loc = f"{loc}.scanners[{j}]"
            if isinstance(sc, str):
                sc_name, sc_opts = sc, {}
            elif isinstance(sc, dict):
                sc_name = sc.get("type")
                sc_opts = {k: v for k, v in sc.items() if k != "type"}
                if not sc_name:
                    errors.append(f"{sc_loc}: missing `type`")
                    continue
            else:
                errors.append(f"{sc_loc}: must be string or object")
                continue

            if sc_name not in SCANNER_REGISTRY:
                errors.append(
                    f"{sc_loc}: unknown scanner type '{sc_name}' "
                    f"(known: {sorted(SCANNER_REGISTRY)})"
                )
                continue

            if sc_name == "backend_clients":
                ctm = sc_opts.get("client_target_map", {})
                if not isinstance(ctm, dict):
                    errors.append(f"{sc_loc}: `client_target_map` must be an object")
                else:
                    for client, target in ctm.items():
                        # Target must be a repo we declare in this same config
                        # (otherwise the edge has no landing point).
                        if target not in declared_names and target not in {
                            e.get("name") for e in repos if isinstance(e, dict)
                        }:
                            warnings.append(
                                f"{sc_loc}: client_target_map['{client}'] → "
                                f"'{target}' is not a declared repo"
                            )
            if sc_name == "frontend_rest":
                bcs = sc_opts.get("base_class_service", {})
                if not isinstance(bcs, dict):
                    errors.append(f"{sc_loc}: `base_class_service` must be an object")
                else:
                    declared = {e.get("name") for e in repos if isinstance(e, dict)}
                    for cls, target in bcs.items():
                        if target not in declared:
                            warnings.append(
                                f"{sc_loc}: base_class_service['{cls}'] → "
                                f"'{target}' is not a declared repo"
                            )

    print_issues(errors, warnings)
    if errors:
        sys.exit(1)
    print(f"\nOK — {len(repos)} repos, 0 errors, {len(warnings)} warnings.")


def print_issues(errors: list[str], warnings: list[str]):
    if errors:
        print("ERRORS:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
    if warnings:
        print("WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"  ! {w}", file=sys.stderr)


def cmd_stats(args):
    from store.db import DB
    from collections import Counter

    db = DB(args.db)
    _stale_warning(db, args)
    nodes = db.get_all_nodes()
    type_counts = Counter(n["type"] for n in nodes)
    svc_counts = Counter(n["service"] for n in nodes)

    print(f"\nDB: {args.db}")
    print(f"Total nodes: {db.node_count()}")
    print(f"Total edges: {db.edge_count()}")
    print("\nNode types:")
    for t, c in type_counts.most_common():
        print(f"  {t}: {c}")
    print("\nServices:")
    for s, c in svc_counts.most_common():
        print(f"  {s}: {c}")


def build_parser() -> argparse.ArgumentParser:
    """
    Single source of truth for Ariadne's CLI surface.

    Importable so mcp_server.py can derive subcommand usage strings from the
    same argparse definitions instead of hand-copying them into help text.
    """
    parser = argparse.ArgumentParser(description="ariadne: cross-service chain hinter")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB path")
    sub = parser.add_subparsers(dest="command")

    scan_parser = sub.add_parser("scan", help="Scan repos and build DB")
    scan_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Config JSON file listing repos and scanners (default: ariadne.config.json)",
    )
    scan_parser.add_argument(
        "--force",
        action="store_true",
        help="Force full re-scan of all repos, ignoring stored git HEAD hashes",
    )

    q_parser = sub.add_parser("query", help="Query by business term")
    q_parser.add_argument("hint", nargs="+", help="Business term or operation name")
    q_parser.add_argument("--top", type=int, default=5, help="Number of clusters to show")

    e_parser = sub.add_parser("expand", help="Expand from a node name")
    e_parser.add_argument("name", nargs="+", help="Endpoint/topic/operation name")

    sub.add_parser("stats", help="Show DB statistics")

    config_parser = sub.add_parser("config", help="Config subcommands")
    config_sub = config_parser.add_subparsers(dest="config_command")
    validate_parser = config_sub.add_parser("validate", help="Validate ariadne.config.json")
    validate_parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help="Config JSON file (default: ariadne.config.json)",
    )

    install_parser = sub.add_parser(
        "install",
        help="One-shot setup: scan, write <workspace>/.mcp.json, inject CLAUDE.md",
    )
    install_parser.add_argument("config", help="Path to ariadne.config.json (work-side scanner config)")
    install_parser.add_argument("workspace", help="Workspace dir (e.g. ~/Desktop/work) — DB lives in <workspace>/.ariadne/")
    install_parser.add_argument("--snippet", default=None, help="Override bundled CLAUDE.md snippet")
    install_parser.add_argument("--no-scan", action="store_true", help="Skip scan; reuse existing DB")
    install_parser.add_argument("--force", action="store_true", help="Force full re-scan instead of incremental")
    install_parser.add_argument(
        "--marker",
        default="## Ariadne",
        help="Idempotency marker; if present in CLAUDE.md, skip injection (default: '## Ariadne')",
    )

    # Attach subparser handles so callers (e.g. mcp_server) can introspect
    # individual subcommands without reparsing.
    parser._ariadne_subparsers = {
        "scan": scan_parser,
        "query": q_parser,
        "expand": e_parser,
        "install": install_parser,
        "config": config_parser,
    }
    return parser


def main():
    parser = build_parser()
    config_parser = parser._ariadne_subparsers["config"]
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "scan": cmd_scan,
        "query": cmd_query,
        "expand": cmd_expand,
        "stats": cmd_stats,
        "install": cmd_install,
    }
    if args.command == "config":
        if args.config_command == "validate":
            cmd_config_validate(args)
            return
        config_parser.print_help()
        sys.exit(1)
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
