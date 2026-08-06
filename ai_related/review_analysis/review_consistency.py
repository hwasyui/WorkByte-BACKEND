"""
Component 4: does the review agree with what actually happened on the contract?

This is deliberately NOT a machine learning model.

Why arithmetic instead of a model
---------------------------------
There is no training data and there cannot be. The only review corpus available
(machine_learning/fake_reviews_dataset.csv) is Amazon product reviews, which
carry no contract telemetry - no on_time_score, no revision_count, no
responsiveness_score. Those exist only for real contracts on this platform.
Shuffling telemetry synthetically would teach a model an invented correlation.

It also does not need one. Both sides are already numeric: the client's
per-category star ratings against objective scores computed in
review_pipeline.py Step 4. Comparing two numbers is subtraction.

And for a reputation input, deterministic is better. A freelancer disputing their
trust score can be shown the arithmetic. It cannot drift, it has no training
distribution to go stale, and it needs no fairness audit - which matters, since
auditing the two models that DO feed trust scores turned up systematic bias in
both (a rating-level bias in the old mismatch regressor, a 7.1x length bias in
authenticity).

Direction matters
-----------------
INFLATION (review flatters the record) suggests reciprocal back-scratching or
coerced praise, and reflects on the freelancer's reputation being overstated.

DEFLATION (review is harsher than the record) suggests retaliation or an
extortion attempt, and reflects on the REVIEWER - not the person reviewed. It
must never be charged against the freelancer: penalising someone for being
unfairly reviewed inverts the intent. Callers should use it to down-weight the
review and to inform the reviewer's own trust score.
"""
from typing import Dict, List, Optional

# Which star category can be checked against which objective measurement, and how
# much to trust the correspondence.
#
# Weights encode how directly the two actually measure the same thing. A star
# rating for timeliness and a computed on-time rate are the same question asked
# twice; "quality" against revision rate is a real signal but a loose one, since
# revisions also happen for scope changes that are nobody's fault.
# Freelancer-review categories are timeliness/quality/communication/professionalism/
# value_for_money; client-review categories are responsiveness/clarity_of_requirements/
# communication/professionalism. The map is shared - a lookup simply misses for
# categories the other side does not collect.
#
# professionalism and value_for_money have no objective counterpart and are absent
# on purpose rather than mapped to something loosely related.
_DIMENSION_MAP = {
    # freelancer reviews
    "timeliness":     ("on_time_score",        1.0),
    "quality":        ("revision_rate_score",  0.4),
    # client reviews
    "responsiveness": ("responsiveness_score", 1.0),
    # Many revision rounds are what unclear or shifting requirements look like from
    # the outside. Loose, because revisions also happen for reasons that are nobody's
    # fault, and because on the freelancer side the same measurement is read as a
    # quality signal - one number, two readings, neither of them tight.
    "clarity_of_requirements": ("revision_rate_score", 0.4),
    # both sides
    "communication":  ("responsiveness_score", 0.4),
}

# The weight of a direct correspondence - the same question asked twice, like a
# timeliness rating against a computed on-time rate. A comparison whose total
# compared weight never reaches this rested entirely on loose mappings, and
# callers charging somebody for the result should say so rather than treat
# "quality vs revision count" as if it were the same kind of evidence.
DIRECT_CORRESPONDENCE_WEIGHT = 1.0

# communication and responsiveness both map onto responsiveness_score, and client
# reviews collect both. Using both would count one objective measurement twice -
# the same double-counting that was just removed from blend_communication_score.
# When responsiveness is present it is the direct mapping and wins.
_SUPERSEDED_WHEN_PRESENT = {"responsiveness": "communication"}


def _normalise_star(score: float) -> float:
    """1-5 stars -> 0-1, matching how client_star_normalized is computed elsewhere."""
    return max(0.0, min(1.0, (float(score) - 1.0) / 4.0))


def compare_review_to_record(
    ratings: List[Dict],
    performance: Dict,
) -> Dict:
    """
    Compare each star rating against its objective counterpart.

    Args:
        ratings: review_ratings rows, each with "category" and "score" (1-5).
        performance: freelancer_performance_scores row, or any mapping with
            on_time_score / revision_rate_score / responsiveness_score. Values may
            be None when there is no supporting data.

            GRANULARITY WARNING for the client side. Freelancer telemetry is stored
            per contract, so a review is compared against the engagement it
            describes. The client equivalents (compute_client_responsiveness_score,
            compute_client_dispute_rate_score) are lifetime aggregates across all of
            that client's contracts, so a client review gets compared against their
            average rather than against this contract. A client who is responsive in
            general but went quiet on this one job will have an accurate complaint
            register as deflation. That is why the client-side weight is lower and
            why the deflation penalty is capped - see calculate_client_trust_score.

    Returns:
        {
            "inflation": float 0-1 or None,   weighted mean of positive gaps
            "deflation": float 0-1 or None,   weighted mean of negative gaps
            "dimensions_compared": int,
            "compared_weight": float,         total _DIMENSION_MAP weight compared
            "per_dimension": {category: {"claimed", "actual", "gap"}},
        }

        Both means are taken over the weight of EVERY dimension compared, not just
        the ones that moved in that direction - see the note above weighted_mean.

        inflation and deflation are None when nothing could be compared - no
        matching category, or no telemetry. None rather than 0.0 on purpose:
        calculate_trust_score drops None components and redistributes their weight,
        whereas 0.0 would assert "this review is perfectly consistent" on no
        evidence. Missing data is neutral, not rewarding.
    """
    by_category = {}
    for row in ratings or []:
        category = row.get("category")
        score = row.get("score")
        if category and score is not None:
            by_category[category] = float(score)

    for present, superseded in _SUPERSEDED_WHEN_PRESENT.items():
        if present in by_category:
            by_category.pop(superseded, None)

    per_dimension = {}
    inflation_terms, deflation_terms = [], []
    compared_weight = 0.0

    for category, claimed_star in by_category.items():
        mapping = _DIMENSION_MAP.get(category)
        if mapping is None:
            continue
        objective_key, weight = mapping

        actual = performance.get(objective_key)
        if actual is None:
            continue

        claimed = _normalise_star(claimed_star)
        gap = claimed - float(actual)

        per_dimension[category] = {
            "claimed": round(claimed, 4),
            "actual": round(float(actual), 4),
            "gap": round(gap, 4),
        }

        compared_weight += weight
        if gap > 0:
            inflation_terms.append((weight, gap))
        elif gap < 0:
            deflation_terms.append((weight, -gap))

    # Divided by the weight of everything compared, NOT by the weight of the terms
    # in this direction. Dividing by the direction's own weight cancelled it out
    # whenever a review moved one way on a single dimension, which is the common
    # case - the confidence weights in _DIMENSION_MAP then attenuated nothing at
    # all. A client rating timeliness 5 against a perfect on-time record (weight
    # 1.0, gap 0) and quality 1 against an 0.8 revision rate (weight 0.4, gap
    # -0.8) scored deflation 0.8, over the unfair-reviewer threshold, on the one
    # dimension the map itself calls loose - while the dimension that agreed
    # exactly, and agreed on the tightest correspondence available, counted for
    # nothing. Over the full compared weight the same review scores 0.23.
    #
    # This is also what the "still evidence" note below has always claimed:
    # an exactly-agreeing dimension has to reach the denominator to be evidence.
    def weighted_mean(terms) -> Optional[float]:
        if not terms or compared_weight <= 0:
            return None
        return round(sum(w * v for w, v in terms) / compared_weight, 4)

    # A dimension that agrees exactly contributes to neither list but is still
    # evidence, so both figures are None only when nothing was comparable at all.
    nothing_comparable = not per_dimension

    return {
        "inflation": None if nothing_comparable else (weighted_mean(inflation_terms) or 0.0),
        "deflation": None if nothing_comparable else (weighted_mean(deflation_terms) or 0.0),
        "dimensions_compared": len(per_dimension),
        # How much confidence the figures above actually rest on, so a caller can
        # tell "harsh against a direct measurement" from "harsh against a loose
        # proxy" - the two are the same number otherwise, and only one of them is
        # worth penalising a reviewer for. Compare against
        # DIRECT_CORRESPONDENCE_WEIGHT.
        "compared_weight": round(compared_weight, 4),
        "per_dimension": per_dimension,
    }


def record_consistency_score(inflation: Optional[float]) -> Optional[float]:
    """
    Trust-score component: 1 - inflation, or None when there was nothing to compare.

    Only inflation is charged. Deflation is a judgement about the reviewer and is
    handled separately - see the module docstring.
    """
    if inflation is None:
        return None
    return round(max(0.0, 1.0 - inflation), 3)
