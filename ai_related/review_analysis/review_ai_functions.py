import os
import sys
import json
import asyncio
import random
import httpx
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Optional, Dict, Tuple
from datetime import datetime, timezone

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"
MODEL          = "gpt-4o-mini"   # fast + cheap, ideal for classification & analysis
LLM_CONCURRENCY_LIMIT = 2
llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)


async def call_llm(system_prompt: str, user_prompt: str, json_mode: bool = False):
    """Single centralized async OpenAI gateway with concurrency and rate-limit backoff."""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": 0.1,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    max_retries = 5
    base_delay_seconds = 2.0

    async with llm_semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(1, max_retries + 1):
                try:
                    resp = await client.post(OPENAI_URL, headers=headers, json=body)
                    resp.raise_for_status()
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    if json_mode:
                        return json.loads(content)
                    return content
                except httpx.HTTPStatusError as e:
                    status_code = e.response.status_code if e.response is not None else None
                    if status_code == 429 and attempt < max_retries:
                        delay = base_delay_seconds * (2 ** (attempt - 1))
                        jitter = random.uniform(0.0, 0.5)
                        total_delay = delay + jitter
                        logger(
                            "REVIEW_AI",
                            f"OpenAI rate limit hit, retry {attempt}/{max_retries} after {total_delay:.1f}s",
                            level="WARNING",
                        )
                        await asyncio.sleep(total_delay)
                        continue
                    raise
                except httpx.RequestError as e:
                    if attempt < max_retries:
                        delay = base_delay_seconds * (2 ** (attempt - 1))
                        jitter = random.uniform(0.0, 0.5)
                        total_delay = delay + jitter
                        logger(
                            "REVIEW_AI",
                            f"OpenAI request error, retry {attempt}/{max_retries} after {total_delay:.1f}s: {str(e)}",
                            level="WARNING",
                        )
                        await asyncio.sleep(total_delay)
                        continue
                    raise


# ── Step 3 ────────────────────────────────────────────────────────────────────

def get_targeted_question(category: str) -> str:
    """Pick a random active prompt for the given category from ai_review_prompts."""
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT question_text FROM ai_review_prompts
               WHERE project_category = :cat AND is_active = TRUE
               ORDER BY RANDOM() LIMIT 1""",
            {"cat": category},
        )
        if rows:
            return rows[0]["question_text"]
        fallback = db.execute_query(
            """SELECT question_text FROM ai_review_prompts
               WHERE project_category = 'general' AND is_active = TRUE
               ORDER BY RANDOM() LIMIT 1""",
        )
        return fallback[0]["question_text"] if fallback else "How satisfied are you with the overall project outcome?"
    except Exception as e:
        logger("REVIEW_AI", f"Error fetching targeted question: {str(e)}", level="ERROR")
        return "How satisfied are you with the overall project outcome?"


# ── Step 4a ───────────────────────────────────────────────────────────────────

def compute_on_time_score(end_date, actual_completion_date) -> float:
    """1.0 if on or before end_date, 0.5 if late, 0.8 if no end_date was set."""
    if not actual_completion_date:
        return 0.5
    if not end_date:
        return 0.8
    return 1.0 if actual_completion_date <= end_date else 0.5


# ── Step 4b ───────────────────────────────────────────────────────────────────

def compute_revision_scores(contract_id: str) -> Tuple[int, float]:
    """
    Returns (revision_count, revision_rate_score).
    Formula: score = 1 / (1 + revision_count)
    0 revisions → 1.0 | 1 → 0.5 | 2 → 0.33 | 3 → 0.25
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT COUNT(*) as cnt FROM contract_submission
               WHERE contract_id = :cid AND status = 'revision_requested'""",
            {"cid": contract_id},
        )
        revision_count = int(rows[0]["cnt"]) if rows else 0
        score = round(1 / (1 + revision_count), 3)
        return revision_count, score
    except Exception as e:
        logger("REVIEW_AI", f"Error computing revision scores: {str(e)}", level="ERROR")
        return 0, 1.0


# ── Step 4c ───────────────────────────────────────────────────────────────────

def compute_responsiveness_score(contract_id: str, freelancer_user_id: str) -> float:
    """
    Pairs each client message with the next freelancer reply.
    Averages the gap in hours. Normalizes: 0h=1.0, 48h+=0.0
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT sender_id, sent_at FROM message
               WHERE contract_id = :cid AND deleted_at IS NULL
               ORDER BY sent_at ASC""",
            {"cid": contract_id},
        )
        if not rows:
            return 0.8

        messages = [{"sender_id": str(r["sender_id"]), "sent_at": r["sent_at"]} for r in rows]
        reply_gaps = []

        for i in range(len(messages) - 1):
            if messages[i]["sender_id"] != freelancer_user_id:
                for j in range(i + 1, len(messages)):
                    if messages[j]["sender_id"] == freelancer_user_id:
                        gap = (messages[j]["sent_at"] - messages[i]["sent_at"]).total_seconds() / 3600
                        reply_gaps.append(gap)
                        break

        if not reply_gaps:
            return 0.8

        avg_hours = sum(reply_gaps) / len(reply_gaps)
        score = max(0.0, min(1.0, 1.0 - (avg_hours / 48.0)))
        return round(score, 3)
    except Exception as e:
        logger("REVIEW_AI", f"Error computing responsiveness: {str(e)}", level="ERROR")
        return 0.8


# ── Step 4d ───────────────────────────────────────────────────────────────────

# ── Step 4e ───────────────────────────────────────────────────────────────────

# ── Step 6a ───────────────────────────────────────────────────────────────────

async def analyze_review_full(
    overall_comment: str,
    client_answer: str,
    avg_star_rating: float,
    freelancer_name: str,
    performance_score_summary: Dict,
    message_thread: str,
) -> Dict:
    system = "You are a review analysis expert. Return valid JSON only."
    user = (
        f'Review text: "{overall_comment} {client_answer}"\n'
        f"Star rating given: {avg_star_rating:.1f} out of 5\n"
        f"Freelancer name: {freelancer_name}\n\n"
        "Objective performance summary (0–1 scale):\n"
        f"- On-time delivery:   {performance_score_summary.get('on_time', 'N/A')}\n"
        f"- Revision rate:      {performance_score_summary.get('revision_rate', 'N/A')}\n"
        f"- Responsiveness:     {performance_score_summary.get('responsiveness', 'N/A')}\n"
        f"- Work quality:       {performance_score_summary.get('work_quality', 'N/A')}\n\n"
        "Message thread from the project:\n"
        f"{message_thread[:3000]}\n\n"
        "Assess the review for sentiment, authenticity, bias, and communication tone. "
        "Return JSON with exactly these keys:\n"
        "{\n"
        '  "sentiment_score": <float -1.0 to 1.0>,\n'
        '  "sentiment_label": <"positive" | "neutral" | "negative">,\n'
        '  "sentiment_mismatch": <true/false>,\n'
        '  "authenticity_score": <float 0.0-1.0, 1.0=clearly genuine>,\n'
        '  "is_flagged_fake": <true/false>,\n'
        '  "is_flagged_coerced": <true/false>,\n'
        '  "flag_reasons": [<list of reason strings if flagged, else []>],\n'
        '  "bias_score": <float 0.0-1.0, 1.0=strong bias detected>,\n'
        '  "bias_flags": {\n'
        '    "rating_vs_performance_inconsistency": <true/false>,\n'
        '    "name_bias": <true/false>\n'
        "  },\n"
        '  "communication_summary": <one sentence summary of project communication>\n'
        "}"
    )
    try:
        result = await call_llm(system, user, json_mode=True)
        return {
            "sentiment_score":    float(result.get("sentiment_score", 0.0)),
            "sentiment_label":    result.get("sentiment_label", "neutral"),
            "sentiment_mismatch": bool(result.get("sentiment_mismatch", False)),
            "authenticity_score": float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake":    bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced": bool(result.get("is_flagged_coerced", False)),
            "flag_reasons":       result.get("flag_reasons", []),
            "bias_score":         float(result.get("bias_score", 0.0)),
            "bias_flags":         result.get("bias_flags", {}),
            "communication_summary": result.get("communication_summary", ""),
            "overall_pass": (
                float(result.get("authenticity_score", 1.0)) >= 0.5
                and float(result.get("bias_score", 0.0)) <= 0.6
                and not (
                    bool(result.get("sentiment_mismatch", False))
                    and avg_star_rating == 5.0
                    and result.get("sentiment_label", "neutral") == "negative"
                )
            ),
        }
    except Exception as e:
        logger("REVIEW_AI", f"Review analysis failed: {str(e)}", level="ERROR")
        return {
            "sentiment_score": 0.0,
            "sentiment_label": "neutral",
            "sentiment_mismatch": False,
            "authenticity_score": 1.0,
            "is_flagged_fake": False,
            "is_flagged_coerced": False,
            "flag_reasons": [],
            "bias_score": 0.0,
            "bias_flags": {},
            "communication_summary": "Analysis unavailable.",
            "overall_pass": True,
        }


# ── Step 8 helpers ────────────────────────────────────────────────────────────

def calculate_trust_score(
    weighted_review_avg: float,
    work_quality_score: Optional[float],
    revision_rate_score: float,
    responsiveness_score: float,
    communication_sentiment: Optional[float],
    conflict_score: Optional[float],
) -> float:
    """
    Formula (0–100):
      weighted_review_avg / 5.0  × 35  (client star ratings, recency-weighted)
      + work_quality_score       × 25  (AI file analysis)
      + revision_rate_score      × 15  (fewer revisions = better)
      + responsiveness_score     × 15  (average reply speed)
      + communication_sentiment  × 10  (message tone)
      - 5  if conflict_score > 0.7     (conflict penalty)
    """
    work_quality_score = float(work_quality_score) if work_quality_score is not None else 0.7
    communication_sentiment = float(communication_sentiment) if communication_sentiment is not None else 0.8
    conflict_score = float(conflict_score) if conflict_score is not None else 0.0

    score  = (weighted_review_avg / 5.0) * 35
    score += work_quality_score * 25
    score += float(revision_rate_score) * 15
    score += float(responsiveness_score) * 15
    score += communication_sentiment * 10
    if conflict_score > 0.7:
        score -= 5
    return round(min(100.0, max(0.0, score)), 2)


def calculate_weighted_review_avg(freelancer_user_id: str) -> Tuple[float, int]:
    """
    Recency-weighted average of all published star ratings for this freelancer.
    weight = 1 / (1 + months_since_review)
    Returns (weighted_avg, total_reviews_count).
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT rr.score, r.published_at
               FROM review_ratings rr
               JOIN reviews r ON r.id = rr.review_id
               WHERE r.freelancer_id = :fid AND r.status = 'published'""",
            {"fid": freelancer_user_id},
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
            months_ago    = max(0, (now - published_at).days / 30)
            weight        = 1 / (1 + months_ago)
            weighted_sum += float(row["score"]) * weight
            weight_total += weight

        weighted_avg = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

        count_rows = db.execute_query(
            """SELECT COUNT(DISTINCT r.id) as cnt FROM reviews r
               WHERE r.freelancer_id = :fid AND r.status = 'published'""",
            {"fid": freelancer_user_id},
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0
        return weighted_avg, total
    except Exception as e:
        logger("REVIEW_AI", f"Error computing weighted avg: {str(e)}", level="ERROR")
        return 0.0, 0