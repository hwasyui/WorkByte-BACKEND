import os
import sys
import json
import asyncio
import random
import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from routes.dm.dm_functions import DMFunctions
from typing import Optional, Dict, Tuple, List
from datetime import datetime, timezone


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

DEFAULT_MODEL = "llama-3.1-8b-instant"
MODEL_FALLBACKS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
    "openai/gpt-oss-20b",
]

LLM_CONCURRENCY_LIMIT = 2
llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)
_supported_models_cache: Optional[set[str]] = None


def _groq_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }


async def fetch_supported_models(force_refresh: bool = False) -> set[str]:
    global _supported_models_cache

    if _supported_models_cache is not None and not force_refresh:
        return _supported_models_cache

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(GROQ_MODELS_URL, headers=_groq_headers())
            if resp.status_code >= 400:
                logger(
                    "REVIEW_AI",
                    f"Groq models endpoint error {resp.status_code}: {resp.text}",
                    level="ERROR",
                )
                resp.raise_for_status()

            payload = resp.json()
            data = payload.get("data", [])
            models = {
                item.get("id")
                for item in data
                if isinstance(item, dict) and item.get("id")
            }

            _supported_models_cache = models
            logger(
                "REVIEW_AI",
                f"Loaded {len(models)} supported Groq models",
                level="INFO",
            )
            return models
    except Exception as e:
        logger(
            "REVIEW_AI",
            f"Failed to load Groq models list: {str(e)}",
            level="WARNING",
        )
        return set(MODEL_FALLBACKS)


async def pick_best_model() -> str:
    supported = await fetch_supported_models()
    for model in MODEL_FALLBACKS:
        if model in supported:
            return model
    return DEFAULT_MODEL


def _is_model_error(status_code: Optional[int], response_text: str) -> bool:
    if status_code != 400:
        return False

    text = (response_text or "").lower()
    return (
        "model_decommissioned" in text
        or "decommissioned" in text
        or "model_not_found" in text
        or "invalid model" in text
        or "not supported" in text
    )


async def _post_chat_completion(
    client: httpx.AsyncClient,
    model: str,
    system_prompt: str,
    user_prompt: str,
    json_mode: bool = False,
):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
    }

    if json_mode:
        body["response_format"] = {"type": "json_object"}

    resp = await client.post(GROQ_CHAT_URL, headers=_groq_headers(), json=body)

    if resp.status_code >= 400:
        logger(
            "REVIEW_AI",
            f"Groq error {resp.status_code} with model {model}: {resp.text}",
            level="ERROR",
        )

    resp.raise_for_status()
    payload = resp.json()
    content = payload["choices"][0]["message"]["content"].strip()

    if json_mode:
        return json.loads(content)
    return content


async def call_llm(system_prompt: str, user_prompt: str, json_mode: bool = False):
    """Groq gateway with retries, model fallback, and decommission handling."""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")

    max_retries = 5
    base_delay_seconds = 2.0

    async with llm_semaphore:
        async with httpx.AsyncClient(timeout=30.0) as client:
            candidate_models = []
            supported = await fetch_supported_models()

            for model in MODEL_FALLBACKS:
                if model in supported and model not in candidate_models:
                    candidate_models.append(model)

            if not candidate_models:
                candidate_models = MODEL_FALLBACKS.copy()

            for model in candidate_models:
                for attempt in range(1, max_retries + 1):
                    try:
                        return await _post_chat_completion(
                            client=client,
                            model=model,
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            json_mode=json_mode,
                        )

                    except json.JSONDecodeError as e:
                        logger(
                            "REVIEW_AI",
                            f"Invalid JSON from model {model}: {str(e)}",
                            level="ERROR",
                        )
                        raise

                    except httpx.HTTPStatusError as e:
                        status_code = e.response.status_code if e.response is not None else None
                        response_text = e.response.text if e.response is not None else ""

                        if status_code == 429 and attempt < max_retries:
                            delay = base_delay_seconds * (2 ** (attempt - 1))
                            jitter = random.uniform(0.0, 0.5)
                            total_delay = delay + jitter
                            logger(
                                "REVIEW_AI",
                                f"Groq rate limit hit on {model}, retry {attempt}/{max_retries} after {total_delay:.1f}s",
                                level="WARNING",
                            )
                            await asyncio.sleep(total_delay)
                            continue

                        if _is_model_error(status_code, response_text):
                            logger(
                                "REVIEW_AI",
                                f"Model {model} unavailable, trying next fallback model",
                                level="WARNING",
                            )
                            break

                        raise

                    except httpx.RequestError as e:
                        if attempt < max_retries:
                            delay = base_delay_seconds * (2 ** (attempt - 1))
                            jitter = random.uniform(0.0, 0.5)
                            total_delay = delay + jitter
                            logger(
                                "REVIEW_AI",
                                f"Groq request error on {model}, retry {attempt}/{max_retries} after {total_delay:.1f}s: {str(e)}",
                                level="WARNING",
                            )
                            await asyncio.sleep(total_delay)
                            continue
                        raise

            raise RuntimeError("No supported Groq model succeeded")


def get_targeted_question(category: str) -> str:
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT question_text
            FROM ai_review_prompts
            WHERE project_category = :cat AND is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """,
            {"cat": category},
        )
        if rows:
            return rows[0]["question_text"]

        fallback = db.execute_query(
            """
            SELECT question_text
            FROM ai_review_prompts
            WHERE project_category = 'general' AND is_active = TRUE
            ORDER BY RANDOM()
            LIMIT 1
            """
        )
        return fallback[0]["question_text"] if fallback else "How satisfied are you with the overall project outcome?"
    except Exception as e:
        logger("REVIEW_AI", f"Error fetching targeted question: {str(e)}", level="ERROR")
        return "How satisfied are you with the overall project outcome?"


def compute_on_time_score(end_date, actual_completion_date) -> float:
    if not actual_completion_date:
        return 0.5
    if not end_date:
        return 0.8
    return 1.0 if actual_completion_date <= end_date else 0.5


def compute_revision_scores(contract_id: str) -> Tuple[int, float]:
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT COUNT(*) as cnt
            FROM contract_submission
            WHERE contract_id = :cid
              AND status IN ('revision_requested', 'superseded')
            """,
            {"cid": contract_id},
        )
        revision_count = int(rows[0]["cnt"]) if rows else 0
        score = round(1 / (1 + revision_count), 3)
        return revision_count, score
    except Exception as e:
        logger("REVIEW_AI", f"Error computing revision scores: {str(e)}", level="ERROR")
        return 0, 1.0


def compute_responsiveness_score(contract_id: str, freelancer_user_id: str) -> float:
    try:
        thread = DMFunctions.get_thread_by_contract_id(contract_id)
        if not thread:
            return 0.8

        messages, _, _ = DMFunctions.get_messages(thread["thread_id"], limit=1000)
        if not messages:
            return 0.8

        reply_gaps = []
        for i in range(len(messages) - 1):
            if str(messages[i]["sender_id"]) != freelancer_user_id:
                for j in range(i + 1, len(messages)):
                    if str(messages[j]["sender_id"]) == freelancer_user_id:
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


async def analyze_review_full(
    overall_comment: str,
    client_answer: str,
    avg_star_rating: float,
    freelancer_name: str,
    performance_score_summary: Dict,
    message_thread: str,
) -> Dict:
    system = (
        "You are a review analysis expert. "
        "Return valid JSON only. "
        "Do not include markdown fences, explanation, or extra text."
    )

    expected_schema = {
        "sentiment_score": -0.2,
        "sentiment_label": "neutral",
        "sentiment_mismatch": False,
        "authenticity_score": 0.95,
        "is_flagged_fake": False,
        "is_flagged_coerced": False,
        "flag_reasons": [],
        "bias_score": 0.1,
        "bias_flags": {
            "rating_vs_performance_inconsistency": False,
            "name_bias": False
        },
        "communication_summary": "Communication was timely, professional, and solution-focused."
    }

    user = (
        f"Review text:\n{overall_comment}\n{client_answer}\n\n"
        f"Star rating given: {avg_star_rating:.1f} out of 5\n"
        f"Freelancer name: {freelancer_name}\n\n"
        "Objective performance summary (0–1 scale):\n"
        f"- On-time delivery: {performance_score_summary.get('on_time', 'N/A')}\n"
        f"- Revision rate: {performance_score_summary.get('revision_rate', 'N/A')}\n"
        f"- Responsiveness: {performance_score_summary.get('responsiveness', 'N/A')}\n"
        f"- Work quality: {performance_score_summary.get('work_quality', 'N/A')}\n\n"
        "Message thread from the project:\n"
        f"{message_thread[:3000]}\n\n"
        "Assess the review for sentiment, authenticity, bias, and communication tone.\n"
        "Return exactly one JSON object with these keys and compatible value types:\n"
        f"{json.dumps(expected_schema, ensure_ascii=False)}"
    )

    try:
        result = await call_llm(system, user, json_mode=True)

        return {
            "sentiment_score": float(result.get("sentiment_score", 0.0)),
            "sentiment_label": result.get("sentiment_label", "neutral"),
            "sentiment_mismatch": bool(result.get("sentiment_mismatch", False)),
            "authenticity_score": float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake": bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced": bool(result.get("is_flagged_coerced", False)),
            "flag_reasons": result.get("flag_reasons", []),
            "bias_score": float(result.get("bias_score", 0.0)),
            "bias_flags": result.get("bias_flags", {}),
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

def calculate_trust_score(
    weighted_review_avg: float,
    revision_rate_score: float,
    responsiveness_score: float,
    communication_sentiment: Optional[float],
    conflict_score: Optional[float],
) -> float:
    communication_sentiment = float(communication_sentiment) if communication_sentiment is not None else 0.5
    conflict_score = float(conflict_score) if conflict_score is not None else 0.0

    score = (weighted_review_avg / 5.0) * 50
    score += float(revision_rate_score) * 20
    score += float(responsiveness_score) * 20
    score += communication_sentiment * 10

    if conflict_score > 0.7:
        score -= 5

    return round(min(100.0, max(0.0, score)), 2)

def calculate_weighted_review_avg(freelancer_user_id: str) -> Tuple[float, int]:
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT rr.score, r.published_at
            FROM review_ratings rr
            JOIN reviews r ON r.id = rr.review_id
            WHERE r.freelancer_id = :fid AND r.status = 'published'
            """,
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

            months_ago = max(0, (now - published_at).days / 30)
            weight = 1 / (1 + months_ago)
            weighted_sum += float(row["score"]) * weight
            weight_total += weight

        weighted_avg = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

        count_rows = db.execute_query(
            """
            SELECT COUNT(DISTINCT r.id) as cnt
            FROM reviews r
            WHERE r.freelancer_id = :fid AND r.status = 'published'
            """,
            {"fid": freelancer_user_id},
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0

        return weighted_avg, total

    except Exception as e:
        logger("REVIEW_AI", f"Error computing weighted avg: {str(e)}", level="ERROR")
        return 0.0, 0