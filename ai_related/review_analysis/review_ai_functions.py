import os
import re
import sys
import json
import math
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

DEFAULT_MODEL = "openai/gpt-oss-120b"
MODEL_FALLBACKS = [
    "openai/gpt-oss-120b",       # primary - matches ai_related/job_engine/rag_analyser.py's chain
    "llama-3.3-70b-versatile"   # fallback 1: separate rate-limit bucket
]

# Written into flag_reasons when the LLM analysis could not run at all, so the
# reconcile sweep can tell "we judged this and held it" apart from "we never got
# to judge it" and retry only the latter. Shared by both analyzers and the sweep -
# a literal duplicated in three places would drift.
ANALYSIS_UNAVAILABLE_REASON = "Automated analysis unavailable - held for manual review"

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
            logger("REVIEW_AI", f"Loaded {len(models)} supported Groq models", level="INFO")
            return models
    except Exception as e:
        logger("REVIEW_AI", f"Failed to load Groq models list: {str(e)}", level="WARNING")
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
                        logger("REVIEW_AI", f"Invalid JSON from model {model}: {str(e)}", level="ERROR")
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


# A generated question is shown verbatim to the reviewer, so it has to be neutral:
# anything that presupposes an outcome ("how impressed were you...") biases every
# review it produces. Cheap guard rather than trusting the prompt alone.
_LEADING_WORDS = re.compile(
    r"\b(excellent|impressive|impressed|amazing|outstanding|fantastic|great|"
    r"superb|brilliant|flawless|terrible|awful|poor|disappoint\w*)\b",
    re.IGNORECASE,
)

QUESTION_MIN_LEN = 20
QUESTION_MAX_LEN = 200


def _validate_generated_question(question: Optional[str]) -> Optional[str]:
    """Return the question if it is usable, else None so the caller falls back.

    Rejects anything that is not a single neutral open question: wrong length,
    missing question mark, outcome-presupposing wording, or a yes/no opener that
    would produce one-word answers with no signal for the groundedness check.
    """
    if not question:
        return None

    q = " ".join(str(question).split())
    if not (QUESTION_MIN_LEN <= len(q) <= QUESTION_MAX_LEN):
        return None
    if not q.endswith("?") or q.count("?") != 1:
        return None
    if _LEADING_WORDS.search(q):
        return None
    if q.split(" ", 1)[0].lower() in {"did", "was", "were", "is", "are", "do", "does", "has", "have"}:
        return None
    return q


async def generate_targeted_question(
    job_title: str,
    role_title: str,
    job_description: str,
    contract_title: str,
    role_skills: List[str],
    category: str,
    submission_notes: List[str],
) -> str:
    """Generate the review question from the actual project.

    A stock question ("How satisfied are you overall?") can be answered
    convincingly by anyone, including someone who never did the work. A question
    grounded in this specific project cannot, which is what makes the answer
    usable as an authenticity signal downstream (see answer_groundedness in
    analyze_review_full). That is the point of generating it rather than looking
    it up.

    Falls back to get_targeted_question(category) - the ai_review_prompts table -
    and then to a hardcoded string, so a Groq outage degrades the question rather
    than breaking contract completion.
    """
    try:
        system = (
            "You write a single neutral review question for a freelancing platform. "
            "Return valid JSON only, no markdown fences or commentary."
        )

        skills_line = ", ".join(role_skills[:12]) if role_skills else "not specified"
        notes_block = "\n".join(f"- {n}" for n in submission_notes[:5] if n) or "- none recorded"

        user = (
            "A client is about to review a freelancer for this completed project.\n\n"
            f"Job title: {job_title}\n"
            f"Contract title: {contract_title}\n"
            f"Role: {role_title}\n"
            f"Category: {category}\n"
            f"Required skills: {skills_line}\n"
            f"Job description:\n{(job_description or '')[:1500]}\n\n"
            "Notes the freelancer attached to their submissions:\n"
            f"{notes_block}\n\n"
            "Write ONE question for the client about how this specific project went.\n"
            "Rules:\n"
            "- Reference a concrete aspect of THIS project (a named deliverable, "
            "technology, constraint, or requirement above).\n"
            "- Stay strictly neutral: do NOT presuppose the work went well or badly, "
            "and do not use evaluative adjectives.\n"
            "- Open-ended. Must NOT be answerable with yes or no. Start it with a word "
            "like How, What, Which, or Where.\n"
            "- One sentence, under 200 characters, ending in a question mark.\n"
            "- Address the client as \"you\"; never name the freelancer.\n"
            'Return exactly: {"question": "..."}'
        )

        result = await call_llm(system, user, json_mode=True)
        validated = _validate_generated_question(
            result.get("question") if isinstance(result, dict) else None
        )
        if validated:
            logger("REVIEW_AI", f"Generated targeted question for category={category}", level="INFO")
            return validated

        logger(
            "REVIEW_AI",
            f"Generated question rejected by validation (category={category}), using prompt table",
            level="WARNING",
        )
    except Exception as e:
        logger("REVIEW_AI", f"Question generation failed, using prompt table: {str(e)}", level="WARNING")

    return get_targeted_question(category)


ON_TIME_ZERO_AT_DAYS = 30.0  # days late at which the on-time component bottoms out


def compute_on_time_score(end_date, actual_completion_date) -> Optional[float]:
    """Graded on-time score, or None when there is nothing to measure.

    Was binary 1.0/0.5, which scored one day late exactly the same as one month
    late, and returned a flattering 0.5-0.8 for contracts that had no completion
    or deadline recorded at all - inventing delivery evidence that did not exist.
    Now returns None in those cases so calculate_trust_score drops the component
    and renormalizes, and decays linearly to 0 over ON_TIME_ZERO_AT_DAYS.

    Callers should pass contract.original_end_date, not end_date, so a deadline
    extension granted during dispute arbitration cannot turn a late delivery into
    an on-time one.
    """
    if not actual_completion_date or not end_date:
        return None

    days_late = (actual_completion_date - end_date).days
    if days_late <= 0:
        return 1.0
    return round(max(0.0, 1.0 - (days_late / ON_TIME_ZERO_AT_DAYS)), 3)


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
    """Reply-gap score from the contract's DM thread. Takes a users.user_id, not
    a freelancer_id, because it compares against dm_message.sender_id."""
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


def blend_communication_score(
    ai_quality_score: float,
    client_star_normalized: Optional[float],
    sentiment_score: float,
) -> float:
    """
    Blends all available communication signals into a single 0–1 score.

    Signal weights:
      56.25%: client's explicit communication star rating (1-5, normalized to 0-1)
      37.5%:  AI assessment of the message thread
      6.25%:  overall review sentiment (weakest, indirect signal)

    responsiveness_score was REMOVED from this blend. It is already its own named
    10% component of calculate_trust_score, and at 20% of this 10% component it
    was being counted a second time - an effective 12% influence for a signal
    documented as 10%. Correlated inputs quietly inflating each other is exactly
    what the "every sub-score is a genuine, named, weighted input" rebalance was
    meant to eliminate. The three weights above are the old 45/30/5 renormalised.

    Known and accepted overlap: the client's communication star also contributes
    to weighted_review_avg as one of the rating categories. That is diluted to a
    few percent there, and a communication score that ignored the client's own
    communication rating would be perverse, so it stays.

    Any signal that is None is dropped and its weight redistributed across the
    rest, the same way calculate_trust_score handles missing components.
    """
    sentiment_component = max(0.0, min(1.0, 0.5 + sentiment_score / 2.0))

    components = [
        (0.5625, client_star_normalized),
        (0.375, ai_quality_score),
        (0.0625, sentiment_component),
    ]

    present = [(w, float(v)) for w, v in components if v is not None]
    total_weight = sum(w for w, _ in present)
    if total_weight <= 0:
        return 0.5

    blended = sum(w * v for w, v in present) / total_weight
    return round(max(0.0, min(1.0, blended)), 3)


def _fmt_metric(value) -> str:
    """Render an objective metric for the LLM prompt.

    Components with no supporting data are None since the honest-defaults change.
    Interpolated raw, that renders as the literal "None", which the model read as
    "the platform measured this and found nothing" - it started flagging reviews
    for "claiming on-time delivery when objective data shows no record". Missing
    data has to be labelled as missing, not as a zero.
    """
    return "not recorded" if value is None else f"{float(value):.3f}"


async def analyze_review_full(
    overall_comment: str,
    client_answer: str,
    avg_star_rating: float,
    freelancer_name: str,
    performance_score_summary: Dict,
    message_thread: str,
    communication_star_rating: Optional[float] = None,  # raw 1–5 from review_ratings, shown as context only
    ai_question: str = "",
) -> Dict:
    """
    Sentiment is intentionally NOT requested here: the trained sentiment_detector
    model (review_ml/) is the sole source of truth for sentiment_score/label
    (see review_pipeline.py), so asking the LLM to also guess it was dead weight
    - computed every call, never read. This call now covers only what the ML
    models can't: authenticity/coercion/mismatch judgment with real-world
    context, and communication quality read from the actual message thread
    (no ML model here ever looks at the thread). The communication-quality
    blend into a single score happens in review_pipeline.py instead of here,
    since it needs the ML sentiment score, which isn't computed yet at this point.
    """
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
        "communication_quality_score": "float between 0.0 and 1.0, quality of freelancer communication judged from the message thread ONLY, not the review text",
        "communication_summary": "string, 1-2 sentence summary of communication quality based on the message thread",
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
        f"- Client's explicit communication star rating: {communication_star_rating:.1f} / 5\n"
        if communication_star_rating is not None
        else ""
    )

    # The question is generated per-project, so the answer can only be judged for
    # groundedness if the model is told what was actually asked. Without it the
    # model was rating an answer against an unknown question.
    qa_block = (
        f"Project-specific question the client was asked:\n{ai_question}\n"
        f"Their answer:\n{client_answer}\n\n"
        if ai_question
        else f"Client's answer to a follow-up question:\n{client_answer}\n\n"
    )

    user = (
        f"Review text:\n{overall_comment}\n\n"
        f"{qa_block}"
        f"Star rating given: {avg_star_rating:.1f} out of 5\n"
        f"Freelancer name: {freelancer_name}\n\n"
        "Objective performance summary (0–1 scale). 'not recorded' means the platform "
        "has no measurement for it - that is missing data, NOT evidence against the "
        "review, and must not be treated as contradicting anything the reviewer says:\n"
        f"- On-time delivery: {_fmt_metric(performance_score_summary.get('on_time'))}\n"
        f"- Revision rate: {_fmt_metric(performance_score_summary.get('revision_rate'))}\n"
        f"- Responsiveness: {_fmt_metric(performance_score_summary.get('responsiveness'))}\n"
        f"{comm_star_line}"
        "\nMessage thread from the project (use this to assess communication_quality_score):\n"
        f"{message_thread[:3000]}\n\n"
        "Assess the review for authenticity, coercion, sentiment/rating mismatch, communication "
        "quality, and how well the answer is grounded in this specific project. "
        "Base your analysis entirely on the data above, do not invent or assume anything.\n"
        "Return exactly one JSON object matching this schema:\n"
        f"{json.dumps(schema_description, ensure_ascii=False, indent=2)}"
    )

    try:
        result = await call_llm(system, user, json_mode=True)

        groundedness = result.get("answer_groundedness")
        return {
            "sentiment_mismatch":          bool(result.get("sentiment_mismatch", False)),
            "authenticity_score":          float(result.get("authenticity_score", 1.0)),
            "is_flagged_fake":             bool(result.get("is_flagged_fake", False)),
            "is_flagged_coerced":          bool(result.get("is_flagged_coerced", False)),
            "flag_reasons":                result.get("flag_reasons", []),
            "communication_quality_score": max(0.0, min(1.0, float(result.get("communication_quality_score", 0.5)))),
            "communication_summary":       result.get("communication_summary", ""),
            "answer_groundedness":         max(0.0, min(1.0, float(groundedness))) if groundedness is not None else None,
            "analysis_unavailable":        False,
        }

    except Exception as e:
        # Fail CLOSED. This used to return authenticity_score=1.0 with no flags,
        # so a Groq outage auto-published every review AND handed each one a
        # perfect authenticity contribution through the (llm+ml)/2 blend. A review
        # we could not analyse is a review we have not checked, so it goes to the
        # admin queue instead of straight to the public profile.
        logger("REVIEW_AI", f"Review analysis failed, failing closed: {str(e)}", level="ERROR")
        return {
            "sentiment_mismatch":          False,
            "authenticity_score":          0.0,
            "is_flagged_fake":             False,
            "is_flagged_coerced":          False,
            "flag_reasons":                [ANALYSIS_UNAVAILABLE_REASON],
            "communication_quality_score": 0.5,
            "communication_summary":       "Analysis unavailable.",
            "answer_groundedness":         None,
            "analysis_unavailable":        True,
        }


SHRINKAGE_K = 5.0       # reviews needed before the raw average carries ~half the weight
SHRINKAGE_PRIOR = 3.5   # neutral star rating a freelancer/client is assumed to be at


def shrink_toward_prior(weighted_review_avg: float, total_reviews: int) -> float:
    """Pull a star average toward a neutral prior in proportion to how little
    evidence supports it.

    Without this, one 5-star review produced the same star component as fifty,
    and a single review was enough to score near the top of the scale. With
    K=5, one review lands about 17% of the way from the prior to the raw
    average, ten reviews about 67%, fifty about 91%.
    """
    n = max(0, int(total_reviews or 0))
    if n <= 0:
        return SHRINKAGE_PRIOR
    return (n * float(weighted_review_avg) + SHRINKAGE_K * SHRINKAGE_PRIOR) / (n + SHRINKAGE_K)


def calculate_trust_score(
    weighted_review_avg: float,
    on_time_score: Optional[float],
    revision_rate_score: Optional[float],
    responsiveness_score: Optional[float],
    communication_sentiment: Optional[float],
    authenticity_confidence: float,
    consistency_score: float,
    coerced_ratio: float,
    total_reviews: int = 0,
    record_consistency: Optional[float] = None,
    unfair_review_ratio: float = 0.0,
) -> float:
    """
    Trust score, rebalanced so every sub-score is a genuine, named, weighted
    input - not a hand-wavy formula and not an invisible pass/fail gate.

    Weights (of 100), grouped by how gameable the evidence is:

      OBJECTIVE BEHAVIOUR - 40%, the largest share because it is measured, not
      self-reported, and a freelancer cannot talk it into existence:
      15%  on_time_score           - on-time delivery rate
      15%  revision_rate_score     - revision frequency (fewer = better)
      10%  responsiveness_score    - reply speed from message threads

      CLIENT JUDGEMENT - 30%:
      30%  weighted_review_avg     - recency-weighted client star ratings

      COMMUNICATION - 10%:
      10%  communication_sentiment - blended communication quality score.
                                      responsiveness_score was removed from that
                                      blend; it is its own component above and was
                                      being counted twice.

      REVIEW INTEGRITY - 20% collectively, deliberately split so no single model
      dominates a reputation number - each of these is a model, and models are wrong:
       8%  authenticity_confidence - Component 2 (authenticity_detector), averaging
                                      (1 - LENGTH-CALIBRATED fake probability). The
                                      raw score was 7.1x more likely to flag short
                                      genuine reviews, which charged a freelancer for
                                      how briefly their clients write.
       6%  consistency_score       - Component 3 (mismatch_detector): text vs rating,
                                      inverse of avg P(disagree).
       6%  record_consistency      - Component 4 (review_consistency.py): star ratings
                                      vs the objective contract record. Arithmetic,
                                      not a model, so it is auditable and needs no
                                      fairness audit. Catches the case no text model
                                      can - a glowing review of an engagement that
                                      was measurably late and heavily revised.

    Deflation - a review HARSHER than the record - is deliberately absent from the
    weighted components. That reflects on the reviewer, not the reviewed; penalising
    someone for being unfairly reviewed inverts the intent. It is charged to whoever
    wrote it, as a penalty on their own score:

      -15  coerced_ratio            - proportional, capped
       -8  unfair_review_ratio      - share of the CLIENT reviews this freelancer
                                       wrote that are materially harsher than that
                                       client's record. Capped lower than the client
                                       side's 10 because the evidence is weaker: a
                                       client's telemetry is a lifetime aggregate
                                       rather than per contract, and holds only
                                       responsiveness and revision churn. See
                                       calculate_freelancer_review_fairness.

    All of on_time_score/revision_rate_score/responsiveness_score/
    communication_sentiment/authenticity_confidence/consistency_score must
    already be aggregated across the freelancer's FULL contract/review
    history by the caller (see calculate_aggregate_performance and
    calculate_ai_trust_components) - passing a single contract's scores here
    would silently let one recent job dominate a lifetime reputation number.

    coerced_ratio (fraction of a freelancer's reviews flagged as coerced) is
    a proportional penalty of up to 15 points, replacing the old flat -5
    "if any coercion flag exists" rule that scored one bad flag the same as ten.

    Two things changed in how the weights are applied:

    * The star average is shrunk toward a neutral prior by review count
      (shrink_toward_prior), so a thin history cannot buy a top score.
    * A component whose input is None has no supporting data, and is dropped
      with its weight redistributed proportionally across the components that
      do - rather than being filled with a generous default. Previously a
      contract with no delivery history contributed 22.5 points of on-time and
      revision credit it had not earned. Missing data is now neutral, not
      rewarding; shrinkage is what handles thin evidence.
    """
    effective_avg = shrink_toward_prior(weighted_review_avg, total_reviews)

    components = [
        (30.0, effective_avg / 5.0),
        (15.0, on_time_score),
        (15.0, revision_rate_score),
        (10.0, responsiveness_score),
        (10.0, communication_sentiment),
        (8.0, authenticity_confidence),
        (6.0, consistency_score),
        (6.0, record_consistency),
    ]

    present = [(w, float(v)) for w, v in components if v is not None]
    total_weight = sum(w for w, _ in present)
    if total_weight <= 0:
        return 0.0

    score = 100.0 * sum(w * v for w, v in present) / total_weight
    score -= min(15.0, coerced_ratio * 30)
    score -= min(8.0, unfair_review_ratio * 16)

    return round(min(100.0, max(0.0, score)), 2)


def compute_repeat_weight(occurrence_index: int) -> float:
    """Diminishing weight for repeated reviews from the same counterparty.

    uq_reviews_contract caps one review per contract, but nothing stops the same
    pair running many small contracts to manufacture reputation. The k-th review
    from a given counterparty is worth 1/sqrt(k), so the first is full value and
    the tenth is worth about 0.32. Genuine repeat business still counts, it just
    stops counting linearly.
    """
    k = max(1, int(occurrence_index or 1))
    return 1.0 / math.sqrt(k)


# A review materially harsher than the objective contract record is down-weighted,
# but only slightly and only past a threshold. Both numbers are deliberately timid.
#
# THE HAZARD, and why this is not more aggressive: the telemetry measures
# timeliness, revision count and reply speed. It does NOT measure the quality of
# the deliverable or how the freelancer behaved. A freelancer can be punctual,
# responsive, revision-free and still produce poor work or be unpleasant to deal
# with. For that client, a harsh review is accurate and the record will still make
# it look "deflated". Down-weighting hard on this signal would let good telemetry
# quietly suppress legitimate criticism - a worse integrity failure than the
# unfair-review problem it is trying to solve.
#
# So: nothing happens below the threshold, and the maximum penalty is a 25% weight
# reduction. Criticism stays audible. The stronger response to a genuinely unfair
# reviewer is the penalty on THEIR trust score - see calculate_review_fairness.
DEFLATION_WEIGHT_THRESHOLD = 0.4
DEFLATION_MAX_REDUCTION = 0.25


def _deflation_weight(deflation: Optional[float]) -> float:
    """1.0 (no reduction) up to 1 - DEFLATION_MAX_REDUCTION at full deflation."""
    if deflation is None or deflation <= DEFLATION_WEIGHT_THRESHOLD:
        return 1.0
    span = 1.0 - DEFLATION_WEIGHT_THRESHOLD
    ramp = min(1.0, (deflation - DEFLATION_WEIGHT_THRESHOLD) / span)
    return 1.0 - DEFLATION_MAX_REDUCTION * ramp


def calculate_weighted_review_avg(freelancer_id: str) -> Tuple[float, int]:
    try:
        from ai_related.review_analysis.review_consistency import compare_review_to_record

        db = get_db()
        rows = db.execute_query(
            """
            SELECT r.id AS review_id, rr.category, rr.score, r.published_at,
                   ra.authenticity_score,
                   fps.on_time_score, fps.revision_rate_score, fps.responsiveness_score,
                   DENSE_RANK() OVER (
                       PARTITION BY r.reviewer_id ORDER BY r.published_at, r.id
                   ) AS pair_occurrence
            FROM review_ratings rr
            JOIN reviews r ON r.id = rr.review_id
            LEFT JOIN review_ai_analysis ra ON ra.review_id = r.id
            LEFT JOIN freelancer_performance_scores fps ON fps.contract_id = r.contract_id
            WHERE r.freelancer_id = :fid AND r.status = 'published'
            """,
            {"fid": freelancer_id},
        )

        if not rows:
            return 0.0, 0

        # Deflation is a property of a whole review, not of one rating category, so
        # it has to be computed per review before the per-rating loop below.
        deflation_by_review = {}
        ratings_by_review = {}
        performance_by_review = {}
        for row in rows:
            rid = str(row["review_id"])
            ratings_by_review.setdefault(rid, []).append(
                {"category": row["category"], "score": row["score"]}
            )
            performance_by_review[rid] = {
                "on_time_score": row["on_time_score"],
                "revision_rate_score": row["revision_rate_score"],
                "responsiveness_score": row["responsiveness_score"],
            }
        for rid, ratings in ratings_by_review.items():
            result = compare_review_to_record(ratings, performance_by_review[rid])
            deflation_by_review[rid] = result["deflation"]

        now = datetime.now(timezone.utc)
        weighted_sum = 0.0
        weight_total = 0.0

        for row in rows:
            published_at = row["published_at"]
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            months_ago = max(0, (now - published_at).days / 30)
            recency_weight = 1 / (1 + months_ago)

            # Confidence-weight by authenticity so a borderline-but-published
            # review counts less than a clearly-genuine one, instead of
            # authenticity only ever acting as a binary publish/suppress gate.
            authenticity_weight = float(row["authenticity_score"]) if row["authenticity_score"] is not None else 1.0
            repeat_weight = compute_repeat_weight(row["pair_occurrence"])
            deflation_weight = _deflation_weight(deflation_by_review.get(str(row["review_id"])))
            weight = recency_weight * authenticity_weight * repeat_weight * deflation_weight

            weighted_sum += float(row["score"]) * weight
            weight_total += weight

        weighted_avg = round(weighted_sum / weight_total, 3) if weight_total > 0 else 0.0

        count_rows = db.execute_query(
            """
            SELECT COUNT(DISTINCT r.id) as cnt
            FROM reviews r
            WHERE r.freelancer_id = :fid AND r.status = 'published'
            """,
            {"fid": freelancer_id},
        )
        total = int(count_rows[0]["cnt"]) if count_rows else 0

        return weighted_avg, total

    except Exception as e:
        logger("REVIEW_AI", f"Error computing weighted avg: {str(e)}", level="ERROR")
        return 0.0, 0


def calculate_review_fairness(client_id: str) -> float:
    """
    The fraction of reviews this client has WRITTEN that are materially harsher
    than the objective contract record.

    This is the other half of deflation handling. Down-weighting the review (see
    _deflation_weight) protects the freelancer being reviewed; this makes the
    behaviour cost the reviewer something, which is where the cost belongs. A
    client who writes one harsh review of a genuinely bad engagement is not
    penalised - a client who does it systematically is.

    Note this measures reviews the client AUTHORED, unlike every other input to
    calculate_client_trust_score, which measures reviews they received. It is a
    judgement about their conduct as a reviewer, not their reputation as a client.

    Returns 0.0 when there is nothing comparable, so it never penalises on absent
    evidence.
    """
    try:
        from ai_related.review_analysis.review_consistency import compare_review_to_record

        db = get_db()
        rows = db.execute_query(
            """
            SELECT r.id AS review_id, rr.category, rr.score,
                   fps.on_time_score, fps.revision_rate_score, fps.responsiveness_score
            FROM reviews r
            JOIN review_ratings rr ON rr.review_id = r.id
            LEFT JOIN freelancer_performance_scores fps ON fps.contract_id = r.contract_id
            WHERE r.reviewer_id = :cid AND r.status = 'published'
            """,
            {"cid": client_id},
        )
        if not rows:
            return 0.0

        by_review = {}
        for row in rows:
            rid = str(row["review_id"])
            entry = by_review.setdefault(
                rid,
                {
                    "ratings": [],
                    "performance": {
                        "on_time_score": row["on_time_score"],
                        "revision_rate_score": row["revision_rate_score"],
                        "responsiveness_score": row["responsiveness_score"],
                    },
                },
            )
            entry["ratings"].append({"category": row["category"], "score": row["score"]})

        comparable, unfair = 0, 0
        for entry in by_review.values():
            result = compare_review_to_record(entry["ratings"], entry["performance"])
            if result["deflation"] is None:
                continue
            comparable += 1
            if result["deflation"] >= DEFLATION_WEIGHT_THRESHOLD:
                unfair += 1

        if not comparable:
            return 0.0
        return round(unfair / comparable, 3)

    except Exception as e:
        logger("REVIEW_AI", f"Error computing review fairness for client {client_id}: {str(e)}",
               level="ERROR")
        return 0.0


def calculate_aggregate_performance(freelancer_id: str) -> Dict:
    """
    Averages on_time/revision/responsiveness/communication scores across
    ALL of a freelancer's completed contracts. Fixes the previous bug where
    calculate_trust_score was fed scores from only the single contract tied
    to whichever review had just been submitted, letting one recent job
    swing half the trust score independent of a long track record.

    Joined to reviews.status = 'published' only: communication_sentiment_score
    and conflict_score are derived by the AI analysis from that same review's
    text (Step 6.5, saved regardless of publish outcome), so a review the
    pipeline suppressed/flagged as likely fake or coerced would otherwise
    still feed its unreviewed communication/conflict signal into this
    freelancer's aggregate the next time the trust score recalculates - a
    silent effect on a review nobody can actually see. on_time_score/
    revision_rate_score/responsiveness_score are objective contract facts
    computed before the review even exists, but they ride the same join since
    they're on the same row.
    """
    empty = {
        "on_time_score": None,
        "revision_rate_score": None,
        "responsiveness_score": None,
        "communication_sentiment_score": None,
        "coerced_ratio": 0.0,
    }
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT fps.on_time_score, fps.revision_rate_score, fps.responsiveness_score,
                   fps.communication_sentiment_score, fps.conflict_score
            FROM freelancer_performance_scores fps
            JOIN reviews r ON r.contract_id = fps.contract_id
            WHERE fps.freelancer_id = :fid AND r.status = 'published'
            """,
            {"fid": freelancer_id},
        )
        # coerced_ratio is deliberately NOT read from the query above. conflict_score
        # is set to 1.0 only when the AI analysis flags a review as coerced, and that
        # flag forces overall_pass=False, which stops the review publishing - so the
        # published-only join made the coerced count structurally always zero and the
        # 15-point penalty in calculate_trust_score unreachable. It could only fire
        # after an admin override-published a coerced review, i.e. exactly when a human
        # had decided it was legitimate. Counted here over every review the pipeline
        # actually analysed instead.
        coerced_rows = db.execute_query(
            """
            SELECT fps.conflict_score
            FROM freelancer_performance_scores fps
            JOIN reviews r ON r.contract_id = fps.contract_id
            WHERE fps.freelancer_id = :fid
              AND r.status IN ('published', 'flagged', 'suppressed')
            """,
            {"fid": freelancer_id},
        )
        coerced_ratio = 0.0
        if coerced_rows:
            coerced_count = sum(1 for r in coerced_rows if float(r["conflict_score"] or 0.0) > 0.7)
            coerced_ratio = round(coerced_count / len(coerced_rows), 3)

        if not rows:
            return {**empty, "coerced_ratio": coerced_ratio}

        # None, not a flattering constant, when a component has no supporting data.
        # calculate_trust_score drops those components and renormalizes the remaining
        # weights, so a contract with no delivery history contributes nothing instead
        # of contributing 22.5 points of invented evidence.
        def avg(key: str) -> Optional[float]:
            values = [float(r[key]) for r in rows if r[key] is not None]
            return round(sum(values) / len(values), 3) if values else None

        return {
            "on_time_score": avg("on_time_score"),
            "revision_rate_score": avg("revision_rate_score"),
            "responsiveness_score": avg("responsiveness_score"),
            "communication_sentiment_score": avg("communication_sentiment_score"),
            "coerced_ratio": coerced_ratio,
        }
    except Exception as e:
        logger("REVIEW_AI", f"Error computing aggregate performance: {str(e)}", level="ERROR")
        return empty


def _calculate_record_consistency(freelancer_id: str) -> Dict:
    """
    Component 4: how far this freelancer's published reviews sit from the objective
    contract record, averaged across their history.

    Computed from source rows (review_ratings + freelancer_performance_scores)
    rather than read from a stored column, so no schema change is needed and the
    arithmetic stays auditable against the underlying data. See
    review_consistency.py for why this is arithmetic rather than a model.

    Returns {"record_consistency": float|None}. None means nothing was comparable - no telemetry, or no mappable rating
    category - and calculate_trust_score drops None components rather than
    crediting them.
    """
    from ai_related.review_analysis.review_consistency import (
        compare_review_to_record,
        record_consistency_score,
    )

    db = get_db()
    rows = db.execute_query(
        """
        SELECT r.id AS review_id,
               rr.category,
               rr.score,
               fps.on_time_score,
               fps.revision_rate_score,
               fps.responsiveness_score
        FROM reviews r
        JOIN review_ratings rr ON rr.review_id = r.id
        LEFT JOIN freelancer_performance_scores fps ON fps.contract_id = r.contract_id
        WHERE r.freelancer_id = :fid AND r.status = 'published'
        """,
        {"fid": freelancer_id},
    )
    if not rows:
        return {"record_consistency": None}

    per_review = {}
    for row in rows:
        entry = per_review.setdefault(
            str(row["review_id"]),
            {
                "ratings": [],
                "performance": {
                    "on_time_score": row["on_time_score"],
                    "revision_rate_score": row["revision_rate_score"],
                    "responsiveness_score": row["responsiveness_score"],
                },
            },
        )
        entry["ratings"].append({"category": row["category"], "score": row["score"]})

    inflations, comparable = [], 0
    for entry in per_review.values():
        result = compare_review_to_record(entry["ratings"], entry["performance"])
        if result["inflation"] is None:
            continue
        comparable += 1
        inflations.append(result["inflation"])

    if not comparable:
        return {"record_consistency": None}

    # Only inflation. Deflation - a review harsher than the record - reflects on the
    # REVIEWER, and is charged there instead: calculate_review_fairness aggregates it
    # per client and calculate_client_trust_score applies it as a penalty. Charging
    # it here as well would penalise a freelancer for being unfairly reviewed.
    return {"record_consistency": record_consistency_score(sum(inflations) / len(inflations))}


def calculate_ai_trust_components(freelancer_id: str) -> Dict:
    """
    Averages this freelancer's own model outputs across their published reviews,
    for use as named trust-score inputs rather than a one-time publish/suppress
    gate.

    Three components:
      authenticity_confidence - Component 2, length-calibrated
      consistency_score       - Component 3, text vs rating
      record_consistency      - Component 4, rating vs objective contract record

    Deflation - a review harsher than the objective record - is deliberately absent.
    It reflects on the reviewer, and is charged there: calculate_review_fairness
    feeds calculate_client_trust_score as a penalty, and _deflation_weight reduces
    such a review's weight in calculate_weighted_review_avg.
    """
    try:
        db = get_db()
        rows = db.execute_query(
            """
            SELECT ra.authenticity_score, ra.disagreement_probability
            FROM review_ai_analysis ra
            JOIN reviews r ON r.id = ra.review_id
            WHERE r.freelancer_id = :fid AND r.status = 'published'
            """,
            {"fid": freelancer_id},
        )
        if not rows:
            return {
                "authenticity_confidence": 1.0,
                "consistency_score": 1.0,
                "record_consistency": None,
            }

        auth_scores = [float(r["authenticity_score"]) for r in rows if r["authenticity_score"] is not None]
        authenticity_confidence = round(sum(auth_scores) / len(auth_scores), 3) if auth_scores else 1.0

        # disagreement_probability stores P(text and rating disagree) on a 0-1 scale
        # from the disagreement classifier, so it inverts directly. It used to hold
        # |predicted - actual| stars (0-4) from a regressor that has been removed:
        # that residual was biased by rating level, handing 0.492 consistency to a
        # freelancer whose clients rate honestly low against 0.874 for one rated
        # high. The classifier's equivalent spread is -0.054.
        #
        # Rows analysed before the switch were nulled by the migration rather than
        # converted - a star gap cannot be turned into this classifier's probability -
        # so they are skipped here and repopulate when those reviews are re-analysed.
        severities = [float(r["disagreement_probability"]) for r in rows
                      if r["disagreement_probability"] is not None]
        avg_disagreement = (sum(severities) / len(severities)) if severities else 0.0
        consistency_score = round(max(0.0, 1.0 - avg_disagreement), 3)

        try:
            record = _calculate_record_consistency(freelancer_id)
        except Exception as e:
            # Component 4 is additive; a failure here must not take out the two
            # components that were already working.
            logger("REVIEW_AI", f"Record-consistency check failed: {str(e)}", level="WARNING")
            record = {"record_consistency": None}

        return {
            "authenticity_confidence": authenticity_confidence,
            "consistency_score": consistency_score,
            "record_consistency": record["record_consistency"],
        }
    except Exception as e:
        logger("REVIEW_AI", f"Error computing AI trust components: {str(e)}", level="ERROR")
        return {
            "authenticity_confidence": 1.0,
            "consistency_score": 1.0,
            "record_consistency": None,
        }


# Profile-level AI review summary

MIN_REVIEWS_FOR_SUMMARY = 3
SUMMARY_REGEN_INTERVAL = 5  # regenerate at 3, 8, 13, 18... published reviews


def _fetch_published_review_texts(freelancer_id: str) -> List[Dict]:
    db = get_db()
    return db.execute_query(
        """
        SELECT wc.overall_comment, wc.client_answer
        FROM reviews r
        JOIN review_written_content wc ON wc.review_id = r.id
        WHERE r.freelancer_id = :fid AND r.status = 'published'
        ORDER BY r.published_at DESC
        """,
        {"fid": freelancer_id},
    )


def _fetch_top_skill_tags(freelancer_id: str, limit: int = 8) -> List[str]:
    db = get_db()
    rows = db.execute_query(
        """
        SELECT st.skill_tag, COUNT(*) as cnt
        FROM review_skill_tags st
        JOIN reviews r ON r.id = st.review_id
        WHERE r.freelancer_id = :fid AND r.status = 'published'
        GROUP BY st.skill_tag
        ORDER BY cnt DESC
        LIMIT :limit
        """,
        {"fid": freelancer_id, "limit": limit},
    )
    return [row["skill_tag"] for row in rows]


async def generate_freelancer_review_summary(
    freelancer_id: str,
    freelancer_name: str,
) -> Optional[str]:
    """
    Synthesizes all of a freelancer's PUBLISHED reviews into a short profile
    summary. Only reads published reviews on purpose - harmful text and
    fake/coerced reviews were already filtered out by the publish gate before
    this ever runs, so this inherits that safety guarantee instead of
    re-checking it. Returns None below MIN_REVIEWS_FOR_SUMMARY (summarizing
    1-2 reviews is redundant with just reading them).
    """
    try:
        review_rows = _fetch_published_review_texts(freelancer_id)
        if len(review_rows) < MIN_REVIEWS_FOR_SUMMARY:
            return None

        skill_tags = _fetch_top_skill_tags(freelancer_id)

        reviews_block = "\n\n".join(
            f"- {(row.get('overall_comment') or '').strip()} {(row.get('client_answer') or '').strip()}".strip()
            for row in review_rows
        )

        system = (
            "You are summarizing a freelancer's published client reviews for their public profile. "
            "Base the summary strictly on the review text provided - do not invent skills, "
            "achievements, or feedback that isn't actually stated. Do not write pure marketing "
            "copy: if a criticism repeats across multiple reviews, include it."
        )
        user = (
            f"Freelancer name: {freelancer_name}\n"
            f"Frequently confirmed skills across these reviews: {', '.join(skill_tags) or 'none recorded'}\n\n"
            f"Published client reviews ({len(review_rows)} total):\n{reviews_block[:6000]}\n\n"
            "Write a 3-4 sentence summary covering: (1) overall impression, (2) specific "
            "recurring strengths, (3) an honest recurring critique ONLY if at least two "
            "reviews raise something similar - otherwise omit it entirely, (4) what kind of "
            "project this freelancer seems best suited for, if inferable from the reviews. "
            "Plain prose only, no markdown, no bullet points."
        )

        summary = await call_llm(system, user, json_mode=False)
        return summary.strip() if summary else None

    except Exception as e:
        logger("REVIEW_AI", f"Review summary generation failed for {freelancer_id}: {str(e)}", level="ERROR")
        return None