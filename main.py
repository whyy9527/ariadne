#!/usr/bin/env python3
"""
ariadne: local non-invasive cross-service chain hinter.

Usage:
  python main.py scan     [--config PATH] [--db PATH]
  python main.py query    <hint>  [--db PATH] [--top N]
  python main.py expand   <name>  [--db PATH]
  python main.py stats    [--db PATH]
  python main.py install  <target-dir> --snippet PATH [--marker STR]

Scan is config-driven. Pass --config PATH (default: ariadne.config.json in the
current working directory). See ariadne.config.example.json for the format.
"""
import argparse
import json
import sys
import os

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
    mod_name, func_name = spec.split(":", 1)
    mod = __import__(mod_name, fromlist=[func_name])
    return getattr(mod, func_name)


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


def cmd_scan(args):
    from normalizer.normalizer import normalize
    from store.db import DB
    from scoring.engine import compute_idf, set_idf, score_all_pairs

    cfg_path = os.path.abspath(args.config)
    cfg = _load_config(cfg_path)
    cfg_dir = os.path.dirname(cfg_path)

    db = DB(args.db)
    all_nodes = []

    repos = cfg.get("repos", [])
    if not repos:
        print("ERROR: config has no repos.", file=sys.stderr)
        sys.exit(1)

    print(f"[1/5] Scanning {len(repos)} repos (config: {cfg_path}) ...")

    for entry in repos:
        name = entry["name"]
        path = _resolve_path(cfg_dir, entry["path"])
        scanners = entry.get("scanners", [])
        if not os.path.isdir(path):
            print(f"  {name}: SKIP (not found at {path})")
            continue

        counts = []
        for sc in scanners:
            if isinstance(sc, str):
                sc_name, sc_opts = sc, {}
            else:
                sc_name = sc.get("type")
                sc_opts = {k: v for k, v in sc.items() if k != "type"}
            spec = SCANNER_REGISTRY.get(sc_name)
            if not spec:
                print(f"  {name}: WARN unknown scanner '{sc_name}'", file=sys.stderr)
                continue
            fn = _load_callable(spec)
            nodes = fn(path, name, **sc_opts) if sc_opts else fn(path, name)
            all_nodes.extend(nodes)
            counts.append(f"{sc_name}={len(nodes)}")
        print(f"  {name}: {', '.join(counts) if counts else 'no scanners'}")

    if not all_nodes:
        print("ERROR: no nodes scanned. Check config paths and scanner types.", file=sys.stderr)
        sys.exit(1)

    print(f"\n[2/5] Normalizing {len(all_nodes)} nodes...")
    enriched = []
    for node in all_nodes:
        norm = normalize(node["raw_name"], node.get("fields", []))
        node["tokens"] = norm["tokens"]
        node["field_tokens"] = norm["field_tokens"]
        db.upsert_node(node, norm["tokens"], norm["field_tokens"])
        enriched.append(node)
    db.commit()

    print("[3/5] Computing TF-IDF weights...")
    idf = compute_idf(enriched)
    db.upsert_token_idf(idf)
    db.commit()
    set_idf(idf)
    top_common = sorted(idf.items(), key=lambda x: x[1])[:8]
    print(f"  Most common (dampened): {[t for t,_ in top_common]}")

    print("[4/5] Scoring pairs...")
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


def cmd_install(args):
    """Append a snippet to <target>/CLAUDE.md, idempotent via marker check."""
    snippet_path = os.path.abspath(args.snippet)
    target_dir = os.path.abspath(args.target)
    if not os.path.isfile(snippet_path):
        print(f"ERROR: snippet not found: {snippet_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(target_dir):
        print(f"ERROR: target dir not found: {target_dir}", file=sys.stderr)
        sys.exit(1)

    with open(snippet_path) as f:
        snippet = f.read()

    marker = args.marker
    claude_md = os.path.join(target_dir, "CLAUDE.md")

    if os.path.isfile(claude_md):
        with open(claude_md) as f:
            existing = f.read()
        if marker in existing:
            print(f"SKIP: marker '{marker}' already present in {claude_md}")
            return
        with open(claude_md, "a") as f:
            f.write("\n---\n")
            f.write(snippet)
        print(f"APPENDED to {claude_md}")
    else:
        with open(claude_md, "w") as f:
            f.write(snippet)
        print(f"CREATED {claude_md}")


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

    q_parser = sub.add_parser("query", help="Query by business term")
    q_parser.add_argument("hint", nargs="+", help="Business term or operation name")
    q_parser.add_argument("--top", type=int, default=5, help="Number of clusters to show")

    e_parser = sub.add_parser("expand", help="Expand from a node name")
    e_parser.add_argument("name", nargs="+", help="Endpoint/topic/operation name")

    sub.add_parser("stats", help="Show DB statistics")

    install_parser = sub.add_parser(
        "install",
        help="Inject a snippet into <target>/CLAUDE.md (idempotent via marker)",
    )
    install_parser.add_argument("target", help="Target directory containing (or to contain) CLAUDE.md")
    install_parser.add_argument("--snippet", required=True, help="Path to snippet file to inject")
    install_parser.add_argument(
        "--marker",
        default="## Ariadne",
        help="Idempotency marker; if present in CLAUDE.md, skip (default: '## Ariadne')",
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
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
