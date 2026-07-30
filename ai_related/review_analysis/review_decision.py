"""
Shared decision logic for both review pipelines.

Scope, deliberately narrow
--------------------------
review_pipeline.py and client_review_pipeline.py are near-identical in shape but
differ genuinely in their I/O: different tables, different *Functions modules,
different field names (client_answer vs freelancer_answer), different trust-score
formulas, different notification recipients. Merging those into one parameterised
function would be a large rewrite of working code with no test suite to catch
regressions.

What IS worth sharing is the part with no I/O at all: how model outputs combine
into a verdict. That logic was duplicated verbatim, and the duplication has already
cost real work - the OR-to-AND fix, the switch to length-calibrated authenticity,
the ML input split and the judgment logging all had to be applied twice, once per
pipeline, with the drift risk that implies.

So this module holds the pure functions. The pipelines keep their own I/O. A future
fix to how signals combine now happens in one place; a future change to how client
reviews are stored still only touches the client pipeline.
"""
from typing import Dict, List, Optional, Tuple


def split_ml_inputs(comment: str, answer: str) -> Tuple[str, str]:
    """
    (text_for_public_scoring, text_for_internal_scoring).

    Sentiment and authenticity get the COMMENT ONLY:
      * sentiment_label is the one field public_review() exposes publicly, and it
        renders next to the comment. Scoring it over text the reader cannot see
        would put a "neutral" badge on a glowing comment whenever the answer
        mentions a problem, which reads as a bug.
      * the authenticity model is strongly length-sensitive (mean P(fake) 0.348 at
        10-19 words against 0.046 at 160+), so appending the answer would lower
        scores just by making the text longer. A confound, not a signal.

    The disagreement classifier gets COMMENT + ANSWER: it judges whether the review
    as a whole matches the rating, it is purely internal, and the original reason
    for excluding the answer - the old e-commerce-trained sentiment model reading
    factual problem-mentions as negative - no longer applies now that Component 1
    is Cardiff, which scores 100% on that category against the old model's 0%.
    """
    scoring_text = (comment or "").strip()
    answer_text = (answer or "").strip()
    full_text = " ".join(part for part in [scoring_text, answer_text] if part)
    return scoring_text, full_text


def ml_authenticity_component(ml_authenticity: Dict) -> float:
    """
    (1 - P(fake)) using the LENGTH-CALIBRATED probability where available.

    authenticity_score is persisted and averaged into
    freelancer_trust_scores.authenticity_confidence. The raw probability is biased
    by review length, so using it charged a freelancer roughly 3 trust points for
    how briefly their clients happen to write - and landed hardest on people who
    write short, simple English. Falls back to raw when no calibration artifact is
    installed, which is a silent degradation: see INSTALL.md.
    """
    calibrated = ml_authenticity.get("fake_probability_calibrated")
    probability = calibrated if calibrated is not None else ml_authenticity["fake_probability"]
    return 1.0 - probability


def blend_authenticity(
    llm_authenticity_score: float,
    ml_authenticity: Dict,
    answer_groundedness: Optional[float],
    answer_text: str,
) -> float:
    """
    0.4 LLM + 0.4 ML + 0.2 groundedness, or a plain two-way average when the
    reviewer skipped the targeted question (it is optional at submit).
    """
    ml_component = ml_authenticity_component(ml_authenticity)

    if answer_groundedness is not None and (answer_text or "").strip():
        return round(
            0.4 * llm_authenticity_score + 0.4 * ml_component + 0.2 * answer_groundedness,
            3,
        )
    return round((llm_authenticity_score + ml_component) / 2, 3)


def resolve_flags(
    llm_analysis: Dict,
    ml_authenticity: Dict,
    ml_mismatch: Dict,
    avg_stars: float,
    base_flag_reasons: List[str],
) -> Tuple[bool, bool, List[str]]:
    """
    (is_flagged_fake, sentiment_mismatch, flag_reasons).

    Both flags require the LLM and the classical model to AGREE.

    For fake, that was justified by measurement: the ML classifier alone flagged 2
    of 8 genuine sample reviews, because short warm praise is exactly what it was
    trained to call templated.

    For mismatch, the rule used to be OR, letting either model set the flag alone.
    On held-out AGREEING (text, rating) pairs the disagreement classifier still
    returns P(disagree) of 0.21-0.29, so it fires often enough on genuine reviews
    that OR made the flag noisy.

    Single-model suspicion is never discarded - it becomes a flag reason so an admin
    reviewing the queue can see which model objected and why.
    """
    flag_reasons = list(base_flag_reasons)

    llm_fake = llm_analysis["is_flagged_fake"]
    ml_fake = ml_authenticity["is_likely_fake"]
    is_flagged_fake = llm_fake and ml_fake

    if ml_fake and not llm_fake:
        flag_reasons.append(
            f"Statistical model flagged generic/templated language, LLM did not "
            f"(fake_probability={ml_authenticity['fake_probability']})"
        )
    elif llm_fake and not ml_fake:
        flag_reasons.append("LLM flagged the review as fabricated, statistical model did not")

    llm_mismatch = llm_analysis["sentiment_mismatch"]
    ml_flagged_mismatch = ml_mismatch["is_mismatched"]
    sentiment_mismatch = llm_mismatch and ml_flagged_mismatch

    if ml_flagged_mismatch and not llm_mismatch:
        flag_reasons.append(
            f"Statistical model flagged a rating-text mismatch, LLM did not "
            f"(P(disagree)={ml_mismatch.get('disagreement_probability')}, "
            f"actual rating {avg_stars}★)"
        )
    elif llm_mismatch and not ml_flagged_mismatch:
        flag_reasons.append("LLM flagged a rating-text mismatch, statistical model did not")

    return is_flagged_fake, sentiment_mismatch, flag_reasons


def compute_overall_pass(
    llm_analysis: Dict,
    authenticity_score: float,
    is_flagged_fake: bool,
    is_flagged_coerced: bool,
    sentiment_mismatch: bool,
    avg_stars: float,
    sentiment_label: str,
) -> bool:
    """
    Whether the review auto-publishes.

    is_flagged_fake and is_flagged_coerced are vetoes rather than contributing
    signals: authenticity_score is a blend, so a review one model specifically
    caught could still average above 0.5 and publish. Since each flag already means
    a model concluded something is wrong, either holds the review back regardless of
    the blended score.

    A False here does NOT mean rejected. Step 7 of each pipeline routes to `flagged`
    (admin queue, reviewer told it is pending) or `suppressed` (high-confidence bad),
    and an unavailable LLM analysis is never suppressed - learning nothing about a
    review is not the same as judging it bad.
    """
    return (
        not llm_analysis.get("analysis_unavailable")
        and authenticity_score >= 0.5
        and not is_flagged_fake
        and not is_flagged_coerced
        and not (sentiment_mismatch and avg_stars == 5.0 and sentiment_label == "negative")
    )
