"""
Embedding-based recall supplement for Ariadne.

Uses BAAI/bge-small-en-v1.5 (local, ~130MB, CPU-friendly).
Embeds node names at build time; queries the vector index at query time
to supplement TF-IDF anchors with semantically similar nodes.
"""
import math
import sys

MODEL_NAME = "BAAI/bge-small-en-v1.5"

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        print(f"[ariadne] Loading embedding model {MODEL_NAME}...", file=sys.stderr)
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts. Returns list of float32 vectors."""
    model = _get_model()
    vecs = model.encode(texts, show_progress_bar=False, batch_size=64)
    return [v.tolist() for v in vecs]


def embed_one(text: str) -> list[float]:
    return embed_texts([text])[0]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def build_embeddings(nodes: list[dict], edb) -> int:
    """
    Embed all nodes and store in EmbeddingDB. Returns count written.
    node text = raw_name + space-joined tokens (gives model camelCase + split tokens).
    """
    texts = []
    ids = []
    for n in nodes:
        raw = n.get("raw_name", "")
        toks = n.get("tokens") or []
        text = raw + " " + " ".join(toks) if toks else raw
        texts.append(text)
        ids.append(n["id"])

    if not texts:
        return 0

    vecs = embed_texts(texts)
    for nid, vec in zip(ids, vecs):
        edb.upsert(nid, vec)
    edb.commit()
    return len(ids)


def rerank_clusters(hint: str, clusters: list[dict], node_map: dict, edb,
                    blend: float = 0.4) -> list[dict]:
    """
    Rerank IR-produced clusters by blending in embedding similarity.
    For each cluster, take max cosine(hint, node) across its nodes, then:
        final = (1 - blend) * confidence + blend * max_cos
    Returns clusters sorted by final desc. Mutates cluster dicts to add
    'embed_score' and 'final_score'.
    """
    all_vecs = edb.get_all()
    if not all_vecs or not clusters:
        return clusters

    hint_vec = embed_one(hint)

    for c in clusters:
        max_cos = 0.0
        for nid in c.get("node_ids", []):
            vec = all_vecs.get(nid)
            if vec is None:
                continue
            s = cosine(hint_vec, vec)
            if s > max_cos:
                max_cos = s
        c["embed_score"] = round(max_cos, 4)
        c["final_score"] = round(
            (1 - blend) * c.get("confidence", 0.0) + blend * max_cos, 4
        )

    clusters.sort(key=lambda c: -c.get("final_score", 0.0))
    return clusters


def recall_by_embedding(hint: str, nodes: list[dict], edb,
                        top_k: int = 15, threshold: float = 0.5) -> list[dict]:
    """
    Embed hint, compute cosine similarity against all stored node vectors,
    return top_k nodes above threshold as supplemental anchors.
    """
    all_vecs = edb.get_all()
    if not all_vecs:
        return []

    hint_vec = embed_one(hint)

    node_map = {n["id"]: n for n in nodes}
    scores = []
    for nid, vec in all_vecs.items():
        if nid not in node_map:
            continue
        s = cosine(hint_vec, vec)
        if s >= threshold:
            scores.append((nid, s))

    scores.sort(key=lambda x: -x[1])
    return [node_map[nid] for nid, _ in scores[:top_k]]
