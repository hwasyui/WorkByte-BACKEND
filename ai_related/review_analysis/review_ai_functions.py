import os
import sys
import json
import asyncio
import httpx
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Optional, Dict, Tuple
from datetime import datetime, timezone

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_URL     = "https://api.openai.com/v1/chat/completions"
MODEL          = "gpt-4o-mini"   # fast + cheap, ideal for classification & analysis


async def call_openai(system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
    """Single reusable async OpenAI call used by every AI function below."""
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(OPENAI_URL, headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


# ── Step 2 ────────────────────────────────────────────────────────────────────

async def classify_project_category(
    job_title: str,
    role_title: str,
    job_description: str,
) -> str:
    """
    Uses GPT-4o-mini to classify the project into one of 9 categories.
    Falls back to 'general' if classification fails or returns unexpected value.
    """
    valid_categories = {
        "mobile_dev", "web_dev", "ui_ux_design", "graphic_design",
        "copywriting", "backend_dev", "data_analytics", "video_editing", "general",
    }
    system = (
        "You are a project classifier for a freelance marketplace. "
        "Return ONLY one category label with no explanation or punctuation."
    )
    user = (
        f"Job Title: {job_title}\n"
        f"Role Title: {role_title}\n"
        f"Job Description: {job_description[:800]}\n\n"
        "Classify into exactly one of:\n"
        "mobile_dev, web_dev, ui_ux_design, graphic_design, "
        "copywriting, backend_dev, data_analytics, video_editing, general"
    )
    try:
        result = await call_openai(system, user)
        category = result.lower().strip()
        return category if category in valid_categories else "general"
    except Exception as e:
        logger("REVIEW_AI", f"Category classification failed: {str(e)}", level="ERROR")
        return "general"


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

async def analyze_message_thread(contract_id: str) -> Dict:
    """
    Sends message thread to GPT-4o-mini.
    Returns communication_sentiment_score, conflict_score, communication_summary.
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT sender_id, message_text, sent_at FROM message
               WHERE contract_id = :cid AND message_type = 'user' AND deleted_at IS NULL
               ORDER BY sent_at ASC LIMIT 60""",
            {"cid": contract_id},
        )
        if not rows:
            return {"communication_sentiment_score": 0.8, "conflict_score": 0.0, "communication_summary": "No messages found."}

        thread = "\n".join([f"[{r['sender_id']}]: {r['message_text']}" for r in rows])

        system = "You are a professional communication analyst. Always return valid JSON."
        user = (
            "Analyse this message thread between a client and freelancer on a completed project.\n\n"
            f"{thread[:3000]}\n\n"
            "Return a JSON object with exactly these keys:\n"
            "{\n"
            '  "communication_sentiment": <float 0.0-1.0, 1.0=very professional and positive>,\n'
            '  "conflict_score": <float 0.0-1.0, 1.0=heavy conflict or frustration detected>,\n'
            '  "communication_summary": <one sentence describing the working relationship>\n'
            "}"
        )
        result = json.loads(await call_openai(system, user, json_mode=True))
        return {
            "communication_sentiment_score": float(result.get("communication_sentiment", 0.8)),
            "conflict_score":                float(result.get("conflict_score", 0.0)),
            "communication_summary":         result.get("communication_summary", ""),
        }
    except Exception as e:
        logger("REVIEW_AI", f"Message thread analysis failed: {str(e)}", level="ERROR")
        return {"communication_sentiment_score": 0.8, "conflict_score": 0.0, "communication_summary": "Analysis unavailable."}


# ── Step 4e ───────────────────────────────────────────────────────────────────

async def analyze_submitted_files(contract_id: str) -> Dict:
    """
    Fetches files from the latest approved contract_submission.
    Routes to text analysis or vision model based on mime_type.
    Returns work_quality_score (0–1) and work_quality_notes.
    """
    try:
        db = get_db()
        submission_rows = db.execute_query(
            """SELECT submission_id FROM contract_submission
               WHERE contract_id = :cid AND status = 'approved'
               ORDER BY submitted_at DESC LIMIT 1""",
            {"cid": contract_id},
        )
        if not submission_rows:
            return {"work_quality_score": None, "work_quality_notes": "No approved submission found."}

        submission_id = str(submission_rows[0]["submission_id"])
        file_rows = db.execute_query(
            """SELECT file_url, file_name, mime_type FROM contract_submission_file
               WHERE submission_id = :sid LIMIT 5""",
            {"sid": submission_id},
        )
        if not file_rows:
            return {"work_quality_score": None, "work_quality_notes": "No files in submission."}

        scores = []
        notes  = []

        for file in file_rows:
            mime      = str(file.get("mime_type") or "")
            file_name = str(file.get("file_name") or "")
            file_url  = str(file.get("file_url")  or "")

            # Text-based files
            if any(t in mime for t in ["text/", "application/json", "application/pdf"]):
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp    = await client.get(file_url)
                        content = resp.text[:3000]
                    system = "You are a professional freelance work quality assessor. Return valid JSON only."
                    user = (
                        f"Assess the quality of this freelance deliverable.\n"
                        f"File: {file_name} ({mime})\n\nContent:\n{content}\n\n"
                        'Return JSON: { "score": <float 0.0-1.0>, "notes": <one sentence explanation> }'
                    )
                    result = json.loads(await call_openai(system, user, json_mode=True))
                    scores.append(float(result.get("score", 0.7)))
                    notes.append(result.get("notes", ""))
                except Exception:
                    scores.append(0.7)
                    notes.append(f"Could not analyse {file_name}.")

            # Image files — use vision model
            elif mime.startswith("image/"):
                try:
                    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
                    body = {
                        "model": "gpt-4o-mini",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": (
                                    "You are a design quality assessor. Rate this image as a freelance deliverable.\n"
                                    'Return JSON: { "score": <float 0.0-1.0>, "notes": <one sentence> }'
                                )},
                                {"type": "image_url", "image_url": {"url": file_url}},
                            ],
                        }],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 200,
                    }
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp   = await client.post(OPENAI_URL, headers=headers, json=body)
                        result = json.loads(resp.json()["choices"][0]["message"]["content"])
                    scores.append(float(result.get("score", 0.7)))
                    notes.append(result.get("notes", ""))
                except Exception:
                    scores.append(0.7)
                    notes.append(f"Could not analyse image {file_name}.")

            # Video or other unsupported types — skip
            else:
                notes.append(f"{file_name}: manual review recommended.")

        if not scores:
            return {"work_quality_score": None, "work_quality_notes": "; ".join(notes) or "Unsupported file types."}

        return {
            "work_quality_score": round(sum(scores) / len(scores), 3),
            "work_quality_notes": "; ".join(notes),
        }
    except Exception as e:
        logger("REVIEW_AI", f"File analysis failed: {str(e)}", level="ERROR")
        return {"work_quality_score": None, "work_quality_notes": "Analysis failed."}


# ── Step 6a ───────────────────────────────────────────────────────────────────

async def analyze_sentiment(
    overall_comment: str,
    client_answer: str,
    avg_star_rating: float,
) -> Dict:
    system = "You are a sentiment analysis expert. Return valid JSON only."
    user = (
        f'Review text: "{overall_comment} {client_answer}"\n'
        f"Star rating given: {avg_star_rating:.1f} out of 5\n\n"
        "Return JSON:\n"
        "{\n"
        '  "sentiment_score": <float -1.0 to 1.0>,\n'
        '  "sentiment_label": <"positive" | "neutral" | "negative">,\n'
        '  "sentiment_mismatch": <true if text tone clearly contradicts the star rating, else false>\n'
        "}"
    )
    try:
        result = json.loads(await call_openai(system, user, json_mode=True))
        return {
            "sentiment_score":    float(result.get("sentiment_score", 0.0)),
            "sentiment_label":    result.get("sentiment_label", "neutral"),
            "sentiment_mismatch": bool(result.get("sentiment_mismatch", False)),
        }
    except Exception as e:
        logger("REVIEW_AI", f"Sentiment analysis failed: {str(e)}", level="ERROR")
        return {"sentiment_score": 0.0, "sentiment_label": "neutral", "sentiment_mismatch": False}


# ── Step 6b ───────────────────────────────────────────────────────────────────

async def check_authenticity(overall_comment: str) -> Dict:
    system = "You are a review authenticity expert. Return valid JSON only."
    user = (
        f'Review text: "{overall_comment}"\n\n'
        "Detect red flags for fake, coerced, or copy-pasted reviews:\n"
        "- Extremely generic text with no project-specific details\n"
        "- Suspiciously templated or formal language\n"
        "- Unrealistic all-positive tone with zero specifics\n\n"
        "Return JSON:\n"
        "{\n"
        '  "authenticity_score": <float 0.0-1.0, 1.0=clearly genuine>,\n'
        '  "is_flagged_fake": <true/false>,\n'
        '  "is_flagged_coerced": <true/false>,\n'
        '  "flag_reasons": [<list of reason strings if flagged, else []>]\n'
        "}"
    )
    try:
        result = json.loads(await call_openai(system, user, json_mode=True))
        return {
            "authenticity_score": float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake":    bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced": bool(result.get("is_flagged_coerced", False)),
            "flag_reasons":       result.get("flag_reasons", []),
        }
    except Exception as e:
        logger("REVIEW_AI", f"Authenticity check failed: {str(e)}", level="ERROR")
        return {"authenticity_score": 1.0, "is_flagged_fake": False, "is_flagged_coerced": False, "flag_reasons": []}


# ── Step 6c ───────────────────────────────────────────────────────────────────

async def check_bias(
    overall_comment: str,
    avg_star_rating: float,
    freelancer_name: str,
    performance_score_summary: Dict,
) -> Dict:
    system = "You are a bias detection expert. Return valid JSON only."
    user = (
        f"Freelancer name: {freelancer_name}\n"
        f"Star rating given: {avg_star_rating:.1f} / 5.0\n"
        f'Review text: "{overall_comment}"\n\n'
        "Objective AI performance scores (0–1 scale):\n"
        f"- On-time delivery:   {performance_score_summary.get('on_time', 'N/A')}\n"
        f"- Revision rate:      {performance_score_summary.get('revision_rate', 'N/A')}\n"
        f"- Responsiveness:     {performance_score_summary.get('responsiveness', 'N/A')}\n"
        f"- Work quality:       {performance_score_summary.get('work_quality', 'N/A')}\n\n"
        "Detect if the rating seems biased or inconsistent with objective data.\n"
        "Also check if the review language suggests name-origin bias.\n\n"
        "Return JSON:\n"
        "{\n"
        '  "bias_score": <float 0.0-1.0, 1.0=strong bias detected>,\n'
        '  "bias_flags": {\n'
        '    "rating_vs_performance_inconsistency": <true/false>,\n'
        '    "name_bias": <true/false>\n'
        "  }\n"
        "}"
    )
    try:
        result = json.loads(await call_openai(system, user, json_mode=True))
        return {
            "bias_score":  float(result.get("bias_score", 0.0)),
            "bias_flags":  result.get("bias_flags", {}),
        }
    except Exception as e:
        logger("REVIEW_AI", f"Bias check failed: {str(e)}", level="ERROR")
        return {"bias_score": 0.0, "bias_flags": {}}


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
    score  = (weighted_review_avg / 5.0) * 35
    score += (work_quality_score or 0.7) * 25
    score += revision_rate_score * 15
    score += responsiveness_score * 15
    score += (communication_sentiment or 0.8) * 10
    if conflict_score and conflict_score > 0.7:
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