"""
Query layer: given a business term or endpoint/topic name,
return top candidate cross-service chains.
"""
from normalizer.normalizer import split_tokens
from scoring.engine import build_clusters, find_anchors


def query(db, hint: str, top_n: int = 5, edb=None) -> list[dict]:
    """
    Input: business word OR endpoint/topic name.
    Output: list of cluster dicts with enriched node info.

    edb: optional EmbeddingDB for embedding-based anchor supplementation.
    """
    from scoring.engine import set_idf
    idf = db.get_token_idf()
    if idf:
        set_idf(idf)

    all_nodes = db.get_all_nodes()

    # Anchor-first edge fetching: find relevant nodes first, then only pull their edges.
    # This scales with anchor count (~30), not corpus size — no magic limit needed.
    anchors = find_anchors(all_nodes, hint)

    # Supplement with embedding recall if available
    if edb is not None:
        from scoring.embedder import recall_by_embedding
        embed_anchors = recall_by_embedding(hint, all_nodes, edb)
        anchor_ids_set = {a["id"] for a in anchors}
        extra = [n for n in embed_anchors if n["id"] not in anchor_ids_set]
        anchors = anchors + extra[:10]

    anchor_ids = [a["id"] for a in anchors]
    anchor_edges = db.get_edges_for_nodes(anchor_ids, min_score=0.25)

    # Build top_n * 2 clusters so embedding rerank has room to reshuffle.
    build_n = top_n * 2 if edb is not None else top_n
    clusters = build_clusters(all_nodes, anchor_edges, query_hint=hint,
                              anchors=anchors, top_n=build_n)

    node_map = {n["id"]: n for n in all_nodes}

    # Embedding rerank: blend IR confidence with hint↔cluster cosine similarity.
    if edb is not None and clusters:
        from scoring.embedder import rerank_clusters
        rerank_clusters(hint, clusters, node_map, edb)
        clusters = clusters[:top_n]
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
        enriched.append({
            "query": hint,
            "confidence": c["confidence"],
            "nodes": nodes_info,
            "services": list({_service_of(n) for n in nodes_info}),
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
            other_id = e["target_id"] if e["source_id"] == target["id"] else e["source_id"]
            other = node_map.get(other_id)
            if other:
                neighbors.append({
                    "node": _format_node(other),
                    "score": round(e["total_score"], 3),
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
