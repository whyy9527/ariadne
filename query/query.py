"""
Query layer: given a business term or endpoint/topic name,
return top candidate cross-service chains.
"""
import os
import sys

from normalizer.normalizer import split_tokens
from scoring.engine import build_clusters, find_anchors

# Boost weight applied to clusters with historical positive feedback.
# Tuned empirically at α=0.15 — revisit after accumulating real usage data.
_BOOST_ALPHA = 0.15


def query(db, hint: str, top_n: int = 5, fdb=None) -> list[dict]:
    """
    Input: business word OR endpoint/topic name.
    Output: list of cluster dicts with enriched node info.

    Pure SQLite read — no ML inference at query time.
    Semantic similarity is pre-computed at scan time and stored as edge weights.
    """
    from scoring.engine import set_idf
    idf = db.get_token_idf()
    if idf:
        set_idf(idf)

    all_nodes = db.get_all_nodes()

    # Anchor-first edge fetching: find relevant nodes first, then only pull their edges.
    # This scales with anchor count (~30), not corpus size — no magic limit needed.
    anchors = find_anchors(all_nodes, hint)

    anchor_ids = [a["id"] for a in anchors]
    anchor_edges = db.get_edges_for_nodes(anchor_ids, min_score=0.25)

    clusters = build_clusters(all_nodes, anchor_edges, query_hint=hint,
                              anchors=anchors, top_n=top_n)

    node_map = {n["id"]: n for n in all_nodes}

    # Feedback boost rerank: lift clusters whose node_ids were historically accepted.
    # Controlled by ARIADNE_FEEDBACK_BOOST env var (default: enabled).
    boost_enabled = os.environ.get("ARIADNE_FEEDBACK_BOOST", "1") != "0"
    if boost_enabled and fdb is not None and clusters:
        try:
            accepted_map = fdb.get_accepted_node_ids(hint)
            if accepted_map:
                reranked = False
                for c in clusters:
                    boost = sum(
                        accepted_map.get(nid, 0) for nid in c.get("node_ids", [])
                    )
                    if boost:
                        c["confidence"] = round(c["confidence"] + _BOOST_ALPHA * boost, 6)
                        reranked = True
                if reranked:
                    clusters.sort(key=lambda c: c["confidence"], reverse=True)
                    n_reranked = sum(
                        1 for c in clusters
                        if any(accepted_map.get(nid, 0) for nid in c.get("node_ids", []))
                    )
                    print(
                        f"[ariadne] boost applied: hint={hint!r} clusters_reranked={n_reranked}",
                        file=sys.stderr,
                    )
        except Exception as _boost_err:
            print(f"[ariadne] boost error (non-fatal): {_boost_err}", file=sys.stderr)

    enriched = []
    for c in clusters:
        nodes_info = []
        for nid in c["node_ids"]:
            n = node_map.get(nid)
            if not n:
                continue
            display = _format_node(n)
            nodes_info.append(display)
        # Trim: max 2 per (service, type), overall max 12
        nodes_info = _trim_cluster_nodes(nodes_info)

        # Build directional edge summary for this cluster
        cluster_node_ids = {n["id"] for n in nodes_info}
        cluster_edges = db.get_edges_for_nodes(list(cluster_node_ids), min_score=0.12)
        directed_edges = []
        for e in cluster_edges:
            if e.get("from_service") and e.get("to_service"):
                sid, tid = e["source_id"], e["target_id"]
                if sid in cluster_node_ids and tid in cluster_node_ids:
                    sn = node_map.get(sid)
                    tn = node_map.get(tid)
                    directed_edges.append({
                        "from_service": e["from_service"],
                        "to_service": e["to_service"],
                        "from_node": sn.get("raw_name") if sn else sid,
                        "to_node": tn.get("raw_name") if tn else tid,
                        "score": round(e["total_score"], 3),
                    })
        # Deduplicate by (from_service, to_service) pair — keep highest score
        seen_pairs: dict[tuple, dict] = {}
        for de in directed_edges:
            key = (de["from_service"], de["to_service"])
            if key not in seen_pairs or de["score"] > seen_pairs[key]["score"]:
                seen_pairs[key] = de
        directed_edges_deduped = sorted(seen_pairs.values(), key=lambda x: -x["score"])

        enriched.append({
            "query": hint,
            "confidence": c["confidence"],
            "nodes": nodes_info,
            "services": list({_service_of(n) for n in nodes_info}),
            "directed_edges": directed_edges_deduped,
        })

    return enriched


def expand(db, node_id_or_name: str, hops: int = 1) -> list[dict]:
    """
    Given exact or partial node id/name, expand to neighbors.
    """
    from scoring.engine import set_idf
    idf = db.get_token_idf()
    if idf:
        set_idf(idf)

    all_nodes = db.get_all_nodes()
    # Find matching node
    targets = [
        n for n in all_nodes
        if node_id_or_name.lower() in n["id"].lower()
        or node_id_or_name.lower() in n["raw_name"].lower()
    ]
    if not targets:
        return []

    results = []
    for target in targets[:3]:
        edges = db.get_edges_for_node(target["id"], min_score=0.08)
        node_map = {n["id"]: n for n in all_nodes}
        neighbors = []
        for e in edges[:10]:
            is_outbound = e["source_id"] == target["id"]
            other_id = e["target_id"] if is_outbound else e["source_id"]
            other = node_map.get(other_id)
            if other:
                from_svc = e.get("from_service")
                to_svc = e.get("to_service")
                # Derive direction label relative to the target node
                if from_svc and to_svc:
                    if from_svc == target.get("service"):
                        direction = "outbound"
                    elif to_svc == target.get("service"):
                        direction = "inbound"
                    else:
                        direction = "related"
                else:
                    direction = None
                neighbors.append({
                    "node": _format_node(other),
                    "score": round(e["total_score"], 3),
                    "from_service": from_svc,
                    "to_service": to_svc,
                    "direction": direction,
                })
        results.append({
            "source": _format_node(target),
            "neighbors": neighbors,
        })
    return results


def _trim_cluster_nodes(nodes: list[dict], max_per_bucket: int = 2, max_total: int = 12) -> list[dict]:
    """Keep top max_per_bucket per (service, type) combo, total capped at max_total."""
    from collections import defaultdict
    buckets = defaultdict(int)
    result = []
    for n in nodes:
        key = (n.get("service"), n.get("type"))
        if buckets[key] < max_per_bucket:
            result.append(n)
            buckets[key] += 1
        if len(result) >= max_total:
            break
    return result


def _format_node(n: dict) -> dict:
    t = n.get("type", "")
    label = {
        "graphql_query": "GraphQL Query",
        "graphql_mutation": "GraphQL Mutation",
        "graphql_subscription": "GraphQL Subscription",
        "graphql_type": "GraphQL Type",
        "http_endpoint": f"HTTP {n.get('method','?')} {n.get('path','')}",
        "kafka_topic": "Kafka Topic",
        "frontend_query": "Frontend Query",
        "frontend_mutation": "Frontend Mutation",
        "frontend_subscription": "Frontend Subscription",
        "cube_query": "Cube Query",
        "backend_client_call": "Backend Client Call",
    }.get(t, t)
    return {
        "type": t,
        "label": label,
        "name": n.get("raw_name"),
        "service": n.get("service"),
        "id": n.get("id"),
    }


def _service_of(node_info: dict) -> str:
    return node_info.get("service", "unknown")


def print_results(results: list[dict]):
    if not results:
        print("  (no results)")
        return
    for i, cluster in enumerate(results, 1):
        print(f"\nTop Cluster #{i}  [confidence: {cluster['confidence']}]")
        print(f"  Services: {', '.join(sorted(cluster['services']))}")
        for n in cluster["nodes"]:
            label = n["label"]
            name = n["name"]
            svc = n["service"]
            print(f"  - [{svc}] {label}: {name}")


def print_expand(results: list[dict]):
    if not results:
        print("  (no results)")
        return
    for r in results:
        src = r["source"]
        print(f"\nSource: [{src['service']}] {src['label']}: {src['name']}")
        for nb in r["neighbors"]:
            n = nb["node"]
            print(f"  → [{n['service']}] {n['label']}: {n['name']}  (score={nb['score']})")
