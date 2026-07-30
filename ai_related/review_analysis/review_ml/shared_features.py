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

# Negation and contrast markers. VADER already handles simple negation scoping,
# but it misses contrastive pivots ("great work, but missed every deadline"),
# which are exactly the reviews where text and star rating disagree.
_NEGATION_MARKERS = [
    " not ", " no ", "n't", " never ", " nothing ", " nobody ",
    " but ", " however ", " although ", " though ", " except ", " unfortunately ",
]

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


# --------------------------------------------------------------------------
# Sentiment-aware features (sentiment + mismatch models only)
#
# The 7 features above were built for the authenticity model: they measure
# STYLE (templated phrasing, shouting, generic praise) and carry almost no
# polarity signal. all-MiniLM-L6-v2 does not fill that gap either - it was
# trained for semantic similarity, so it organises its space by TOPIC. "I love
# this" and "I hate this" are near-identical topically, and the encoder places
# them close together. A linear head then has to separate them along a
# direction the encoder was never trained to make salient.
#
# These 5 features inject the polarity axis explicitly. They are kept on a
# separate build path so the authenticity model keeps its original 391-dim
# input and its existing artifact stays valid.
# --------------------------------------------------------------------------

SENTIMENT_FEATURE_NAMES = [
    "vader_compound",
    "vader_positive",
    "vader_negative",
    "vader_neutral",
    "negation_density",
]


@lru_cache(maxsize=1)
def _get_vader():
    from nltk.sentiment.vader import SentimentIntensityAnalyzer
    try:
        return SentimentIntensityAnalyzer()
    except LookupError:
        import nltk
        nltk.download("vader_lexicon", quiet=True)
        return SentimentIntensityAnalyzer()


def extract_sentiment_features(raw_text: str) -> np.ndarray:
    """
    Polarity features from the RAW text. VADER is tuned for informal
    user-generated text and handles negation scoping, intensifiers and
    punctuation emphasis itself, so it gets the uncleaned string.
    """
    text = raw_text or ""
    scores = _get_vader().polarity_scores(text)

    lower = f" {text.lower()} "
    word_count = max(len(text.split()), 1)
    negations = sum(lower.count(marker) for marker in _NEGATION_MARKERS)

    return np.array([
        scores["compound"],
        scores["pos"],
        scores["neg"],
        scores["neu"],
        negations / word_count,
    ], dtype=np.float64)


def build_feature_matrix_with_sentiment(texts: List[str]) -> np.ndarray:
    """
    The 391-dim base matrix plus 5 polarity features -> (n, 396).
    Used by the sentiment and mismatch models.
    """
    base = build_feature_matrix(texts)
    sentiment = np.stack([extract_sentiment_features(t) for t in texts])
    return np.concatenate([base, sentiment], axis=1)


def build_feature_matrix_with_sentiment_cached(texts: List[str], cache_key: str) -> np.ndarray:
    """
    Reuses the cached 391-dim base matrix and appends the polarity features,
    which are cheap to recompute (pure-Python VADER, no SBERT pass). This means
    adding sentiment features costs no re-encoding of the corpus.
    """
    base = build_feature_matrix_cached(texts, cache_key)
    sentiment = np.stack([extract_sentiment_features(t) for t in texts])
    return np.concatenate([base, sentiment], axis=1)
