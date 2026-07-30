"""
Sentiment for review text.

Primary model is PRETRAINED: cardiffnlp/twitter-roberta-base-sentiment-latest,
fine-tuned on ~124M human-labelled examples. The custom SBERT + logistic
regression model that used to live here is kept as an offline fallback only -
train_sentiment_model.py and model_artifacts/sentiment/ still exist and still
work, but nothing prefers them.

Why the custom model was demoted
--------------------------------
  * Its labels were never sentiment. rating_to_sentiment() maps stars to
    sentiment, so "product's great, wrong item shipped" at 1 star is labelled
    negative. That noise caps everything downstream.
  * ITS EVALUATION HAD THE SAME FLAW. The held-out set is star-derived too, so
    every metric ever reported for it measured star-bucket reproduction, not
    sentiment. f1_macro 0.5399 does not mean what it appears to mean.
  * VADER - a rule-based lexicon, no training - scored f1_macro 0.4880 on that
    same set against the custom model's 0.5399. A frozen transformer plus 384
    embedding dimensions plus 16,172 training examples bought ~0.05 over a word
    list.
  * all-MiniLM-L6-v2 is trained for semantic SIMILARITY, so it organises by
    topic: "I love this blender" and "I hate this blender" land close together.
    Polarity is a direction it was never trained to make salient.

Why Cardiff specifically, over nlptown/bert-base-multilingual-uncased-sentiment
-------------------------------------------------------------------------------
Scored on 32 hand-written freelance-domain cases, Cardiff 69% against nlptown
59%. The decisive category was factual problem-mentions - "Flagged a layout
issue early and we fixed it before launch" - where Cardiff scored 100% and
nlptown 20% (rating that example 1 star). That category is exactly why
client_answer was removed from the ML input: the targeted question invites
problem-mentioning replies and the old model read them as negative.

Cardiff's known weakness: it leans positive, scoring 20% on genuinely neutral
text and calling "Work was fine, nothing special" positive. For moderation that
is the more dangerous direction, so negative-review recall is worth watching.

Caveat to state in any writeup: none of these models has been evaluated against
real sentiment labels, because no such labelled set exists for this project yet.
Cardiff is preferred on the strength of its training data (124M human labels vs
16k proxy labels) and 32 hand-checked cases, not on a benchmark.
"""
import os
import sys
from functools import lru_cache
from typing import Dict, Optional, Tuple

import joblib

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from functions.logger import logger

PRETRAINED_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"

# Cardiff emits these three labels; kept as a mapping rather than assuming order.
_CARDIFF_TO_LABEL = {"negative": "negative", "neutral": "neutral", "positive": "positive"}

_LABEL_NAMES = ["negative", "neutral", "positive"]
_FALLBACK_DIR = os.path.join(os.path.dirname(__file__), "model_artifacts", "sentiment")


@lru_cache(maxsize=1)
def _load_pretrained():
    """
    Downloads ~500MB from HuggingFace on first call and caches it. Returns None if
    unavailable (no network on a cold cache, disk full) so the fallback can take
    over rather than review submission failing.
    """
    try:
        from transformers import pipeline
        return pipeline(
            "sentiment-analysis",
            model=PRETRAINED_MODEL,
            top_k=None,
            truncation=True,
            max_length=512,
        )
    except Exception as e:
        logger("REVIEW_ML", f"Could not load {PRETRAINED_MODEL}, falling back to the "
                            f"custom sentiment model: {str(e)[:200]}", level="WARNING")
        return None


@lru_cache(maxsize=1)
def _load_fallback():
    try:
        model = joblib.load(os.path.join(_FALLBACK_DIR, "model.pkl"))
        scaler = joblib.load(os.path.join(_FALLBACK_DIR, "scaler.pkl"))
        return model, scaler
    except Exception:
        return None


def _score_from_probabilities(p_negative: float, p_positive: float) -> float:
    """
    Confidence-weighted -1..1 score for review_ai_analysis.sentiment_score, rather
    than a flat {-1,0,1} bucket, so two "positive" verdicts with different
    confidence do not collapse to the same number.

    Computed independently of the label, so the two can disagree at the margins -
    a "neutral" label with a mildly positive score is expected, not a bug.
    """
    return round(float(p_positive - p_negative), 4)


def cardiff_probabilities(review_text: str) -> Optional[Tuple[float, float, float]]:
    """
    Raw (P_negative, P_neutral, P_positive) from the pretrained model, or None if it
    is unavailable.

    Exposed because the disagreement classifier (mismatch_detector) uses these three
    numbers as features rather than the collapsed label - a label throws away exactly
    the confidence that distinguishes "mildly positive text rated 1 star" from
    "furiously negative text rated 1 star". Returning None lets that model fall back
    to its own SBERT-based variant instead of feeding it zeros.
    """
    clf = _load_pretrained()
    if clf is None:
        return None
    try:
        scores = {r["label"].lower(): float(r["score"]) for r in clf(review_text)[0]}
        return (scores.get("negative", 0.0), scores.get("neutral", 0.0), scores.get("positive", 0.0))
    except Exception as e:
        logger("REVIEW_ML", f"Cardiff scoring failed: {str(e)[:200]}", level="WARNING")
        return None


def _predict_pretrained(review_text: str) -> Dict:
    clf = _load_pretrained()
    scores = {r["label"].lower(): float(r["score"]) for r in clf(review_text)[0]}

    label = _CARDIFF_TO_LABEL[max(scores, key=scores.get)]
    return {
        "sentiment_label": label,
        "sentiment_score": _score_from_probabilities(scores.get("negative", 0.0),
                                                     scores.get("positive", 0.0)),
        "model_used": "cardiff_roberta",
    }


def _predict_fallback(review_text: str) -> Dict:
    from ai_related.review_analysis.review_ml.shared_features import (
        build_feature_matrix_with_sentiment,
    )

    loaded = _load_fallback()
    if loaded is None:
        # Neither model available. Neutral is the honest answer, and the pipeline
        # treats sentiment as a contributing signal rather than a veto.
        logger("REVIEW_ML", "No sentiment model available, returning neutral", level="ERROR")
        return {"sentiment_label": "neutral", "sentiment_score": 0.0, "model_used": "unavailable"}

    model, scaler = loaded
    features = scaler.transform(build_feature_matrix_with_sentiment([review_text]))
    proba = model.predict_proba(features)[0]

    return {
        "sentiment_label": _LABEL_NAMES[int(model.predict(features)[0])],
        "sentiment_score": _score_from_probabilities(float(proba[0]), float(proba[2])),
        "model_used": f"sbert_{type(model).__name__}",
    }


def predict_sentiment(review_text: str) -> Dict:
    """
    Predict positive/neutral/negative sentiment for review text.

    Returns:
        {
            "sentiment_label": "positive" | "neutral" | "negative",
            "sentiment_score": float (-1..1),
            "model_used": "cardiff_roberta" | "sbert_<classifier>" | "unavailable",
        }

    model_used is the observable signal for which path ran - worth checking if
    scores shift unexpectedly, since the fallback is a materially weaker model.
    """
    text = (review_text or "").strip()
    if not text:
        return {"sentiment_label": "neutral", "sentiment_score": 0.0, "model_used": "empty_text"}

    if _load_pretrained() is not None:
        try:
            return _predict_pretrained(text)
        except Exception as e:
            logger("REVIEW_ML", f"Pretrained sentiment failed at inference: {str(e)[:200]}",
                   level="WARNING")

    return _predict_fallback(text)
