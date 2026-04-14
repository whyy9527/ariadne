"""
Scoring engine: generates cross-node edges + clusters.

Score formula:
  base = idf_jaccard(name_tokens) * 0.55 + idf_jaccard(field_tokens) * 0.45
  role_mult = 1.3 if complementary type pair, else 1.0
  service_mult = 1.25 if cross-service, else 0.8
  final = base * role_mult * service_mult   (capped at 1.0)

TF-IDF: tokens common across >20% of corpus get log-dampened IDF weight.
Edge only emitted if base > 0 (requires actual token overlap).
"""
import math
from collections import Counter
from itertools import combinations
from normalizer.normalizer import split_tokens

COMPLEMENTARY_PAIRS = {
    frozenset({"graphql_mutation", "http_endpoint"}),
    frozenset({"graphql_mutation", "kafka_topic"}),
    frozenset({"graphql_query", "http_endpoint"}),
    frozenset({"frontend_mutation", "graphql_mutation"}),
    frozenset({"frontend_query", "graphql_query"}),
    frozenset({"frontend_mutation", "http_endpoint"}),
    frozenset({"http_endpoint", "kafka_topic"}),
    frozenset({"frontend_query", "http_endpoint"}),
    frozenset({"frontend_mutation", "kafka_topic"}),
    # cube.js analytics: GraphQL queries fan out to cube queries
    frozenset({"graphql_query", "cube_query"}),
    frozenset({"cube_query", "http_endpoint"}),
}

METHOD_OP_ALIGN = {
    ("POST", "graphql_mutation"): 0.15,
    ("POST", "frontend_mutation"): 0.15,
    ("GET", "graphql_query"): 0.15,
    ("GET", "frontend_query"): 0.15,
    ("DELETE", "graphql_mutation"): 0.10,
    ("PUT", "graphql_mutation"): 0.10,
    ("PATCH", "graphql_mutation"): 0.10,
}


def compute_idf(nodes: list[dict]) -> dict[str, float]:
    """
    Compute IDF weights across the corpus.
    idf(t) = log(N / df(t)) — high for rare tokens, low for common ones.
    Tokens in >30% of nodes get extra dampening.
    """
    N = len(nodes)
    if N == 0:
        return {}
    df: Counter = Counter()
    for node in nodes:
        # Each node contributes its unique tokens once to df
        unique_tokens = set(node.get("tokens", [])) | set(node.get("field_tokens", []))
        for t in unique_tokens:
            df[t] += 1

    idf = {}
    for token, count in df.items():
        idf[token] = math.log(N / count)

    return idf


def idf_weighted_jaccard(a: list[str], b: list[str], idf: dict[str, float]) -> float:
    """Jaccard similarity weighted by IDF — rare tokens count more."""
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = sa & sb
    union = sa | sb
    if not union:
        return 0.0

    def weight(t):
        return idf.get(t, 1.0)

    inter_w = sum(weight(t) for t in inter)
    union_w = sum(weight(t) for t in union)
    return inter_w / union_w if union_w > 0 else 0.0


def jaccard(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union)


# Global IDF map — set by main.py after scanning
_IDF: dict[str, float] = {}


def set_idf(idf: dict[str, float]):
    global _IDF
    _IDF = idf


def compute_scores(node_a: dict, node_b: dict) -> tuple[dict, float]:
    # Use IDF-weighted jaccard if IDF available, else plain jaccard
    _jac = (lambda a, b: idf_weighted_jaccard(a, b, _IDF)) if _IDF else jaccard

    name_score = _jac(node_a.get("tokens", []), node_b.get("tokens", []))

    fa = node_a.get("field_tokens", [])
    fb = node_b.get("field_tokens", [])
    field_score = _jac(fa, fb)
    cross_ab = _jac(node_a.get("tokens", []), fb)
    cross_ba = _jac(node_b.get("tokens", []), fa)
    field_score = max(field_score, cross_ab * 0.7, cross_ba * 0.7)

    base = name_score * 0.55 + field_score * 0.45

    types_pair = frozenset({node_a["type"], node_b["type"]})
    role_mult = 1.30 if types_pair in COMPLEMENTARY_PAIRS else 1.0
    for node in (node_a, node_b):
        other = node_b if node is node_a else node_a
        if node.get("method") and other.get("type"):
            bonus = METHOD_OP_ALIGN.get((node["method"], other["type"]), 0)
            role_mult += bonus

    service_mult = 1.25 if node_a.get("service") != node_b.get("service") else 0.80

    total = min(base * role_mult * service_mult, 1.0)

    scores = {
        "name_score": round(name_score, 4),
        "field_score": round(field_score, 4),
        "role_score": round(role_mult - 1.0, 4),
        "service_score": round(service_mult, 4),
    }
    return scores, round(total, 4)


def score_all_pairs(nodes: list[dict], min_score: float = 0.12) -> list[tuple]:
    edges = []
    node_list = [n for n in nodes if n.get("tokens")]

    for a, b in combinations(node_list, 2):
        ta, tb = set(a.get("tokens", [])), set(b.get("tokens", []))
        fa, fb = set(a.get("field_tokens", [])), set(b.get("field_tokens", []))
        if not (ta & tb or ta & fb or tb & fa or fa & fb):
            continue

        scores, total = compute_scores(a, b)
        if total >= min_score:
            edges.append((a["id"], b["id"], scores, total))

    edges.sort(key=lambda x: x[3], reverse=True)
    return edges


def _node_hint_score(node: dict, hint_tokens: set) -> float:
    """How well does this node match the hint? Returns 0-1."""
    nt = set(node.get("tokens", []))
    nf = set(node.get("field_tokens", []))
    if not hint_tokens:
        return 0.0
    name_jac = len(hint_tokens & nt) / max(len(hint_tokens | nt), 1)
    field_jac = len(hint_tokens & nf) / max(len(hint_tokens | nf), 1)
    return name_jac * 0.75 + field_jac * 0.25


def find_anchors(nodes: list[dict], query_hint: str, top_n: int = 30) -> list[dict]:
    """Find anchor nodes: those with meaningful direct relevance to the query hint."""
    hint_tokens = set(split_tokens(query_hint))
    scored = [(n, _node_hint_score(n, hint_tokens)) for n in nodes if n.get("tokens")]
    scored.sort(key=lambda x: -x[1])
    anchors = [n for n, s in scored if s >= 0.15][:top_n]
    if not anchors:
        anchors = [n for n, s in scored if s > 0][:10]
    return anchors


def build_clusters(nodes: list[dict], edges: list[dict], query_hint: str = None,
                   anchors: list[dict] = None, top_n: int = 5) -> list[dict]:
    """
    Anchor-based clustering:
    1. Find anchor nodes: high direct relevance to hint
    2. For each anchor, find best cross-service neighbors per (service, type) pair
    3. Group anchors + their neighbors into tight clusters
    4. Score by confidence + diversity
    """
    node_map = {n["id"]: n for n in nodes}

    def edge_score(e):
        if isinstance(e, tuple):
            return e[3]
        return e.get("total_score", 0)

    def edge_ids(e):
        if isinstance(e, tuple):
            return e[0], e[1]
        return e["source_id"], e["target_id"]

    # Build fast adjacency: node_id → [(neighbor_id, score)]
    adj: dict[str, list[tuple[str, float]]] = {}
    for e in edges:
        sid, tid = edge_ids(e)
        sc = edge_score(e)
        if sc < 0.12:
            continue
        adj.setdefault(sid, []).append((tid, sc))
        adj.setdefault(tid, []).append((sid, sc))
    for nid in adj:
        adj[nid].sort(key=lambda x: -x[1])

    if anchors is None:
        if query_hint:
            anchors = find_anchors(nodes, query_hint)
        else:
            # No hint: use high-degree nodes as anchors
            anchors = sorted(nodes, key=lambda n: -len(adj.get(n["id"], [])))[:20]

    if not anchors:
        return []

    # For each anchor, collect tight neighborhood
    def tight_neighborhood(anchor_id: str, max_size: int = 12) -> list[str]:
        """BFS with strict threshold, capped size."""
        result = [anchor_id]
        visited = {anchor_id}
        # Sort neighbors by score desc
        for neighbor_id, score in adj.get(anchor_id, [])[:max_size * 2]:
            if score < 0.25:
                break
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                result.append(neighbor_id)
                if len(result) >= max_size:
                    break
        return result

    # Group anchors into clusters using union-find on shared neighborhoods
    anchor_hoods = {}
    for a in anchors:
        hood = tight_neighborhood(a["id"])
        hood_set = set(hood)
        anchor_hoods[a["id"]] = hood_set

    # Merge anchors with overlap >= 30%
    merged = []
    used = set()
    anchor_ids = [a["id"] for a in anchors]
    for i, aid in enumerate(anchor_ids):
        if aid in used:
            continue
        group = {aid}
        h_i = anchor_hoods[aid]
        for j, bid in enumerate(anchor_ids[i+1:], i+1):
            if bid in used:
                continue
            h_j = anchor_hoods[bid]
            overlap = len(h_i & h_j) / max(len(h_i | h_j), 1)
            if overlap >= 0.25:
                group.add(bid)
                used.add(bid)
        used.add(aid)
        merged.append(group)

    # Build clusters from groups
    scored_clusters = []
    seen_cluster_sets = []
    for group in merged:
        # Union all neighborhoods
        all_ids = set()
        for gid in group:
            all_ids |= anchor_hoods[gid]

        # Deduplicate against already-found clusters
        is_dup = any(
            len(all_ids & prev) / max(len(all_ids | prev), 1) >= 0.7
            for prev in seen_cluster_sets
        )
        if is_dup:
            continue
        seen_cluster_sets.append(all_ids)

        c_nodes = [nid for nid in all_ids if nid in node_map]
        if len(c_nodes) < 2:
            continue

        # Score cluster
        c_set = set(c_nodes)
        c_edges = [
            edge_score(e) for e in edges
            if edge_ids(e)[0] in c_set and edge_ids(e)[1] in c_set
        ]
        avg_score = sum(c_edges) / max(len(c_edges), 1)

        types = {node_map[n]["type"] for n in c_nodes}
        services = {node_map[n]["service"] for n in c_nodes}
        type_div = min(len(types) / 4.0, 1.0)
        svc_div = min((len(services) - 1) / 2.0, 1.0)

        confidence = round(avg_score * 0.60 + type_div * 0.20 + svc_div * 0.20, 3)
        confidence = min(confidence, 1.0)

        if query_hint:
            hint_tokens = set(split_tokens(query_hint))
            direct = sum(
                1 for nid in c_nodes
                if hint_tokens & set(node_map[nid].get("tokens", []))
            )
            hint_boost = min(direct / max(len(c_nodes), 1) * 0.2, 0.2)
            confidence = min(round(confidence + hint_boost, 3), 1.0)

        # Sort nodes within cluster: anchors first, then by service diversity
        anchor_set = {a["id"] for a in anchors}
        c_nodes_sorted = (
            [n for n in c_nodes if n in anchor_set] +
            [n for n in c_nodes if n not in anchor_set]
        )

        scored_clusters.append({
            "node_ids": c_nodes_sorted,
            "confidence": confidence,
            "query_hint": query_hint,
        })

    scored_clusters.sort(key=lambda x: -x["confidence"])
    return scored_clusters[:top_n]
