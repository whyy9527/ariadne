"""
Embedding-based recall supplement for Ariadne.

Uses BAAI/bge-small-en-v1.5 (ONNX int8 quantized, ~34MB, CPU-friendly).
Embeds node names at build time; queries the vector index at query time
to supplement TF-IDF anchors with semantically similar nodes.

Runtime: onnxruntime + tokenizers (Rust). No PyTorch / sentence-transformers.
Cold start: ~0.3s (was ~13s with sentence-transformers + torch).
"""
import math
import os
import sys

MODEL_REPO = "Xenova/bge-small-en-v1.5"
MODEL_FILE = "onnx/model_quantized.onnx"
TOKENIZER_FILE = "tokenizer.json"
CACHE_DIR = os.path.expanduser("~/.cache/ariadne/bge-small-en-v1.5")

_session = None
_tokenizer = None


def _get_model_paths() -> tuple[str, str]:
    """
    Return (model_path, tokenizer_path), downloading from HuggingFace Hub if needed.
    Files are cached to ~/.cache/ariadne/bge-small-en-v1.5/.
    """
    model_path = os.path.join(CACHE_DIR, "onnx", "model_quantized.onnx")
    tokenizer_path = os.path.join(CACHE_DIR, "tokenizer.json")

    if os.path.isfile(model_path) and os.path.isfile(tokenizer_path):
        return model_path, tokenizer_path

    print(
        f"[ariadne] Downloading ONNX model from HuggingFace Hub ({MODEL_REPO})...",
        file=sys.stderr,
    )
    print(
        "  If this fails, check your network access to huggingface.co.",
        file=sys.stderr,
    )
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "ERROR: huggingface_hub not installed. Run: pip install huggingface_hub",
            file=sys.stderr,
        )
        raise

    try:
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=MODEL_FILE,
            local_dir=CACHE_DIR,
        )
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=TOKENIZER_FILE,
            local_dir=CACHE_DIR,
        )
    except Exception as e:
        print(
            f"ERROR: Failed to download model from HuggingFace Hub.\n"
            f"  {e}\n"
            f"  Check network access to huggingface.co, or pre-download the files to:\n"
            f"  {CACHE_DIR}/onnx/model_quantized.onnx\n"
            f"  {CACHE_DIR}/tokenizer.json",
            file=sys.stderr,
        )
        raise

    print("[ariadne] ONNX model downloaded.", file=sys.stderr)
    return model_path, tokenizer_path


def _get_session():
    """Return (onnxruntime session, tokenizers.Tokenizer), loading lazily."""
    global _session, _tokenizer
    if _session is None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        model_path, tokenizer_path = _get_model_paths()
        _tokenizer = Tokenizer.from_file(tokenizer_path)
        _tokenizer.enable_padding(pad_id=0, pad_token="[PAD]", length=512)
        _tokenizer.enable_truncation(max_length=512)
        _session = ort.InferenceSession(model_path)
    return _session, _tokenizer


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts. Returns list of float32 vectors (L2-normalized)."""
    import numpy as np

    session, tokenizer = _get_session()
    encodings = tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

    outputs = session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )
    # last_hidden_state: (batch, seq_len, dim)
    last_hidden_state = outputs[0]

    # Mean pooling over non-padding tokens, then L2 normalize
    mask = attention_mask[:, :, np.newaxis].astype(np.float32)
    summed = (last_hidden_state * mask).sum(axis=1)
    count = mask.sum(axis=1)
    mean = summed / np.maximum(count, 1e-12)
    norm = np.linalg.norm(mean, axis=1, keepdims=True)
    normalized = mean / np.maximum(norm, 1e-12)

    return normalized.tolist()


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
