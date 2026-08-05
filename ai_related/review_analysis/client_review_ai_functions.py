import os
import sys
import json
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from functions.profile_ids import user_id_for_client
from routes.dm.dm_functions import DMFunctions
from ai_related.review_analysis.review_ai_functions import (
    ANALYSIS_UNAVAILABLE_REASON,
    call_llm,
    MIN_REVIEWS_FOR_SUMMARY,
    _fmt_metric,
    _validate_generated_question,
    compute_repeat_weight,
    shrink_toward_prior,
)

# Client reviews get the same LLM analysis pass as freelancer reviews
# (analyze_client_review_full below), just with a prompt framed around what's
# actually observable for a client - requirement clarity and communication
# from the message thread - rather than the freelancer prompt's on-time
# delivery/revision-rate framing, which doesn't apply here. Shares call_llm
# with the freelancer side rather than duplicating the Groq gateway/retry logic.

# No per-category project taxonomy applies to clients the way it does to
# freelancer project categories, so these rotate directly rather than
# going through the ai_review_prompts table.
_CLIENT_REVIEW_QUESTIONS = [
    "How clear were the project requirements when you started?",
    "How would you describe this client's communication throughout the project?",
    "Was the scope of work stable, or did it change significantly after you started?",
    "How responsive was this client to your questions and submissions?",
]


def get_client_targeted_question() -> str:
    return random.choice(_CLIENT_REVIEW_QUESTIONS)


async def generate_client_targeted_question(
    job_title: str,
    role_title: str,
    job_description: str,
    contract_title: str,
    role_skills: List[str],
    revision_count: int,
) -> str:
    """Project-specific question for the freelancer about the client they worked
    for - counterpart to generate_targeted_question on the freelancer side, and
    the same reasoning: a question grounded in this project is hard to answer
    convincingly without having lived it, which is what makes the answer usable
    as an authenticity signal.

    Framed around what a freelancer can actually observe about a client -
    requirement clarity, scope stability, responsiveness - rather than delivery
    quality, which is the other side's concern. Falls back to the rotating
    _CLIENT_REVIEW_QUESTIONS list.
    """
    try:
        system = (
            "You write a single neutral review question for a freelancing platform. "
            "Return valid JSON only, no markdown fences or commentary."
        )

        skills_line = ", ".join(role_skills[:12]) if role_skills else "not specified"

        user = (
            "A freelancer is about to review the CLIENT they worked for on this "
            "completed project.\n\n"
            f"Job title: {job_title}\n"
            f"Contract title: {contract_title}\n"
            f"Role the freelancer filled: {role_title}\n"
            f"Required skills: {skills_line}\n"
            f"Revision rounds requested: {revision_count}\n"
            f"Job description as the client wrote it:\n{(job_description or '')[:1500]}\n\n"
            "Write ONE question for the freelancer about what this client was like "
            "to work with on this project.\n"
            "Rules:\n"
            "- Focus on the CLIENT's conduct: how clearly they specified requirements, "
            "whether scope stayed stable, how they handled feedback or revisions, how "
            "reachable they were. Do NOT ask about the freelancer's own work.\n"
            "- Reference a concrete aspect of THIS project (a named deliverable, "
            "technology, constraint, or requirement above).\n"
            "- Stay strictly neutral: do NOT presuppose the client was good or bad, "
            "and do not use evaluative adjectives.\n"
            "- Open-ended. Must NOT be answerable with yes or no. Start it with a word "
            "like How, What, Which, or Where.\n"
            "- One sentence, under 200 characters, ending in a question mark.\n"
            '- Address the freelancer as "you".\n'
            'Return exactly: {"question": "..."}'
        )

        result = await call_llm(system, user, json_mode=True)
        validated = _validate_generated_question(
            result.get("question") if isinstance(result, dict) else None
        )
        if validated:
            logger("CLIENT_REVIEW_AI", "Generated targeted client question", level="INFO")
            return validated

        logger(
            "CLIENT_REVIEW_AI",
            "Generated client question rejected by validation, using static list",
            level="WARNING",
        )
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Client question generation failed, using static list: {str(e)}", level="WARNING")

    return get_client_targeted_question()


async def analyze_client_review_full(
    overall_comment: str,
    freelancer_answer: str,
    avg_star_rating: float,
    client_name: str,
    performance_score_summary: Dict,
    message_thread: str,
    communication_star_rating: Optional[float] = None,  # raw 1-5 from client_review_ratings, shown as context only
    ai_question: str = "",
) -> Dict:
    """Client-side counterpart to analyze_review_full (review_ai_functions.py).

    Sentiment, communication_quality_score/summary, and the blend into
    communication_sentiment_score were removed: nothing on the client-review
    side ever persists or reads them - ClientReviewFunctions.save_ai_analysis
    has no columns for them, and calculate_client_ai_trust_components derives
    its own "communication_sentiment" straight from the ML sentiment_detector's
    persisted sentiment_score instead. They were pure computed-and-discarded
    LLM output. What's left is exactly what's actually consumed downstream:
    authenticity/coercion/mismatch judgment with real-world context that the
    review_ml models (trained on off-domain e-commerce review data) can't
    reliably provide alone."""
    system = (
        "You are a review analysis expert. "
        "Analyze the provided review data and return your own independent assessment as valid JSON only. "
        "Do not copy, echo, or mirror any values from the schema description, produce original analysis. "
        "Do not include markdown fences, explanation, or extra text."
    )

    schema_description = {
        "authenticity_score": "float between 0.0 and 1.0, likelihood the review is genuine and not fabricated",
        "is_flagged_fake": "boolean, true if review appears fabricated or templated",
        "is_flagged_coerced": "boolean, true if review appears pressured or coerced",
        "flag_reasons": "list of strings describing specific red flags, empty list if none",
        "sentiment_mismatch": "boolean, true if the review text's tone contradicts the star rating (e.g. negative text with 5 stars)",
        "answer_groundedness": (
            "float between 0.0 and 1.0. The question asked was generated from this specific "
            "project. Judge ONLY whether the answer actually engages with it using concrete, "
            "checkable specifics someone who lived this project would know. 1.0 = specific and "
            "clearly grounded in this project; 0.5 = on topic but generic; 0.0 = evasive, "
            "contradictory, or could have been written about any project without seeing this one. "
            "Judge specificity, NOT whether the answer is positive or negative."
        ),
    }

    comm_star_line = (
        f"- Freelancer's explicit communication star rating: {communication_star_rating:.1f} / 5\n"
        if communication_star_rating is not None
        else ""
    )

    # The question is project-generated, so groundedness can only be judged if the
    # model is told what was actually asked (see the freelancer-side note).
    qa_block = (
        f"Project-specific question the freelancer was asked:\n{ai_question}\n"
        f"Their answer:\n{freelancer_answer}\n\n"
        if ai_question
        else f"Freelancer's answer to a follow-up question:\n{freelancer_answer}\n\n"
    )

    user = (
        f"Review text:\n{overall_comment}\n\n"
        f"{qa_block}"
        f"Star rating given: {avg_star_rating:.1f} out of 5\n"
        f"Client name: {client_name}\n\n"
        "Objective signals about this client (0-1 scale). 'not recorded' means the "
        "platform has no measurement for it - that is missing data, NOT evidence "
        "against the review, and must not be treated as contradicting anything the "
        "reviewer says:\n"
        f"- Responsiveness: {_fmt_metric(performance_score_summary.get('responsiveness'))}\n"
        f"- Dispute fairness (1 - dispute rate): {_fmt_metric(performance_score_summary.get('dispute_fairness'))}\n"
        f"{comm_star_line}"
        "\nMessage thread from the project (for context only):\n"
        f"{message_thread[:3000]}\n\n"
        "Assess the review for authenticity, coercion, sentiment/rating mismatch, and how "
        "well the answer is grounded in this specific project. "
        "Base your analysis entirely on the data above, do not invent or assume anything.\n"
        "Return exactly one JSON object matching this schema:\n"
        f"{json.dumps(schema_description, ensure_ascii=False, indent=2)}"
    )

    try:
        result = await call_llm(system, user, json_mode=True)

        groundedness = result.get("answer_groundedness")
        return {
            "sentiment_mismatch":  bool(result.get("sentiment_mismatch", False)),
            "authenticity_score":  float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake":     bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced":  bool(result.get("is_flagged_coerced", False)),
            "flag_reasons":        result.get("flag_reasons", []),
            "answer_groundedness": max(0.0, min(1.0, float(groundedness))) if groundedness is not None else None,
            "analysis_unavailable": False,
        }

    except Exception as e:
        # Fail CLOSED - see the symmetric note in analyze_review_full.
        logger("CLIENT_REVIEW_AI", f"Client review analysis failed, failing closed: {str(e)}", level="ERROR")
        return {
            "sentiment_mismatch": False,
            "authenticity_score": 0.0,
            "is_flagged_fake":    False,
            "is_flagged_coerced": False,
            "flag_reasons":       [ANALYSIS_UNAVAILABLE_REASON],
            "answer_groundedness": None,
            "analysis_unavailable": True,
        }


def measure_client_responsiveness(client_id: str) -> Optional[float]:
    """
    The measurement behind compute_client_responsiveness_score, or None when
    there was nothing to measure - no contracts, no DM thread, or no reply pair
    to time.

    Split out because the 0.8 fallback below is a scoring convenience, not a
    measurement: charging an unmeasured client neither credit nor penalty in the
    trust score is reasonable, but rendering that same 0.8 to an admin under a
    heading that says "measured platform data" states as fact something the
    platform never observed. Callers that display the number, rather than score
    with it, want the None - the same distinction compute_on_time_score draws on
    the freelancer side.
    """
    try:
        db = get_db()
        contracts = db.fetch_data(
            "contract",
            conditions=[("client_id", "=", client_id)],
        )
        if not contracts:
            return None

        client_user_id = user_id_for_client(client_id)
        if not client_user_id:
            return None

        all_gaps = []
        for contract in contracts:
            thread = DMFunctions.get_thread_by_contract_id(str(contract["contract_id"]))
            if not thread:
                continue
            messages, _, _ = DMFunctions.get_messages(thread["thread_id"], limit=1000)
            if not messages:
                continue
            for i in range(len(messages) - 1):
                if str(messages[i]["sender_id"]) == client_user_id:
                    continue
                for j in range(i + 1, len(messages)):
                    if str(messages[j]["sender_id"]) == client_user_id:
                        gap = (messages[j]["sent_at"] - messages[i]["sent_at"]).total_seconds() / 3600
                        all_gaps.append(gap)
                        break

        if not all_gaps:
            return None

        avg_hours = sum(all_gaps) / len(all_gaps)
        return round(max(0.0, min(1.0, 1.0 - (avg_hours / 48.0))), 3)
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client responsiveness: {str(e)}", level="ERROR")
        return None


def compute_client_responsiveness_score(client_id: str) -> float:
    """
    Symmetric counterpart to compute_responsiveness_score (freelancer side),
    aggregated live across ALL of this client's contracts rather than a
    per-contract snapshot table - avoids the same "single contract dominates
    the aggregate" bug fixed on the freelancer side (see calculate_trust_score).

    Previously took a users.user_id and filtered contract.client_id with it.
    contract.client_id is a client.client_id, so the filter never matched and
    this silently returned its 0.8 fallback for every client. It now takes the
    client profile id the review tables key on, and resolves the user id only
    where it is genuinely needed: dm_message.sender_id.

    Scoring entry point: keeps the 0.8 neutral fallback its callers rely on.
    For display, use measure_client_responsiveness and render None as unmeasured.
    """
    measured = measure_client_responsiveness(client_id)
    return 0.8 if measured is None else measured


def measure_client_dispute_fairness(client_id: str) -> Optional[float]:
    """
    The measurement behind compute_client_dispute_rate_score, or None when the
    client has no closed contracts yet - see measure_client_responsiveness for
    why the scoring fallback (1.0 there) must not be shown as a measurement. A
    client with nothing finished has not proved a clean dispute record; there is
    simply nothing on file.
    """
    try:
        rows = get_db().execute_query(
            """
            SELECT COUNT(DISTINCT c.contract_id) AS total,
                   COUNT(DISTINCT CASE WHEN dm.metadata::jsonb->>'type' = 'dispute_raised'
                                        THEN c.contract_id END) AS disputed
            FROM contract c
            LEFT JOIN dm_thread dt ON dt.contract_id = c.contract_id
            LEFT JOIN dm_message dm ON dm.thread_id = dt.thread_id
            WHERE c.client_id = :cid
              AND c.status IN ('completed', 'cancelled', 'disputed')
            """,
            {"cid": client_id},
        )
        if not rows or not rows[0]["total"]:
            return None

        total = int(rows[0]["total"])
        disputed = int(rows[0]["disputed"] or 0)
        return round(max(0.0, 1.0 - (disputed / total)), 3)
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client dispute rate: {str(e)}", level="ERROR")
        return None


def compute_client_dispute_rate_score(client_id: str) -> float:
    """
    1 - (disputed contracts / total contracts). Measures how often working
    with this client escalated to a dispute - not who was at fault, since
    arbitration outcomes (approve/cancel/revise) don't cleanly attribute
    blame to either party. Dispute history lives as DM system-events
    (event_type='dispute_raised'), not a dedicated contract column - see
    ContractFunctions.raise_dispute.

    Had two independent faults before: it filtered contract.client_id by a
    users.user_id (never matched), and dm_message.metadata is TEXT holding a
    JSON string, so the bare `->>` raised "operator does not exist: text ->>
    unknown" and the except below turned every client's dispute-fairness score
    into a constant 1.0. The cast is explicit rather than relying on the column
    type, since metadata is written as json.dumps(...) by
    DMFunctions.send_system_event.

    Scoring entry point: keeps the 1.0 neutral fallback its callers rely on.
    For display, use measure_client_dispute_fairness and render None as unmeasured.
    """
    measured = measure_client_dispute_fairness(client_id)
    return 1.0 if measured is None else measured


def _client_record_gaps(client_id: str) -> Dict[str, Dict]:
    """
    Per published client review, how far its star ratings sit from this client's
    objective record. {client_review_id: {"inflation": float|None, "deflation": ...}}

    Shared by calculate_weighted_client_review_avg (which uses deflation to
    down-weight) and calculate_client_ai_trust_components (which uses inflation as a
    trust component), so the comparison runs once per caller rather than being
    duplicated with a chance to drift.

    Note the granularity caveat in review_consistency.compare_review_to_record: the
    client telemetry here is a LIFETIME aggregate, not per contract, so every review
    is compared against the same two numbers.
    """
    from ai_related.review_analysis.review_consistency import compare_review_to_record

    performance = {
        # The MEASURED figure, not the 0.8-fallback scoring one. compare_review_to_record
        # skips a dimension whose objective value is None and renormalises, on the
        # principle that missing data is neutral; handing it the fallback instead
        # manufactures a comparison against a number nobody observed, so a client with
        # no DM history on file would have every low responsiveness rating they receive
        # scored as deflation against a fictional 0.8.
        "responsiveness_score": measure_client_responsiveness(client_id),
        # Revisions are contract-level; aggregated across this client's contracts to
        # match the granularity of the responsiveness figure above.
        "revision_rate_score": measure_client_revision_rate(client_id),
        "on_time_score": None,  # a client has no delivery deadline to meet
    }

    rows = get_db().execute_query(
        """
        SELECT cr.id AS client_review_id, crr.category, crr.score
        FROM client_reviews cr
        JOIN client_review_ratings crr ON crr.client_review_id = cr.id
        WHERE cr.client_id = :cid AND cr.status = 'published'
        """,
        {"cid": client_id},
    )

    ratings_by_review = {}
    for row in rows or []:
        ratings_by_review.setdefault(str(row["client_review_id"]), []).append(
            {"category": row["category"], "score": row["score"]}
        )

    return {
        rid: compare_review_to_record(ratings, performance)
        for rid, ratings in ratings_by_review.items()
    }


def measure_client_revision_rate(client_id: str) -> Optional[float]:
    """Mean revision_rate_score across this client's contracts, or None if unknown."""
    try:
        rows = get_db().execute_query(
            """
            SELECT fps.revision_rate_score
            FROM freelancer_performance_scores fps
            JOIN contract c ON c.contract_id = fps.contract_id
            WHERE c.client_id = :cid AND fps.revision_rate_score IS NOT NULL
            """,
            {"cid": client_id},
        )
        values = [float(r["revision_rate_score"]) for r in rows or []]
        return round(sum(values) / len(values), 4) if values else None
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Could not average revision rate for {client_id}: {str(e)}",
               level="WARNING")
        return None


def calculate_weighted_client_review_avg(client_id: str) -> Tuple[float, int]:
    """Recency + authenticity confidence-weighted average, mirroring
    calculate_weighted_review_avg on the freelancer side."""
    try:
        from ai_related.review_analysis.review_ai_functions import _deflation_weight

        db = get_db()
        rows = db.execute_query(
            """
            SELECT cr.id AS client_review_id, crr.score, cr.published_at,
                   cra.authenticity_score,
                   DENSE_RANK() OVER (
                       PARTITION BY cr.reviewer_id ORDER BY cr.published_at, cr.id
                   ) AS pair_occurrence
            FROM client_review_ratings crr
            JOIN client_reviews cr ON cr.id = crr.client_review_id
            LEFT JOIN client_review_ai_analysis cra ON cra.client_review_id = cr.id
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_id},
        )
        if not rows:
            return 0.0, 0

        try:
            gaps = _client_record_gaps(client_id)
        except Exception as e:
            # Additive signal; a failure here must not break the star average.
            logger("CLIENT_REVIEW_AI", f"Record gaps unavailable for {client_id}: {str(e)}",
                   level="WARNING")
            gaps = {}

        now = datetime.now(timezone.utc)
        weighted_sum = 0.0
        weight_total = 0.0

        for row in rows:
            published_at = row["published_at"]
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            months_ago = max(0, (now - published_at).days / 30)
            recency_weight = 1 / (1 + months_ago)
            authenticity_weight = float(row["authenticity_score"]) if row["authenticity_score"] is not None else 1.0
            # Same anti-gaming weighting as the freelancer side: repeat reviews from
            # the same counterparty decay.
            repeat_weight = compute_repeat_weight(row["pair_occurrence"])
            # And the same capped down-weight for reviews materially harsher than the
            # record - max 25%, nothing below the threshold. See _deflation_weight for
            # why it is deliberately timid.
            gap = gaps.get(str(row["client_review_id"])) or {}
            deflation_weight = _deflation_weight(gap.get("deflation"))
            weight = recency_weight * authenticity_weight * repeat_weight * deflation_weight

            weighted_sum += float(row["score"]) * weight
            weight_total += weight

        weighted_avg = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

        count_rows = db.execute_query(
            """
            SELECT COUNT(DISTINCT cr.id) as cnt
            FROM client_reviews cr
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_id},
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0

        return weighted_avg, total
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing weighted client review avg: {str(e)}", level="ERROR")
        return 0.0, 0


def calculate_client_ai_trust_components(client_id: str) -> Dict:
    """Averages the review_ml model outputs across this client's published
    reviews - mirrors calculate_ai_trust_components on the freelancer side."""
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT cra.authenticity_score, cra.disagreement_probability, cra.sentiment_score
            FROM client_review_ai_analysis cra
            JOIN client_reviews cr ON cr.id = cra.client_review_id
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_id},
        )
        if not rows:
            return {"authenticity_confidence": 1.0, "consistency_score": 1.0,
                    "communication_sentiment": None, "record_consistency": None}

        auth_scores = [float(r["authenticity_score"]) for r in rows if r["authenticity_score"] is not None]
        authenticity_confidence = round(sum(auth_scores) / len(auth_scores), 3) if auth_scores else 1.0

        # disagreement_probability stores P(disagree) on a 0-1 scale, so it inverts
        # directly. See review_ai_functions.calculate_ai_trust_components for the
        # measurement behind removing the old 0-4 star-gap regressor.
        severities = [float(r["disagreement_probability"]) for r in rows
                      if r["disagreement_probability"] is not None]
        avg_disagreement = (sum(severities) / len(severities)) if severities else 0.0
        consistency_score = round(max(0.0, 1.0 - avg_disagreement), 3)

        sentiments = [float(r["sentiment_score"]) for r in rows if r["sentiment_score"] is not None]
        # Normalize -1..1 sentiment average to 0..1 for the trust-score blend.
        communication_sentiment = (
            round(max(0.0, min(1.0, 0.5 + (sum(sentiments) / len(sentiments)) / 2.0)), 3)
            if sentiments else None
        )

        # Component 4, mirroring the freelancer side. Wrapped separately so a
        # failure here cannot take out the three components that already worked.
        try:
            from ai_related.review_analysis.review_consistency import record_consistency_score

            gaps = _client_record_gaps(client_id)
            inflations = [g["inflation"] for g in gaps.values() if g["inflation"] is not None]
            record_consistency = (
                record_consistency_score(sum(inflations) / len(inflations)) if inflations else None
            )
        except Exception as e:
            logger("CLIENT_REVIEW_AI", f"Client record-consistency check failed: {str(e)}",
                   level="WARNING")
            record_consistency = None

        return {
            "authenticity_confidence": authenticity_confidence,
            "consistency_score": consistency_score,
            "communication_sentiment": communication_sentiment,
            "record_consistency": record_consistency,
        }
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client AI trust components: {str(e)}", level="ERROR")
        return {"authenticity_confidence": 1.0, "consistency_score": 1.0,
                "communication_sentiment": None, "record_consistency": None}


def calculate_client_trust_score(
    weighted_review_avg: float,
    responsiveness_score: Optional[float],
    dispute_fairness_score: Optional[float],
    authenticity_confidence: float,
    consistency_score: float,
    communication_sentiment: Optional[float],
    total_reviews: int = 0,
    coerced_ratio: float = 0.0,
    unfair_review_ratio: float = 0.0,
    record_consistency: Optional[float] = None,
) -> float:
    """
    Client trust score - built entirely from what's actually observable on
    this platform (no payment tracking exists, so budget/payment-promptness
    cannot be inputs):

      35%  weighted_review_avg      - recency+authenticity-weighted star ratings from freelancers
      25%  responsiveness_score     - DM reply-gap based, symmetric to the freelancer side
      15%  dispute_fairness_score   - 1 - dispute rate (how often contracts escalated, not fault)
       8%  authenticity_confidence  - Component 2, averaged across their received reviews
       6%  consistency_score        - Component 3, text vs rating on received reviews
       6%  record_consistency       - Component 4, their received ratings vs their own
                                       objective record. Same 8/6/6 integrity split as
                                       the freelancer side, so no single model dominates.
       5%  communication_sentiment  - Component 1 sentiment, averaged over received reviews
      -15  coerced_ratio            - proportional penalty, symmetric to the freelancer side
      -10  unfair_review_ratio      - share of the reviews this client WROTE that are
                                       materially harsher than the objective contract
                                       record (see calculate_review_fairness)

    Shrinkage and weight renormalization work exactly as on the freelancer side -
    see calculate_trust_score. A client pressuring a freelancer into a favourable
    review is as much a trust problem as the reverse, but the coercion flag was
    previously computed, stored in client_review_ai_analysis.is_flagged_coerced,
    and then never read by any score.

    unfair_review_ratio is a PENALTY rather than a weighted component on purpose.
    Every weighted input above describes reviews this client RECEIVED; this one
    describes their conduct as a reviewer. Mixing the two into one weighted average
    would conflate "is this a good client to work for" with "does this client review
    fairly". Modelling it the same way as coerced_ratio keeps those separate and
    matches the existing idiom for behavioural penalties.

    Capped at 10 rather than the coercion penalty's 15 because the underlying
    evidence is weaker: the telemetry behind it measures timeliness and
    responsiveness, not deliverable quality, so a harsh review can be entirely
    accurate and still register as deflated. See _deflation_weight for the full
    hazard.
    """
    effective_avg = shrink_toward_prior(weighted_review_avg, total_reviews)

    components = [
        (35.0, effective_avg / 5.0),
        (25.0, responsiveness_score),
        (15.0, dispute_fairness_score),
        (8.0, authenticity_confidence),
        (6.0, consistency_score),
        (6.0, record_consistency),
        (5.0, communication_sentiment),
    ]

    present = [(w, float(v)) for w, v in components if v is not None]
    total_weight = sum(w for w, _ in present)
    if total_weight <= 0:
        return 0.0

    score = 100.0 * sum(w * v for w, v in present) / total_weight
    score -= min(15.0, coerced_ratio * 30)
    score -= min(10.0, unfair_review_ratio * 20)

    return round(min(100.0, max(0.0, score)), 2)


def calculate_freelancer_review_fairness(freelancer_id: str) -> float:
    """
    The fraction of CLIENT reviews this freelancer has WRITTEN that are materially
    harsher than the client's objective record.

    Mirror of review_ai_functions.calculate_review_fairness, which does the same for
    clients reviewing freelancers. Both halves of the platform can write unfair
    reviews, so both carry the penalty - the freelancer side was missing it.

    Weaker evidence than the freelancer-side equivalent, for two reasons: the client
    telemetry it compares against is a lifetime aggregate rather than per contract
    (see review_consistency.compare_review_to_record), and a client's record has only
    responsiveness and revision churn in it. Hence the smaller cap in
    calculate_trust_score.

    Returns 0.0 when nothing was comparable, so it never penalises on absent evidence.
    """
    try:
        from ai_related.review_analysis.review_ai_functions import DEFLATION_WEIGHT_THRESHOLD
        from ai_related.review_analysis.review_consistency import compare_review_to_record

        rows = get_db().execute_query(
            """
            SELECT cr.id AS client_review_id, cr.client_id, crr.category, crr.score
            FROM client_reviews cr
            JOIN client_review_ratings crr ON crr.client_review_id = cr.id
            WHERE cr.reviewer_id = :fid AND cr.status = 'published'
            """,
            {"fid": freelancer_id},
        )
        if not rows:
            return 0.0

        # Grouped by review, but the telemetry is per CLIENT, so it is fetched once
        # per distinct client rather than once per review.
        by_review = {}
        for row in rows:
            rid = str(row["client_review_id"])
            entry = by_review.setdefault(rid, {"client_id": str(row["client_id"]), "ratings": []})
            entry["ratings"].append({"category": row["category"], "score": row["score"]})

        performance_cache = {}
        comparable, unfair = 0, 0
        for entry in by_review.values():
            cid = entry["client_id"]
            if cid not in performance_cache:
                performance_cache[cid] = {
                    # Measured, not the scoring fallback - see _client_record_gaps. An
                    # unmeasured client must not make a reviewer look unfair.
                    "responsiveness_score": measure_client_responsiveness(cid),
                    "revision_rate_score": measure_client_revision_rate(cid),
                    "on_time_score": None,
                }
            result = compare_review_to_record(entry["ratings"], performance_cache[cid])
            if result["deflation"] is None:
                continue
            comparable += 1
            if result["deflation"] >= DEFLATION_WEIGHT_THRESHOLD:
                unfair += 1

        if not comparable:
            return 0.0
        return round(unfair / comparable, 3)

    except Exception as e:
        logger("CLIENT_REVIEW_AI",
               f"Error computing review fairness for freelancer {freelancer_id}: {str(e)}",
               level="ERROR")
        return 0.0


def calculate_client_coerced_ratio(client_id: str) -> float:
    """Share of this client's analysed reviews that were flagged as coerced.

    Counts every review the pipeline analysed, not just published ones - a coerced
    review is held back from publishing, so a published-only filter would make this
    structurally zero. Same reasoning as the freelancer-side fix in
    calculate_aggregate_performance.
    """
    try:
        rows = get_db().execute_query(
            """
            SELECT cra.is_flagged_coerced
            FROM client_review_ai_analysis cra
            JOIN client_reviews cr ON cr.id = cra.client_review_id
            WHERE cr.client_id = :cid
              AND cr.status IN ('published', 'flagged', 'suppressed')
            """,
            {"cid": client_id},
        )
        if not rows:
            return 0.0
        coerced = sum(1 for r in rows if r["is_flagged_coerced"])
        return round(coerced / len(rows), 3)
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client coerced ratio: {str(e)}", level="ERROR")
        return 0.0


# Profile-level AI review summary (client side) - symmetric counterpart to
# generate_freelancer_review_summary. Reuses the same MIN_REVIEWS_FOR_SUMMARY/
# SUMMARY_REGEN_INTERVAL thresholds rather than duplicating the constants.

def _fetch_published_client_review_texts(client_id: str) -> List[Dict]:
    db = get_db()
    return db.execute_query(
        """
        SELECT wc.overall_comment, wc.freelancer_answer
        FROM client_reviews cr
        JOIN client_review_written_content wc ON wc.client_review_id = cr.id
        WHERE cr.client_id = :cid AND cr.status = 'published'
        ORDER BY cr.published_at DESC
        """,
        {"cid": client_id},
    )


async def generate_client_review_summary(
    client_id: str,
    client_name: str,
) -> Optional[str]:
    """
    Synthesizes all of a client's PUBLISHED reviews (written by freelancers
    they've hired) into a short profile summary for freelancers deciding
    whether to work with them. Only reads published reviews - harmful text
    and fake/coerced reviews were already filtered out by the publish gate.
    Returns None below MIN_REVIEWS_FOR_SUMMARY reviews.

    Unlike the freelancer summary, there are no skill tags to ground this in -
    the content instead centers on what freelancers can actually observe about
    a client: requirement clarity, communication, responsiveness, scope
    stability, and dispute fairness (the same categories client_review_ratings
    already scores).
    """
    try:
        review_rows = _fetch_published_client_review_texts(client_id)
        if len(review_rows) < MIN_REVIEWS_FOR_SUMMARY:
            return None

        reviews_block = "\n\n".join(
            f"- {(row.get('overall_comment') or '').strip()} {(row.get('freelancer_answer') or '').strip()}".strip()
            for row in review_rows
        )

        system = (
            "You are summarizing a client's published reviews (written by freelancers "
            "who worked for them) for freelancers deciding whether to take a job with "
            "this client. Base the summary strictly on the review text provided - do "
            "not invent details that aren't actually stated. Do not write pure marketing "
            "copy: if a criticism repeats across multiple reviews, include it."
        )
        user = (
            f"Client name: {client_name}\n\n"
            f"Published freelancer reviews ({len(review_rows)} total):\n{reviews_block[:6000]}\n\n"
            "Write a 3-4 sentence summary covering: (1) overall impression of working "
            "with this client, (2) specific recurring strengths (e.g. clear requirements, "
            "responsive, fair with scope/disputes), (3) an honest recurring critique ONLY "
            "if at least two reviews raise something similar - otherwise omit it entirely, "
            "(4) what kind of freelancer or project this client seems best matched with, "
            "if inferable. Plain prose only, no markdown, no bullet points."
        )

        summary = await call_llm(system, user, json_mode=False)
        return summary.strip() if summary else None

    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Review summary generation failed for {client_id}: {str(e)}", level="ERROR")
        return None
