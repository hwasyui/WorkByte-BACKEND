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


# Gates on the blended authenticity score. Both were re-tuned when the ML term was
# removed from the blend - see blend_authenticity. The old values (0.50 / 0.30) were
# set against a distribution the ML constant inflated by roughly +0.14, so carrying
# them over unchanged would have silently tightened both gates.
#
# Measured on the 42 logged production judgments: the old blend had mean 0.767 and a
# floor of 0.498; the new one has mean 0.669. These values keep the publish and
# suppress rates in the same place. Re-tune against real traffic once there is more
# than one batch of it.
PASS_THRESHOLD = 0.42
SUPPRESS_THRESHOLD = 0.25


def ml_authenticity_component(ml_authenticity: Dict) -> float:
    """
    (1 - P(fake)) using the LENGTH-CALIBRATED probability where available.

    NO LONGER FEEDS THE BLEND, and nothing else currently calls it - see
    blend_authenticity for the audit that removed it. Kept rather than deleted
    because the judgment log still records the raw and calibrated probabilities on
    every review, and whatever replaces this model will need the same
    calibrated-preferring derivation to compare old scores against new ones.

    (The admin breakdown in admin_functions.py renders the probabilities directly
    from the logged dict; it does not go through here.)

    Falls back to raw when no calibration artifact is installed, which is a silent
    degradation: see INSTALL.md.
    """
    calibrated = ml_authenticity.get("fake_probability_calibrated")
    probability = calibrated if calibrated is not None else ml_authenticity["fake_probability"]
    return 1.0 - probability


def blend_authenticity(
    llm_authenticity_score: float,
    answer_groundedness: Optional[float],
    answer_text: str,
    analysis_unavailable: bool = False,
) -> Optional[float]:
    """
    0.8 LLM + 0.2 groundedness, or the LLM score alone when the reviewer skipped
    the targeted question (it is optional at submit).

    Returns None when the LLM analysis was unavailable - see below.

    Why the ML term was dropped
    ---------------------------
    The blend used to be 0.4 LLM + 0.4 ML + 0.2 groundedness. Audited over the 42
    logged production judgments, the ML term was not a signal:

        ML component (1 - calibrated P(fake))   mean 0.963   stdev 0.086
        LLM authenticity                        mean 0.737   stdev 0.276

    It contributed a near-constant +0.385 to every review. Its `is_likely_fake`
    never fired once across all 42 - the 0.75 threshold sits above the model's
    entire real-world output range - and on the three reviews the LLM did judge
    fabricated it returned P(fake) of 0.055/0.052/0.229, pulling one from 0.22 up
    to a passing 0.652. So it was not merely uninformative, it actively diluted.

    The root cause is the training label, not the fit. review_ml/ is trained on the
    Salminen corpus, where the positive class is GPT-2-generated Amazon product
    reviews. That target has drifted away from what this pipeline needs twice over:
    wrong domain (product vs. freelance service), and wrong question - "written by a
    2020 language model" is no longer a proxy for "dishonest", in either direction.

    Why None rather than 0.0 when the LLM is unavailable
    ----------------------------------------------------
    On a Groq outage analyze_review_full returns authenticity_score 0.0. Under the
    old blend the ML constant masked that as ~0.499; without it the pipeline would
    persist a hard 0.0 into a freelancer's permanent trust average, so an API
    outage would read as a maximally inauthentic review. overall_pass already holds
    these reviews on analysis_unavailable alone, so the score itself carries no
    decision - it only has to avoid poisoning the aggregate. Both consumers of the
    column already skip NULL (authenticity_confidence excludes it from the mean,
    weighted_review_avg falls back to weight 1.0), so None is the honest value.
    """
    if analysis_unavailable:
        return None

    if answer_groundedness is not None and (answer_text or "").strip():
        return round(0.8 * llm_authenticity_score + 0.2 * answer_groundedness, 3)
    return round(llm_authenticity_score, 3)


def resolve_flags(
    llm_analysis: Dict,
    ml_authenticity: Dict,
    ml_mismatch: Dict,
    avg_stars: float,
    base_flag_reasons: List[str],
) -> Tuple[bool, bool, List[str]]:
    """
    (is_flagged_fake, sentiment_mismatch, flag_reasons).

    is_flagged_fake is the LLM's call alone. It used to require the authenticity
    classifier to agree, which was justified at the time - that model alone flagged
    2 of 8 genuine sample reviews, since short warm praise is exactly what it was
    trained to call templated. But the AND gate turned out to disable the flag
    outright: across 42 logged production judgments the classifier's `is_likely_fake`
    never once fired, so `llm_fake and ml_fake` was structurally always False and the
    three reviews the LLM did flag were all silently cleared. A veto that can never
    fire is worse than a noisy one, because nothing surfaces it.

    sentiment_mismatch still requires both models to AGREE. That gate is doing real
    work and the reasoning behind it stands: the rule used to be OR, and on held-out
    AGREEING (text, rating) pairs the disagreement classifier still returns
    P(disagree) of 0.21-0.29, so it fires often enough on genuine reviews that OR
    made the flag noisy. Unlike the authenticity model, this one does fire in
    production, so the AND is a filter rather than an off switch.

    Single-model suspicion is never discarded - it becomes a flag reason so an admin
    reviewing the queue can see which model objected and why.
    """
    flag_reasons = list(base_flag_reasons)

    llm_fake = llm_analysis["is_flagged_fake"]
    ml_fake = ml_authenticity["is_likely_fake"]
    is_flagged_fake = llm_fake

    if ml_fake and not llm_fake:
        flag_reasons.append(
            f"Statistical model flagged generic/templated language, LLM did not "
            f"(fake_probability={ml_authenticity['fake_probability']}) - advisory "
            f"only, this model no longer sets the flag or feeds the score"
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
    authenticity_score: Optional[float],
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

    authenticity_score is None exactly when the analysis was unavailable, which the
    first clause already holds on. The None check is written out anyway rather than
    left to short-circuit evaluation, so that a future reordering of these clauses
    fails loudly instead of comparing None to a float.
    """
    if llm_analysis.get("analysis_unavailable") or authenticity_score is None:
        return False

    return (
        authenticity_score >= PASS_THRESHOLD
        and not is_flagged_fake
        and not is_flagged_coerced
        and not (sentiment_mismatch and avg_stars == 5.0 and sentiment_label == "negative")
    )


def should_suppress(llm_analysis: Dict, authenticity_score: Optional[float]) -> bool:
    """
    Whether a non-passing review is written off (`suppressed`) rather than sent to
    the admin queue (`flagged`).

    Suppression is the harsher outcome, so it needs a positive finding. An
    unavailable analysis is never suppressed: we learned nothing about the review,
    which is not the same as having judged it bad. That is also why None - which is
    exactly the unavailable case now that the score is nullable - returns False
    rather than comparing as low.

    Both pipelines had their own copy of this two-line rule, including the constant.
    Dropping the ML term moved the constant, which is the kind of change that used
    to have to be made twice.
    """
    if llm_analysis.get("analysis_unavailable") or authenticity_score is None:
        return False
    return authenticity_score < SUPPRESS_THRESHOLD
