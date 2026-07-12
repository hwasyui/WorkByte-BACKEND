import os
import re
import sys
from functools import lru_cache
from typing import List

import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.harmful_text_detection.preprocessing import TextPreprocessor

SBERT_ENCODER_NAME = "all-MiniLM-L6-v2"

_SUPERLATIVE_KEYWORDS = [
    "amazing", "best", "excellent", "perfect", "incredible", "outstanding",
    "phenomenal", "exceptional", "flawless", "unbelievable", "awesome",
    "fantastic", "wonderful", "superb", "brilliant", "greatest",
]

_GENERIC_PHRASES = [
    "highly recommend", "great product", "works great", "good quality",
    "as described", "would recommend", "five stars", "exceeded my expectations",
    "great price", "fast shipping", "will buy again", "love it", "works well",
    "very happy", "highly satisfied",
]

_FIRST_PERSON_WORDS = {"i", "my", "me", "mine", "myself", "we", "our", "ours", "us"}

ENGINEERED_FEATURE_NAMES = [
    "text_length",
    "word_count",
    "exclamation_ratio",
    "caps_word_ratio",
    "superlative_count",
    "generic_phrase_count",
    "first_person_density",
]


def clean_text(text: str) -> str:
    return TextPreprocessor.clean_text(text or "")


def _count_occurrences(haystack_lower: str, phrases: List[str]) -> int:
    return sum(1 for phrase in phrases if phrase in haystack_lower)


def extract_engineered_features(raw_text: str) -> np.ndarray:
    """
    Stylistic features computed from the RAW (uncleaned) text, since cleaning
    strips exactly the signal these need (case, punctuation repetition).
    """
    text = raw_text or ""
    words = text.split()
    word_count = max(len(words), 1)
    text_lower = text.lower()

    caps_words = sum(1 for w in words if w.isalpha() and w.isupper() and len(w) > 1)

    features = np.array([
        float(len(text)),
        float(len(words)),
        text.count("!") / word_count,
        caps_words / word_count,
        float(_count_occurrences(text_lower, _SUPERLATIVE_KEYWORDS)),
        float(_count_occurrences(text_lower, _GENERIC_PHRASES)),
        sum(1 for w in re.findall(r"[a-zA-Z']+", text_lower) if w in _FIRST_PERSON_WORDS) / word_count,
    ], dtype=np.float64)
    return features


@lru_cache(maxsize=1)
def get_encoder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(SBERT_ENCODER_NAME)


def build_feature_matrix(texts: List[str]) -> np.ndarray:
    """
    (n, 384) SBERT embedding of cleaned text concatenated with (n, 7)
    engineered stylistic features from the raw text -> (n, 391).
    """
    encoder = get_encoder()
    cleaned = [clean_text(t) for t in texts]
    embeddings = encoder.encode(cleaned, show_progress_bar=False, batch_size=64)
    engineered = np.stack([extract_engineered_features(t) for t in texts])
    return np.concatenate([embeddings, engineered], axis=1)


_CACHE_DIR = os.path.join(os.path.dirname(__file__), "machine_learning", "_feature_cache")


def build_feature_matrix_cached(texts: List[str], cache_key: str) -> np.ndarray:
    """
    Same as build_feature_matrix, but memoized to disk under `cache_key` so
    the three training scripts (authenticity / mismatch / sentiment), which
    all embed the same underlying review texts, only pay the SBERT-encoding
    cost once instead of once per script.
    """
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(_CACHE_DIR, f"{cache_key}.npy")
    if os.path.exists(cache_path):
        cached = np.load(cache_path)
        if cached.shape[0] == len(texts):
            return cached
    features = build_feature_matrix(texts)
    np.save(cache_path, features)
    return features
