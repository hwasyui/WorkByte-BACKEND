"""
Component 3: does a review's TEXT disagree with the star rating attached to it -
the signature of an accidental misclick or a coerced review.

Two models, primary and fallback
--------------------------------
PRIMARY (model_artifacts/disagreement_v2): 12 features - Cardiff's three sentiment
probabilities, five VADER polarity features, and four features derived from the
rating itself. Average precision 0.9248.

FALLBACK (model_artifacts/disagreement): 400 features - a 396-dim frozen
all-MiniLM-L6-v2 matrix plus the same four rating features. Average precision
0.8721. Used only when Cardiff cannot be loaded, since without it the primary model
has no text features at all and feeding it zeros would be worse than switching.

The 12-feature model wins on every axis measured: +0.053 average precision, lower
P(disagree) on genuinely agreeing pairs (0.14-0.28 against 0.21-0.29), and far more
decisive on the case that matters - scathing text rated 5 stars scores 0.990 against
the fallback's 0.714. Twelve numbers beat four hundred because they are the right
twelve: Cardiff was fine-tuned on ~124M human sentiment labels, whereas MiniLM was
trained for semantic similarity and organises its space by topic, so polarity is a
direction it was never asked to make salient.

One expectation did NOT survive
-------------------------------
The point of a small feature set was meant to be inspectable coefficients. Logistic
regression on these 12 features collapses to average precision 0.6117 - barely above
chance - while XGBoost reaches 0.9248 on the identical inputs. The relationship is
irreducibly non-linear: what matters is the INTERACTION between polarity and rating
("negative text AND five stars"), and a linear model over those columns cannot
express a product term. So this is still a tree ensemble, and there are no
coefficients to read off. Worth stating plainly rather than claiming
interpretability the model does not have.

Why a classifier rather than the regressor this replaced
--------------------------------------------------------
A star-rating regressor used to live here and the pipeline thresholded
|predicted - actual|. That is a detector built from a regression residual, and two
measurements killed it:

  * The rule |f(text) - rating| >= 1.5 cannot condition on the rating, because f
    never sees it. So it cannot learn "harsh text with 1 star is fine, harsh text
    with 5 stars is suspicious" - and a genuinely scathing 1-star review produced a
    1.7-star residual and got flagged.
  * On held-out pairs where text and rating AGREED, mean severity ran 2.034 stars
    for 1-star reviews against 0.502 for 5-star ones, because 60% of its training
    corpus was 5-star and it reverted to that mean. Through consistency_score that
    handed 0.492 to freelancers whose clients rate honestly low and 0.874 to those
    rated high. Averaging does not fix that - it is bias, not noise.

Both models here judge the (text, rating) PAIR, so the rating is an input. Their
1-vs-5-star spread on agreeing pairs is -0.054, against the regressor's +1.532.
"""
import os
import sys
from functools import lru_cache
from typing import Dict, Optional

import joblib
import numpy as np

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from ai_related.review_analysis.review_ml.sentiment_detector import cardiff_probabilities
from ai_related.review_analysis.review_ml.shared_features import (
    build_feature_matrix_with_sentiment,
    extract_sentiment_features,
)

_ARTIFACTS = os.path.join(os.path.dirname(__file__), "model_artifacts")
_PRIMARY_DIR = os.path.join(_ARTIFACTS, "disagreement_v2")
_FALLBACK_DIR = os.path.join(_ARTIFACTS, "disagreement")

# P(disagree) past which the pair is called mismatched. Both models are trained on a
# corpus balanced per rating level, so their probabilities are not skewed by a class
# prior and 0.5 is the natural operating point.
#
# Not yet calibrated. On genuinely agreeing pairs the primary model still returns
# 0.14-0.28, so the pipelines require the LLM to agree before this contributes to a
# flag - see resolve_flags in review_decision.py.
DISAGREEMENT_THRESHOLD = 0.5


def _load(directory: str):
    model_path = os.path.join(directory, "model.pkl")
    scaler_path = os.path.join(directory, "scaler.pkl")
    if not (os.path.exists(model_path) and os.path.exists(scaler_path)):
        return None
    try:
        return joblib.load(model_path), joblib.load(scaler_path)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_primary():
    return _load(_PRIMARY_DIR)


@lru_cache(maxsize=1)
def _load_fallback():
    return _load(_FALLBACK_DIR)


def _build_rating_features(rating: float) -> np.ndarray:
    """
    Must stay identical to build_rating_features() in train_disagreement_model.py.
    Distance from the midpoint and the two extreme flags are included because
    disagreement concentrates at 1 and 5 stars.
    """
    r = float(rating)
    return np.array([[r, abs(r - 3.0), float(r >= 4), float(r <= 2)]], dtype=np.float64)


def predict_mismatch(review_text: str, actual_rating: float) -> Dict:
    """
    Judge whether the review text and the rating given belong together.

    Returns:
        {
            "disagreement_probability": float (0-1),
            "is_mismatched": bool,
            "model_used": "cardiff_vader_<classifier>" | "sbert_<classifier>"
                          | "unavailable",
        }

    model_used is the observable signal for which path ran. A value starting with
    "sbert_" means Cardiff was unavailable and the weaker fallback answered.
    """
    rating_features = _build_rating_features(actual_rating)

    primary = _load_primary()
    if primary is not None:
        probabilities = cardiff_probabilities(review_text)
        if probabilities is not None:
            model, scaler = primary
            features = np.hstack([
                np.array([probabilities], dtype=np.float64),
                extract_sentiment_features(review_text).reshape(1, -1),
                rating_features,
            ])
            probability = float(model.predict_proba(scaler.transform(features))[0][1])
            return {
                "disagreement_probability": round(probability, 4),
                "is_mismatched": probability >= DISAGREEMENT_THRESHOLD,
                "model_used": f"cardiff_vader_{type(model).__name__}",
            }

    fallback = _load_fallback()
    if fallback is None:
        # Neither artifact installed. The pipelines require the LLM to agree before
        # this flag contributes, so a neutral answer degrades the ensemble rather
        # than blocking review submission.
        return {
            "disagreement_probability": 0.0,
            "is_mismatched": False,
            "model_used": "unavailable",
        }

    model, scaler = fallback
    features = np.hstack([build_feature_matrix_with_sentiment([review_text]), rating_features])
    probability = float(model.predict_proba(scaler.transform(features))[0][1])
    return {
        "disagreement_probability": round(probability, 4),
        "is_mismatched": probability >= DISAGREEMENT_THRESHOLD,
        "model_used": f"sbert_{type(model).__name__}",
    }
