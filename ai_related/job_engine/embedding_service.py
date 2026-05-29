import asyncio
import hashlib
from typing import List
import numpy as np
from sentence_transformers import SentenceTransformer
from functions.logger import logger

_MODEL_NAME  = "nomic-ai/nomic-embed-text-v1.5"
_EMBED_DIM   = 768
_DOC_PREFIX  = "search_document: "

# Module-level singleton. loaded once at startup, reused across all requests.
_model: SentenceTransformer | None = None

def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger("EMBEDDING_SERVICE", f"Loading {_MODEL_NAME} ...", level="INFO")
        _model = SentenceTransformer(_MODEL_NAME, trust_remote_code=True)
        logger("EMBEDDING_SERVICE", f"{_MODEL_NAME} loaded (dim={_EMBED_DIM})", level="INFO")
    return _model


async def get_embedding(text: str) -> List[float]:
    """
    Generate a 768-dim L2-normalised embedding using nomic-embed-text-v1.5.
    Prepends the required 'search_document:' task prefix before encoding.
    Runs encode() in a thread-pool executor to avoid blocking the async event loop.
    """
    if not text or not text.strip():
        logger("EMBEDDING_SERVICE", "Empty text received, returning zero vector", level="WARNING")
        return [0.0] * _EMBED_DIM

    prefixed = _DOC_PREFIX + text
    log_prefix = hashlib.sha256(prefixed.encode()).hexdigest()[:8]
    logger(
        "EMBEDDING_SERVICE",
        f"embed | hash={log_prefix} | text_len={len(text)}",
        level="DEBUG",
    )

    model = _get_model()
    loop  = asyncio.get_event_loop()

    vec: np.ndarray = await loop.run_in_executor(
        None,
        lambda: model.encode(prefixed, normalize_embeddings=True),
    )

    logger(
        "EMBEDDING_SERVICE",
        f"embed done | hash={log_prefix} | dim={len(vec)}",
        level="DEBUG",
    )
    return vec.tolist()
