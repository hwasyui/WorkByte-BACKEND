import os
import sys
import json
import random
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.dm.dm_functions import DMFunctions
from ai_related.review_analysis.review_ai_functions import call_llm, MIN_REVIEWS_FOR_SUMMARY

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


async def analyze_client_review_full(
    overall_comment: str,
    freelancer_answer: str,
    avg_star_rating: float,
    client_name: str,
    performance_score_summary: Dict,
    message_thread: str,
    communication_star_rating: Optional[float] = None,  # raw 1-5 from client_review_ratings, shown as context only
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
    }

    comm_star_line = (
        f"- Freelancer's explicit communication star rating: {communication_star_rating:.1f} / 5\n"
        if communication_star_rating is not None
        else ""
    )

    user = (
        f"Review text:\n{overall_comment}\n{freelancer_answer}\n\n"
        f"Star rating given: {avg_star_rating:.1f} out of 5\n"
        f"Client name: {client_name}\n\n"
        "Objective signals about this client (0-1 scale):\n"
        f"- Responsiveness: {performance_score_summary.get('responsiveness', 'N/A')}\n"
        f"- Dispute fairness (1 - dispute rate): {performance_score_summary.get('dispute_fairness', 'N/A')}\n"
        f"{comm_star_line}"
        "\nMessage thread from the project (for context only):\n"
        f"{message_thread[:3000]}\n\n"
        "Assess the review for authenticity, coercion, and sentiment/rating mismatch. "
        "Base your analysis entirely on the data above, do not invent or assume anything.\n"
        "Return exactly one JSON object matching this schema:\n"
        f"{json.dumps(schema_description, ensure_ascii=False, indent=2)}"
    )

    try:
        result = await call_llm(system, user, json_mode=True)

        return {
            "sentiment_mismatch":  bool(result.get("sentiment_mismatch", False)),
            "authenticity_score":  float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake":     bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced":  bool(result.get("is_flagged_coerced", False)),
            "flag_reasons":        result.get("flag_reasons", []),
        }

    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Client review analysis failed: {str(e)}", level="ERROR")
        return {
            "sentiment_mismatch": False,
            "authenticity_score": 1.0,
            "is_flagged_fake":    False,
            "is_flagged_coerced": False,
            "flag_reasons":       [],
        }


def compute_client_responsiveness_score(client_user_id: str) -> float:
    """
    Symmetric counterpart to compute_responsiveness_score (freelancer side),
    aggregated live across ALL of this client's contracts rather than a
    per-contract snapshot table - avoids the same "single contract dominates
    the aggregate" bug fixed on the freelancer side (see calculate_trust_score).
    """
    try:
        db = get_db()
        contracts = db.fetch_data(
            "contract",
            conditions=[("client_id", "=", client_user_id)],
        )
        if not contracts:
            return 0.8

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
            return 0.8

        avg_hours = sum(all_gaps) / len(all_gaps)
        return round(max(0.0, min(1.0, 1.0 - (avg_hours / 48.0))), 3)
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client responsiveness: {str(e)}", level="ERROR")
        return 0.8


def compute_client_dispute_rate_score(client_user_id: str) -> float:
    """
    1 - (disputed contracts / total contracts). Measures how often working
    with this client escalated to a dispute - not who was at fault, since
    arbitration outcomes (approve/cancel/revise) don't cleanly attribute
    blame to either party. Dispute history lives as DM system-events
    (event_type='dispute_raised'), not a dedicated contract column - see
    ContractFunctions.raise_dispute.
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT COUNT(DISTINCT c.contract_id) AS total,
                   COUNT(DISTINCT CASE WHEN dm.metadata->>'type' = 'dispute_raised'
                                        THEN c.contract_id END) AS disputed
            FROM contract c
            LEFT JOIN dm_thread dt ON dt.contract_id = c.contract_id
            LEFT JOIN dm_message dm ON dm.thread_id = dt.thread_id
            WHERE c.client_id = :cid
              AND c.status IN ('completed', 'cancelled', 'disputed')
            """,
            {"cid": client_user_id},
        )
        if not rows or not rows[0]["total"]:
            return 1.0

        total = int(rows[0]["total"])
        disputed = int(rows[0]["disputed"] or 0)
        return round(max(0.0, 1.0 - (disputed / total)), 3)
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client dispute rate: {str(e)}", level="ERROR")
        return 1.0


def calculate_weighted_client_review_avg(client_user_id: str) -> Tuple[float, int]:
    """Recency + authenticity confidence-weighted average, mirroring
    calculate_weighted_review_avg on the freelancer side."""
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT crr.score, cr.published_at, cra.authenticity_score
            FROM client_review_ratings crr
            JOIN client_reviews cr ON cr.id = crr.client_review_id
            LEFT JOIN client_review_ai_analysis cra ON cra.client_review_id = cr.id
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_user_id},
        )
        if not rows:
            return 0.0, 0

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
            weight = recency_weight * authenticity_weight

            weighted_sum += float(row["score"]) * weight
            weight_total += weight

        weighted_avg = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

        count_rows = db.execute_query(
            """
            SELECT COUNT(DISTINCT cr.id) as cnt
            FROM client_reviews cr
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_user_id},
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0

        return weighted_avg, total
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing weighted client review avg: {str(e)}", level="ERROR")
        return 0.0, 0


def calculate_client_ai_trust_components(client_user_id: str) -> Dict:
    """Averages the review_ml model outputs across this client's published
    reviews - mirrors calculate_ai_trust_components on the freelancer side."""
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT cra.authenticity_score, cra.mismatch_severity, cra.sentiment_score
            FROM client_review_ai_analysis cra
            JOIN client_reviews cr ON cr.id = cra.client_review_id
            WHERE cr.client_id = :cid AND cr.status = 'published'
            """,
            {"cid": client_user_id},
        )
        if not rows:
            return {"authenticity_confidence": 1.0, "consistency_score": 1.0, "communication_sentiment": None}

        auth_scores = [float(r["authenticity_score"]) for r in rows if r["authenticity_score"] is not None]
        authenticity_confidence = round(sum(auth_scores) / len(auth_scores), 3) if auth_scores else 1.0

        severities = [float(r["mismatch_severity"]) for r in rows if r["mismatch_severity"] is not None]
        avg_severity = (sum(severities) / len(severities)) if severities else 0.0
        consistency_score = round(max(0.0, 1.0 - (avg_severity / 4.0)), 3)

        sentiments = [float(r["sentiment_score"]) for r in rows if r["sentiment_score"] is not None]
        # Normalize -1..1 sentiment average to 0..1 for the trust-score blend.
        communication_sentiment = (
            round(max(0.0, min(1.0, 0.5 + (sum(sentiments) / len(sentiments)) / 2.0)), 3)
            if sentiments else None
        )

        return {
            "authenticity_confidence": authenticity_confidence,
            "consistency_score": consistency_score,
            "communication_sentiment": communication_sentiment,
        }
    except Exception as e:
        logger("CLIENT_REVIEW_AI", f"Error computing client AI trust components: {str(e)}", level="ERROR")
        return {"authenticity_confidence": 1.0, "consistency_score": 1.0, "communication_sentiment": None}


def calculate_client_trust_score(
    weighted_review_avg: float,
    responsiveness_score: float,
    dispute_fairness_score: float,
    authenticity_confidence: float,
    consistency_score: float,
    communication_sentiment: Optional[float],
) -> float:
    """
    Client trust score - built entirely from what's actually observable on
    this platform (no payment tracking exists, so budget/payment-promptness
    cannot be inputs):

      35%  weighted_review_avg      - recency+authenticity-weighted star ratings from freelancers
      25%  responsiveness_score     - DM reply-gap based, symmetric to the freelancer side
      15%  dispute_fairness_score   - 1 - dispute rate (how often contracts escalated, not fault)
      10%  authenticity_confidence  - review_ml Model 1, averaged across their received reviews
      10%  consistency_score        - review_ml Model 2, averaged across their received reviews
       5%  communication_sentiment  - review_ml Model 3, averaged sentiment of received reviews
    """
    comm = communication_sentiment if communication_sentiment is not None else 0.5

    score  = (weighted_review_avg / 5.0) * 35
    score += responsiveness_score       * 25
    score += dispute_fairness_score     * 15
    score += authenticity_confidence    * 10
    score += consistency_score          * 10
    score += comm                       * 5

    return round(min(100.0, max(0.0, score)), 2)


# Profile-level AI review summary (client side) - symmetric counterpart to
# generate_freelancer_review_summary. Reuses the same MIN_REVIEWS_FOR_SUMMARY/
# SUMMARY_REGEN_INTERVAL thresholds rather than duplicating the constants.

def _fetch_published_client_review_texts(client_user_id: str) -> List[Dict]:
    db = get_db()
    return db.execute_query(
        """
        SELECT wc.overall_comment, wc.freelancer_answer
        FROM client_reviews cr
        JOIN client_review_written_content wc ON wc.client_review_id = cr.id
        WHERE cr.client_id = :cid AND cr.status = 'published'
        ORDER BY cr.published_at DESC
        """,
        {"cid": client_user_id},
    )


async def generate_client_review_summary(
    client_user_id: str,
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
        review_rows = _fetch_published_client_review_texts(client_user_id)
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
        logger("CLIENT_REVIEW_AI", f"Review summary generation failed for {client_user_id}: {str(e)}", level="ERROR")
        return None
