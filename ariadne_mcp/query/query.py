"""
Query layer: given a business term or endpoint/topic name,
return top candidate cross-service chains.
"""
import os
import sys

from ariadne_mcp.normalizer.normalizer import split_tokens
from ariadne_mcp.scoring.engine import build_clusters, find_anchors

# A feedback judgment can break a close lexical tie, never accumulate without
# bound. Exploration is retained in the feedback DB but is not a judgment.
_FEEDBACK_LIMIT = 0.10


def _feedback_adjustment(node_ids, counts):
    evidence = []
    for nid in set(node_ids):
        row = counts.get(nid)
        if row:
            total = row["accepted"] + row["rejected"]
            if total:
                evidence.append((row["accepted"] - row["rejected"]) / total)
    return _FEEDBACK_LIMIT * sum(evidence) / max(len(evidence), 1)


def _eligible_anchors(all_nodes, anchors, hint):
    # A structural GraphQL qualifier must not resolve to commercial billing.
    tokens = set(split_tokens(hint))
    if "graphql" in hint.casefold().split() and "subscription" in tokens:
        typed = [n for n in all_nodes if n["type"] in {
            "graphql_subscription", "frontend_subscription",
        }]
        remainder = tokens - {"graphql", "graph", "ql", "subscription"}
        return (find_anchors(typed, " ".join(sorted(remainder))) if remainder
                else sorted(typed, key=lambda n: n["id"])[:30])
    effective = _effective_hint_tokens(all_nodes, hint)
    # Multi-token hints need a majority of their business terms in one anchor.
    required = min(len(effective), max(2, len(effective) // 2 + 1))
    return [n for n in anchors if _hint_match_count(
        n, effective
    ) >= required]


def query(db, hint: str, top_n: int = 5, fdb=None, *, singleton_fallback: bool = True, evidence_gate: bool = True) -> list[dict]:
    """
    Input: business word OR endpoint/topic name.
    Output: list of cluster dicts with enriched node info.

    Pure SQLite read — no ML inference at query time.
    Semantic similarity is pre-computed at scan time and stored as edge weights.
    """
    from ariadne_mcp.scoring.engine import set_idf
    idf = db.get_token_idf()
    set_idf(idf)

    all_nodes = db.get_all_nodes()

    # Anchor-first edge fetching: find relevant nodes first, then only pull their edges.
    # This scales with anchor count (~30), not corpus size — no magic limit needed.
    if top_n < 1:
        raise ValueError("top_n must be positive")
    anchors = find_anchors(all_nodes, hint)
    if evidence_gate:
        anchors = _eligible_anchors(all_nodes, anchors, hint)

    anchor_ids = [a["id"] for a in anchors]
    anchor_edges = db.get_edges_for_nodes(anchor_ids, min_score=0.25)

    clusters = build_clusters(all_nodes, anchor_edges, query_hint=hint,
                              anchors=anchors, top_n=max(10, top_n))

    if not clusters and singleton_fallback:
        from ariadne_mcp.scoring.engine import _node_hint_score
        hint_tokens = _effective_hint_tokens(all_nodes, hint)
        clusters = [{"node_ids": [a["id"]],
                     "confidence": round(_node_hint_score(a, hint_tokens), 3)}
                    for a in anchors[:min(top_n, 3)]]

    node_map = {n["id"]: n for n in all_nodes}

    enriched = []
    seen_result_sets: set[frozenset[str]] = set()
    for c in clusters:
        raw_nodes = []
        for nid in c["node_ids"]:
            n = node_map.get(nid)
            if not n:
                continue
            raw_nodes.append(n)

        raw_nodes = _filter_nodes_for_hint(raw_nodes, all_nodes, hint)
        if evidence_gate and "graphql" in hint.casefold().split() and "subscription" in split_tokens(hint):
            raw_nodes = [n for n in raw_nodes if n["type"] in {
                "graphql_subscription", "frontend_subscription",
            }]
        if not raw_nodes:
            continue
        result_set = frozenset(n["id"] for n in raw_nodes)
        if result_set in seen_result_sets:
            continue
        seen_result_sets.add(result_set)
        nodes_info = [_format_node(n) for n in raw_nodes]
        # Trim: max 2 per (service, type), overall max 12
        nodes_info = _trim_cluster_nodes(nodes_info)

        # Build directional edge summary for this cluster
        cluster_node_ids = {n["id"] for n in nodes_info}
        cluster_edges = db.get_edges_for_nodes(sorted(cluster_node_ids), min_score=0.12)
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
            "services": sorted({_service_of(n) for n in nodes_info}),
            "directed_edges": directed_edges_deduped,
        })

    # Judge only evidence actually returned to the caller, after filtering and
    # trimming. Hidden neighbours must not influence the visible result rank.
    if os.environ.get("ARIADNE_FEEDBACK_BOOST", "1") != "0" and fdb is not None:
        try:
            counts = fdb.get_node_feedback_counts(hint, explicit_only=True)
            changed = False
            for cluster in enriched:
                adjustment = _feedback_adjustment([n["id"] for n in cluster["nodes"]], counts)
                if adjustment:
                    cluster["confidence"] = round(min(1, max(0, cluster["confidence"] + adjustment)), 6)
                    changed = True
            if changed:
                enriched.sort(key=lambda c: (-c["confidence"], tuple(n["id"] for n in c["nodes"])))
        except Exception as exc:
            print(f"[ariadne] feedback error (non-fatal): {exc}", file=sys.stderr)
    return enriched[:top_n]


def expand(db, node_id_or_name: str, hops: int = 1) -> list[dict]:
    """
    Given exact or partial node id/name, expand to neighbors.
    """
    from ariadne_mcp.scoring.engine import set_idf
    idf = db.get_token_idf()
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


def _effective_hint_tokens(nodes: list[dict], hint: str) -> set[str]:
    hint_tokens = set(split_tokens(hint))
    service_tokens = set()
    for node in nodes:
        svc = node.get("service")
        if svc:
            service_tokens.update(split_tokens(svc))
    non_service_tokens = hint_tokens - service_tokens
    return non_service_tokens or hint_tokens


def _hint_match_count(node: dict, hint_tokens: set[str]) -> int:
    return len(hint_tokens & (set(node.get("tokens", [])) | set(node.get("field_tokens", []))))


def _filter_nodes_for_hint(cluster_nodes: list[dict], all_nodes: list[dict], hint: str) -> list[dict]:
    """For multi-word hints, hide nodes that only match a single generic token."""
    hint_tokens = _effective_hint_tokens(all_nodes, hint)
    min_matches = min(2, len(hint_tokens))
    if min_matches <= 1:
        return cluster_nodes

    strong = [
        n for n in cluster_nodes
        if _hint_match_count(n, hint_tokens) >= min_matches
    ]
    # Only filter when the cluster has a meaningful strong core. If filtering
    # would collapse the cluster to one node, keep the original neighborhood.
    if len(strong) >= 2:
        strong_ids = {n["id"] for n in strong}
        return [n for n in cluster_nodes if n["id"] in strong_ids]
    return cluster_nodes


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
        "source_file": n.get("source_file"),
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
