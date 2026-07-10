import os

from functions.db_manager import get_db

# Each RAG job-fit analysis is one real Groq LLM call against a shared, rate-limited
# API key pool (see rag_analyser.py's _GROQ_RAG_MODELS fallback chain). This caps how
# many analyses a single freelancer can trigger per day so one heavy user can't exhaust
# the shared quota for everyone else. Request-count is used as the limiting unit rather
# than raw token count: the prompt already truncates every field to a fixed length
# (bio 250 chars, job description 400 chars, etc. - see rag_analyser.py._build_prompt),
# so token usage per call is roughly bounded already, and a request-count limit avoids
# needing to parse Groq's response `usage` block, which _call_groq_rag doesn't do today.
DAILY_JOB_FIT_ANALYSIS_LIMIT = int(os.getenv("JOB_FIT_ANALYSIS_DAILY_LIMIT", "10"))


def check_and_increment_daily_usage(freelancer_id: str) -> tuple[bool, int]:
    """
    Atomically increments today's job-fit analysis count for this freelancer and
    reports whether they're still within the daily limit.

    Increments even on the request that crosses the limit, so "today's count" always
    reflects attempts rather than only served analyses - once over, every later call
    today is rejected without needing a second write to "undo" anything. The increment
    is a single INSERT ... ON CONFLICT DO UPDATE, so two concurrent requests from the
    same freelancer can't race past each other with a stale read.

    Returns (allowed, count_today).
    """
    db = get_db()
    row = db.execute_query(
        """
        INSERT INTO job_fit_analysis_usage (freelancer_id, usage_date, request_count)
        VALUES (:fid, CURRENT_DATE, 1)
        ON CONFLICT (freelancer_id, usage_date)
        DO UPDATE SET request_count = job_fit_analysis_usage.request_count + 1
        RETURNING request_count
        """,
        {"fid": freelancer_id},
    )
    count_today = row[0]["request_count"]
    return count_today <= DAILY_JOB_FIT_ANALYSIS_LIMIT, count_today
