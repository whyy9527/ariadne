"""
Embedding-based recall supplement for Ariadne.

Uses BAAI/bge-small-en-v1.5 (ONNX int8 quantized, ~34MB, CPU-friendly).
Embeds node names at build time; queries the vector index at query time
to supplement TF-IDF anchors with semantically similar nodes.

Runtime: onnxruntime + tokenizers (Rust). No PyTorch / sentence-transformers.
Cold start: ~0.3s (was ~13s with sentence-transformers + torch).
"""
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


def build_embeddings(nodes: list[dict], edb, batch_size: int = 64) -> int:
    """
    Embed all nodes and store in EmbeddingDB. Returns count written.
    node text = raw_name + space-joined tokens (gives model camelCase + split tokens).
    Processes in batches of batch_size to avoid memory issues with large corpora.
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

    total = len(texts)
    written = 0
    for start in range(0, total, batch_size):
        batch_texts = texts[start:start + batch_size]
        batch_ids = ids[start:start + batch_size]
        vecs = embed_texts(batch_texts)
        for nid, vec in zip(batch_ids, vecs):
            edb.upsert(nid, vec)
        written += len(batch_ids)

    edb.commit()
    return written


def compute_semantic_edges(edb, threshold: float = 0.65) -> list[tuple[str, str, float]]:
    """
    Compute pairwise cosine similarity between all node embeddings using numpy matrix multiply.
    Returns list of (node_id_a, node_id_b, cosine_score) for pairs above threshold.
    Vectors are already L2-normalized, so cosine = dot product.
    """
    all_vecs = edb.get_all()
    if not all_vecs:
        return []

    import numpy as np
    ids = list(all_vecs.keys())
    matrix = np.array([all_vecs[nid] for nid in ids], dtype=np.float32)
    # Vectors are already L2-normalized, so cosine = dot product
    sim_matrix = matrix @ matrix.T

    edges = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            score = float(sim_matrix[i, j])
            if score >= threshold:
                edges.append((ids[i], ids[j], score))
    return edges
