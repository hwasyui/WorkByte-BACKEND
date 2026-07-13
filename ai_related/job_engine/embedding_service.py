import asyncio
import hashlib
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

# SentenceTransformer.encode() is not safe to call concurrently from multiple
# threads on one shared model instance -- concurrent calls can silently corrupt
# output (e.g. mismatched tensor shapes) rather than raising an exception. A
# dedicated single-worker executor guarantees at most one encode() call
# physically executes at a time, instead of relying on the event loop's default
# executor, which would let many threads race on this same model instance.
_EMBED_EXECUTOR = ThreadPoolExecutor(max_workers=1)


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger("EMBEDDING_SERVICE", f"Loading {_MODEL_NAME} ...", level="INFO")
        _model = SentenceTransformer(_MODEL_NAME, trust_remote_code=True)
        logger("EMBEDDING_SERVICE", f"{_MODEL_NAME} loaded (dim={_EMBED_DIM})", level="INFO")
    return _model


async def _embed(prefixed: str, label: str) -> List[float]:
    """Shared encode path. Runs on the dedicated single-worker executor so at
    most one encode() call executes at a time, system-wide."""
    log_prefix = hashlib.sha256(prefixed.encode()).hexdigest()[:8]
    logger("EMBEDDING_SERVICE", f"{label} | hash={log_prefix} | text_len={len(prefixed)}", level="DEBUG")
    model = _get_model()
    loop  = asyncio.get_event_loop()
    vec: np.ndarray = await loop.run_in_executor(
        _EMBED_EXECUTOR,
        lambda: model.encode(prefixed, normalize_embeddings=True),
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


def shutdown_executor() -> None:
    """
    Shut down the dedicated embedding executor. Called from the FastAPI lifespan
    teardown alongside the sweep worker and database connections, so worker
    threads don't outlive the application process on restart.
    """
    _EMBED_EXECUTOR.shutdown(wait=True)
    logger("EMBEDDING_SERVICE", "Embedding executor shut down", level="INFO")
