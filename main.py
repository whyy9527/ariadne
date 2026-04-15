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
DEFAULT_EMB = os.path.join(os.path.dirname(__file__), "embeddings.db")
DEFAULT_CONFIG = "ariadne.config.json"


SCANNER_REGISTRY = {
    "graphql": "scanner.graphql_scanner:scan_graphql_files",
    "http": "scanner.http_scanner:scan_http_controllers",
    "kafka": "scanner.kafka_scanner:scan_kafka",
    "frontend_graphql": "scanner.frontend_scanner:scan_frontend",
    "frontend_rest": "scanner.frontend_rest_scanner:scan_frontend_rest",
    "backend_clients": "scanner.backend_client_scanner:scan_backend_clients",
    "cube": "scanner.cube_scanner:scan_cubes",
}


def _load_callable(spec: str):
    """Load a function from a 'module:func_name' spec (used for built-in scanners)."""
    mod_name, func_name = spec.split(":", 1)
    mod = __import__(mod_name, fromlist=[func_name])
    return getattr(mod, func_name)


def _resolve_scanner(sc_name: str, sc_opts: dict):
    """Return a callable ``fn(repo_path, service) -> list[dict]``.

    Resolution order:
    1. Built-in name (e.g. ``"graphql"``) → look up SCANNER_REGISTRY, load function.
    2. Dotted-path class reference (``"module.path:ClassName"``) not in the built-in
       registry → dynamic-import the class via importlib, instantiate with *sc_opts*
       as kwargs, return a bound ``scan`` method.

    Raises ``ValueError`` for unknown names / malformed specs.
    """
    import importlib

    # --- 1. Built-in name ---
    spec = SCANNER_REGISTRY.get(sc_name)
    if spec is not None:
        # Returns (callable, is_class_based=False) — caller passes sc_opts as kwargs
        return _load_callable(spec), False

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
        # Returns (bound scan method, is_class_based=True) — opts consumed in __init__
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
    print(f"[1/5] Scanning {len(repos)} repos ({mode}; config: {cfg_path}) ...")

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
                fn, is_class_based = _resolve_scanner(sc_name, sc_opts)
            except ValueError as exc:
                print(f"  {name}: WARN {exc}", file=sys.stderr)
                continue
            # Built-in (function) scanners: forward sc_opts as kwargs.
            # Class-based custom scanners: opts already consumed by __init__,
            # so call scan(repo_path, service) with no extra kwargs.
            if is_class_based:
                nodes = fn(path, name)
            else:
                nodes = fn(path, name, **sc_opts) if sc_opts else fn(path, name)
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
        print(f"\n[2/5] All repos unchanged — skipping normalize/IDF/scoring.")
        print(f"[5/5] Done. DB: {args.db}")
        print(f"  Nodes: {db.node_count()}, Edges: {db.edge_count()}")
        return

    print(f"\n[2/5] Normalizing — {len(enriched)} nodes total")
    # Re-normalize reused nodes too so IDF sees consistent token lists.
    for node in enriched:
        if not node.get("tokens") or not node.get("field_tokens"):
            norm = normalize(node["raw_name"], node.get("fields", []))
            node["tokens"] = norm["tokens"]
            node["field_tokens"] = norm["field_tokens"]

    print("[3/5] Computing TF-IDF weights...")
    idf = compute_idf(enriched)
    db.upsert_token_idf(idf)
    db.commit()
    set_idf(idf)
    top_common = sorted(idf.items(), key=lambda x: x[1])[:8]
    print(f"  Most common (dampened): {[t for t,_ in top_common]}")

    print("[4/5] Scoring pairs (full re-score — edges depend on global IDF)...")
    db.delete_all_edges()
    edges = score_all_pairs(enriched, min_score=0.12)
    print(f"  Generated {len(edges)} edges above threshold")

    for src_id, tgt_id, scores, total in edges:
        db.upsert_edge(src_id, tgt_id, scores, total)
    db.commit()

    print(f"[5/5] Done. DB: {args.db}")
    print(f"  Nodes: {db.node_count()}, Edges: {db.edge_count()}")


def cmd_query(args):
    from store.db import DB
    from store.embedding_db import EmbeddingDB
    from scoring.embedder import build_embeddings
    from query.query import query, print_results

    db = DB(args.db)
    edb = EmbeddingDB(args.emb)
    node_count = db.node_count()
    if edb.is_stale(node_count):
        print(
            f"[ariadne] Building embeddings for {node_count} nodes (first run ~30s)...",
            file=sys.stderr,
        )
        build_embeddings(db.get_all_nodes(), edb)
        print("[ariadne] Embeddings ready.", file=sys.stderr)

    hint = " ".join(args.hint)
    print(f"\nQuery: {hint}\n" + "=" * 50)
    results = query(db, hint, top_n=args.top, edb=edb)
    print_results(results)


def cmd_expand(args):
    from store.db import DB
    from query.query import expand, print_expand

    db = DB(args.db)
    name = " ".join(args.name)
    print(f"\nExpand: {name}\n" + "=" * 50)
    results = expand(db, name)
    print_expand(results)


PKG_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SNIPPET = os.path.join(PKG_DIR, "claude-md-snippet.md")
MCP_SERVER_PATH = os.path.join(PKG_DIR, "mcp_server.py")


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
    emb_path = os.path.join(data_dir, "embeddings.db")
    fb_path  = os.path.join(data_dir, "feedback.db")

    # 1. Scan (unless --no-scan)
    if not args.no_scan:
        print(f"==> Scanning via {config_path}")
        scan_args = argparse.Namespace(config=config_path, db=db_path, force=args.force)
        cmd_scan(scan_args)
    else:
        print(f"==> --no-scan; expecting DB at {db_path}")
        if not os.path.isfile(db_path):
            print(f"ERROR: --no-scan but DB missing at {db_path}", file=sys.stderr)
            sys.exit(1)

    # 2. Warm embeddings.db so the first MCP query doesn't pay a cold-start tax.
    #    Downloads the ONNX model (~34MB) on first run; subsequent runs reuse cache.
    if not args.no_embed:
        from store.db import DB as _DB
        from store.embedding_db import EmbeddingDB
        from scoring.embedder import build_embeddings, _get_session
        _db = _DB(db_path)
        edb = EmbeddingDB(emb_path)
        n_nodes = _db.node_count()
        # Ensure model is downloaded and session loads before embedding
        print("==> Verifying ONNX embedding model (downloads ~34MB on first run)...")
        _get_session()
        print("    ONNX session ready")
        if edb.is_stale(n_nodes):
            print(f"==> Building embeddings for {n_nodes} nodes...")
            build_embeddings(_db.get_all_nodes(), edb)
            print("    embeddings ready")
        else:
            print("==> Embeddings up to date")

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
        "args": [MCP_SERVER_PATH, "--db", db_path, "--emb", emb_path, "--fb", fb_path],
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


def main():
    parser = argparse.ArgumentParser(description="ariadne: cross-service chain hinter")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB path")
    parser.add_argument("--emb", default=DEFAULT_EMB, help="Embeddings DB path")
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
    install_parser.add_argument("--no-embed", action="store_true", help="Skip warming embeddings.db (first MCP query will rebuild it ~30s)")
    install_parser.add_argument(
        "--marker",
        default="## Ariadne",
        help="Idempotency marker; if present in CLAUDE.md, skip injection (default: '## Ariadne')",
    )

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
