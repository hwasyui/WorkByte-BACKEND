import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, List, Optional


"""
Public read models for the review subsystem.

The stored review/trust rows carry two kinds of field mixed together:

  * things the subject earned - star ratings, on-time delivery, revision rate
  * things the moderation pipeline concluded about the REVIEW and its author -
    authenticity_score, is_flagged_fake, is_flagged_coerced, flag_reasons,
    disagreement_probability, and the aggregates derived from them

Only the first kind belongs on a profile. The second is telemetry about a third
party: a viewer cannot act on it, publishing it invites gaming of the detectors,
and since the publish veto now requires the LLM and the ML model to agree, a
review can be published while still carrying a flag reason like "review praises
clear communication but message thread contains no freelancer responses". That
sentence is a judgement about the reviewer and must not be readable by strangers.

These helpers are applied at the route layer, not inside the *Functions getters,
so the pipelines and the admin moderation screens keep receiving the full rows by
construction rather than by remembering to ask for them.
"""


# Review counts at which a score stops being mostly prior and starts being evidence.
# Mirrors SHRINKAGE_K in review_ai_functions: at k=5 a single review sits ~17% of the
# way from the prior to the raw average, ten reviews ~67%.
CONFIDENCE_BUILDING_AT = 3
CONFIDENCE_ESTABLISHED_AT = 10

# Never leaves the server on a public route.
_MODERATION_ONLY_TRUST_FIELDS = (
    "authenticity_confidence",   # how genuine the reviews look, not how good the work was
    "consistency_score",         # ditto - derived from mismatch severity across reviews
    "effective_review_avg",           # shrunk average; drives `confidence`, must not be printed
    "effective_review_avg_received",  # client-side counterpart
    "weighted_review_avg",       # internal scoring input; display_star_avg is the public figure
    "ai_review_summary_updated_at",
    # Row PKs. Not sensitive, but the subject is addressed by freelancer_id/client_id
    # everywhere else, so exposing a second identifier only invites confusion.
    "id",
    "client_trust_score_id",
)


def review_confidence(total_reviews: Optional[int]) -> str:
    """How much evidence backs a score: 'new' | 'building' | 'established'.

    Returned instead of effective_review_avg so clients cannot render the shrunk
    average as a number. A freelancer who received five 5-star ratings will read
    "3.688" as a bug, not as a confidence adjustment - the honesty belongs in the
    label and the tooltip, not in a second contradictory figure.
    """
    n = int(total_reviews or 0)
    if n >= CONFIDENCE_ESTABLISHED_AT:
        return "established"
    if n >= CONFIDENCE_BUILDING_AT:
        return "building"
    return "new"


SENTIMENT_LABELS = ("positive", "neutral", "negative")


def public_review(review: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A single review with the moderation analysis replaced by a bare sentiment label.

    sentiment_label is the one part of ai_analysis that is safe in public: it
    summarises the tone of text the viewer can already read, so it reveals nothing
    they could not work out themselves. Everything else in that object -
    authenticity_score, is_flagged_fake, is_flagged_coerced, flag_reasons,
    disagreement_probability, sentiment_mismatch - is a judgement about the reviewer, and
    stays server-side.

    Lifted to a flat `sentiment` key rather than left nested, so no future field
    added to ai_analysis can leak by being carried along with it.
    """
    if not review:
        return review

    view = {k: v for k, v in review.items() if k != "ai_analysis"}
    analysis = review.get("ai_analysis") or {}
    label = analysis.get("sentiment_label")
    view["sentiment"] = label if label in SENTIMENT_LABELS else None
    return view


def public_reviews(reviews: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """A review list with the moderation analysis removed from every entry."""
    return [public_review(r) for r in (reviews or [])]


def public_trust_score(
    trust_score: Optional[Dict[str, Any]],
    total_reviews_key: str = "total_reviews",
) -> Optional[Dict[str, Any]]:
    """Trust score with moderation aggregates dropped and `confidence` added.

    total_reviews_key differs by side: freelancers count `total_reviews`, clients
    count `total_reviews_received`.
    """
    if not trust_score:
        return trust_score

    view = {k: v for k, v in trust_score.items() if k not in _MODERATION_ONLY_TRUST_FIELDS}
    view["confidence"] = review_confidence(trust_score.get(total_reviews_key))
    return view
