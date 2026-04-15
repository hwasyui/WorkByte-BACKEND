"""
Embedding generation service.
Mode is controlled by the LLM env var:
  "local" -> try Ollama first, fallback to Google on failure
  "api"   -> use Google directly

Both nomic-embed-text (Ollama) and text-embedding-005 (Google) produce 768-dim vectors.
"""

import os
import time
import httpx
from typing import List
from functions.logger import logger


def _get_ollama_embed_url() -> str:
    """
    Build the Ollama embeddings endpoint URL from the OLLAMA_URL environment variable.

    Replaces 127.0.0.1 with host.docker.internal so the container can reach the host Ollama process.

    Returns:
        Full URL string for the Ollama /api/embeddings endpoint.
    """
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    # Strip to base URL then append the embeddings endpoint
    if "/api/" in base:
        base = base[: base.index("/api/")]
    url = base.rstrip("/") + "/api/embeddings"
    # Inside Docker, localhost resolves to the container, not the host
    if "127.0.0.1" in url:
        url = url.replace("127.0.0.1", "host.docker.internal")
    return url


async def _embed_with_ollama(text: str) -> List[float]:
    """
    Request an embedding vector from the local Ollama instance.

    Args:
        text: Input text to embed.

    Returns:
        List of floats representing the embedding vector from the nomic-embed-text model.
    """
    url = _get_ollama_embed_url()
    model = os.getenv("OLLAMA_TEXT_EMBEDDING", "nomic-embed-text")

    logger("EMBEDDING_SERVICE", f"Calling Ollama | url={url} | model={model} | text_len={len(text)}", level="DEBUG")
    t0 = time.perf_counter()

    payload = {"model": model, "prompt": text}
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    embedding = data.get("embedding")
    if not embedding:
        raise ValueError(f"No embedding field in Ollama response: {list(data.keys())}")

    elapsed = round((time.perf_counter() - t0) * 1000)
    logger("EMBEDDING_SERVICE", f"Ollama embedding done | dim={len(embedding)} | elapsed={elapsed}ms", level="INFO")
    return embedding


async def _embed_with_google(text: str) -> List[float]:
    """
    Request an embedding vector from Google Vertex AI (text-embedding-005).

    Args:
        text: Input text to embed.

    Returns:
        List of floats representing the 768-dim embedding vector.
    """
    from google import genai

    project_id = os.getenv("GOOGLE_PROJECT_ID")
    location = os.getenv("GOOGLE_LOCATION", "us-central1")
    model = os.getenv("GOOGLE_TEXT_EMBEDDING", "text-embedding-005")

    logger("EMBEDDING_SERVICE", f"Calling Google Vertex AI | project={project_id} | model={model} | text_len={len(text)}", level="DEBUG")
    t0 = time.perf_counter()

    client = genai.Client(vertexai=True, project=project_id, location=location)
    response = await client.aio.models.embed_content(model=model, contents=text)
    embedding = list(response.embeddings[0].values)

    elapsed = round((time.perf_counter() - t0) * 1000)
    logger("EMBEDDING_SERVICE", f"Google embedding done | dim={len(embedding)} | elapsed={elapsed}ms", level="INFO")
    return embedding


async def get_embedding(text: str) -> List[float]:
    """
    Generate a 768-dim embedding vector for the given text.

    Routes to Ollama or Google based on the LLM environment variable:
    LLM="local" tries Ollama first, falls back to Google on error.
    LLM="api" calls Google directly.

    Args:
        text: Input text to embed.

    Returns:
        List of 768 floats representing the embedding vector.
    """
    mode = os.getenv("LLM", "local").strip().lower()
    logger("EMBEDDING_SERVICE", f"get_embedding called | mode={mode} | text_len={len(text)}", level="DEBUG")

    if mode == "local":
        try:
            return await _embed_with_ollama(text)
        except Exception as e:
            logger(
                "EMBEDDING_SERVICE",
                f"Ollama failed ({type(e).__name__}: {e}) — falling back to Google API",
                level="WARNING",
            )
            return await _embed_with_google(text)
    else:
        return await _embed_with_google(text)
