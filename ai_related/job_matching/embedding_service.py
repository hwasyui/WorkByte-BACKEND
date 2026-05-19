"""
Embedding generation service.

Uses BAAI/bge-base-en-v1.5 via sentence-transformers (local, no API calls).
768-dimensional L2-normalised vectors — the same model used in GENERATE_DATA.ipynb
and rank_demo.py, ensuring all vectors live in the same embedding space.

Why a single fixed model with no fallback:
  Embeddings from different models are NOT interchangeable even at the same
  dimension. A cosine similarity computed between a bge-base vector and a
  nomic-embed-text vector is meaningless — the two spaces are unrelated. A
  fallback to a different model would silently corrupt pgvector search results
  and portfolio_relevance scores in the ML ranker.
"""

import asyncio
import hashlib
from typing import List

import numpy as np
from sentence_transformers import SentenceTransformer

from functions.logger import logger


_MODEL_NAME = "BAAI/bge-base-en-v1.5"
_EMBED_DIM  = 768

# Module-level singleton — loaded once, reused across all requests.
_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger("EMBEDDING_SERVICE", f"Loading {_MODEL_NAME} ...", level="INFO")
        _model = SentenceTransformer(_MODEL_NAME)
        logger("EMBEDDING_SERVICE", f"{_MODEL_NAME} loaded (dim={_EMBED_DIM})", level="INFO")
    return _model


async def get_embedding(text: str) -> List[float]:
    """
    Generate a 768-dim L2-normalised embedding vector using BAAI/bge-base-en-v1.5.

    Runs the CPU-bound encode() call in a thread-pool executor so it does not
    block the async event loop.

    Args:
        text: Input text to embed. Empty/whitespace-only text returns a zero vector.

    Returns:
        List of 768 floats (L2-normalised).
    """
    if not text or not text.strip():
        logger("EMBEDDING_SERVICE", "Empty text received — returning zero vector", level="WARNING")
        return [0.0] * _EMBED_DIM

    log_prefix = hashlib.sha256(text.encode()).hexdigest()[:8]
    logger(
        "EMBEDDING_SERVICE",
        f"embed | hash={log_prefix} | text_len={len(text)}",
        level="DEBUG",
    )

    model = _get_model()
    loop  = asyncio.get_event_loop()

    vec: np.ndarray = await loop.run_in_executor(
        None,
        lambda: model.encode(text, normalize_embeddings=True),
    )

    logger(
        "EMBEDDING_SERVICE",
        f"embed done | hash={log_prefix} | dim={len(vec)}",
        level="DEBUG",
    )
    return vec.tolist()
