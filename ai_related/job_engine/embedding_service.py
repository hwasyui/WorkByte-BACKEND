import asyncio
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List
import numpy as np
from sentence_transformers import SentenceTransformer
from functions.logger import logger

_MODEL_NAME    = "nomic-ai/nomic-embed-text-v1.5"
_EMBED_DIM     = 768
_DOC_PREFIX    = "search_document: "
_QUERY_PREFIX  = "search_query: "

# Module-level singleton. loaded once at startup, reused across all requests.
_model: SentenceTransformer | None = None
_model_lock = threading.Lock()

# SentenceTransformer.encode() is not safe to call concurrently from multiple threads
# on one model instance - under real concurrent load (e.g. a freelancer, education,
# work experience and portfolio entry embedding within the same few seconds under
# "immediate" embedding mode) concurrent calls have been observed to corrupt each
# other's tensors mid-computation (mismatched sequence-length dimensions from two
# different calls colliding), not just raise a clean error. A dedicated executor,
# isolated from the app's shared default thread pool, serializes every encode() call
# regardless of how many upserts fire at once - max_workers=1 is the actual guarantee,
# no separate semaphore needed on top.
_EMBED_MAX_CONCURRENT = int(os.getenv("EMBEDDING_MAX_CONCURRENT_ENCODES", "1"))
_EMBED_EXECUTOR = ThreadPoolExecutor(max_workers=_EMBED_MAX_CONCURRENT, thread_name_prefix="embedding")

# Ceiling on how long a caller waits for an embedding before giving up. Every upsert_*
# function in embedding_manager.py already wraps its call in a broad try/except that logs
# and returns a status dict instead of crashing the sweep worker - a TimeoutError here is
# just another exception that same handling already catches, so callers don't gain a new
# failure mode, they just stop waiting silently if the executor queue backs up under load.
_EMBED_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))


def shutdown_executor() -> None:
    """Called from main.py's lifespan teardown so worker threads don't outlive the app."""
    _EMBED_EXECUTOR.shutdown(wait=True, cancel_futures=True)


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                logger("EMBEDDING_SERVICE", f"Loading {_MODEL_NAME} ...", level="INFO")
                _model = SentenceTransformer(_MODEL_NAME, trust_remote_code=True)
                logger("EMBEDDING_SERVICE", f"{_MODEL_NAME} loaded (dim={_EMBED_DIM})", level="INFO")
    return _model


def _encode_sync(prefixed: str) -> np.ndarray:
    model = _get_model()
    return model.encode(prefixed, normalize_embeddings=True)


async def _embed(prefixed: str, label: str) -> List[float]:
    """Shared encode path. Runs on a dedicated executor to avoid blocking the event loop
    and to keep concurrent encode() calls from corrupting each other."""
    log_prefix = hashlib.sha256(prefixed.encode()).hexdigest()[:8]
    logger("EMBEDDING_SERVICE", f"{label} | hash={log_prefix} | text_len={len(prefixed)}", level="DEBUG")
    loop = asyncio.get_event_loop()
    vec: np.ndarray = await asyncio.wait_for(
        loop.run_in_executor(_EMBED_EXECUTOR, _encode_sync, prefixed),
        timeout=_EMBED_TIMEOUT_SECONDS,
    )
    logger("EMBEDDING_SERVICE", f"{label} done | hash={log_prefix} | dim={len(vec)}", level="DEBUG")
    return vec.tolist()


async def get_embedding(text: str) -> List[float]:
    """
    Embed a document for indexing (job roles). Uses 'search_document:' task prefix.
    Runs encode() off the event loop to avoid blocking.
    """
    if not text or not text.strip():
        logger("EMBEDDING_SERVICE", "Empty text received, returning zero vector", level="WARNING")
        return [0.0] * _EMBED_DIM
    return await _embed(_DOC_PREFIX + text, "embed")


async def get_query_embedding(text: str) -> List[float]:
    """
    Embed a query profile for retrieval (freelancer, contract, portfolio).
    Uses 'search_query:' task prefix so nomic-embed-text-v1.5 treats this as the
    query side of an asymmetric retrieval pair against search_document: job vectors.
    """
    if not text or not text.strip():
        logger("EMBEDDING_SERVICE", "Empty text received, returning zero vector", level="WARNING")
        return [0.0] * _EMBED_DIM
    return await _embed(_QUERY_PREFIX + text, "query embed")
