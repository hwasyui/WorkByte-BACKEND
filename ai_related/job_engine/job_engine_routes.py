import time
from fastapi import APIRouter, Depends

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import get_freelancer_profile_for_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_engine.sweep_worker import run_sweep_once
from ai_related.job_engine.rag_analyser import analyse_role_match
from ai_related.job_engine.usage_limits import check_and_increment_daily_usage, get_daily_usage

router = APIRouter(prefix="/ai/job-engine", tags=["Job Engine"])


def _serialize_rows(rows) -> list:
    result = []
    for row in rows:
        d = dict(row)
        for k, v in d.items():
            if hasattr(v, "__class__"):
                cls = v.__class__.__name__
                if "UUID" in cls:
                    d[k] = str(v)
                elif cls == "Decimal":
                    d[k] = float(v)
        result.append(d)
    return result


@router.get("/analyse/role/{job_role_id}")
async def analyse_role(
    job_role_id: str,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    RAG + LLM analysis of the freelancer's fit for one specific job role.
    Retrieves role requirements, the freelancer's profile, and relevant past
    contracts from the DB, then asks the LLM for a structured JSON response
    with match_score, strengths, gaps, recommendation, and skill_tips.
    LLM calls can take 5-30s; this is user-triggered so the latency is acceptable.

    Capped at DAILY_JOB_FIT_ANALYSIS_LIMIT analyses per freelancer per day
    (default 10) against the shared Groq key pool -- the usage check runs
    before analyse_role_match(), so a freelancer already over the limit never
    triggers an LLM call at all.
    """
    t_request = time.perf_counter()
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        usage = check_and_increment_daily_usage(db, fid)
        if not usage["allowed"]:
            logger(
                "JOB_ENGINE",
                f"Daily job-fit analysis limit reached | freelancer_id={fid} "
                f"| usage_today={usage['usage_today']} | limit={usage['usage_limit']}",
                level="WARNING",
            )
            return ResponseSchema.error(
                {
                    "message":         f"Daily job-fit analysis limit reached ({usage['usage_limit']} per day). Try again tomorrow.",
                    "usage_today":     usage["usage_today"],
                    "usage_limit":     usage["usage_limit"],
                    "remaining_today": usage["remaining_today"],
                },
                429,
            )

        logger(
            "JOB_ENGINE",
            f"analyse/role request started | freelancer_id={fid} | job_role_id={job_role_id}",
            level="INFO",
        )

        result = await analyse_role_match(db, fid, job_role_id)

        total_ms = (time.perf_counter() - t_request) * 1000

        if "error" in result:
            logger(
                "JOB_ENGINE",
                f"RAG analysis returned error | freelancer_id={fid} | job_role_id={job_role_id} "
                f"| error={result['error']} | time={total_ms:.0f}ms",
                level="WARNING",
            )
            return ResponseSchema.error(
                {
                    "message":         result["error"],
                    "usage_today":     usage["usage_today"],
                    "usage_limit":     usage["usage_limit"],
                    "remaining_today": usage["remaining_today"],
                },
                502,
            )

        result["usage_today"]     = usage["usage_today"]
        result["usage_limit"]     = usage["usage_limit"]
        result["remaining_today"] = usage["remaining_today"]

        logger(
            "JOB_ENGINE",
            f"analyse/role complete | freelancer_id={fid} | job_role_id={job_role_id} "
            f"| match_score={result.get('match_score')} | recommendation={result.get('recommendation')} "
            f"| total_time={total_ms:.0f}ms",
            level="INFO",
        )
        return ResponseSchema.success(result, 200)

    except Exception as e:
        total_ms = (time.perf_counter() - t_request) * 1000
        logger("JOB_ENGINE", f"Error in RAG analysis after {total_ms:.0f}ms | error={e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.get("/usage")
async def get_usage(
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """Read-only lookup of today's job-fit analysis usage, without spending a request."""
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()
        usage = get_daily_usage(db, fid)
        return ResponseSchema.success(usage, 200)
    except Exception as e:
        logger("JOB_ENGINE", f"Error fetching usage: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


# dev only — background worker (embedding_sweep_loop) already runs this on a timer;
# this is just a manual "run it now" trigger for testing.
@router.post("/sweep")
async def trigger_sweep(current_user: UserInDB = Depends(get_current_user)):
    """Force a dirty-embedding sweep now instead of waiting for the loop."""
    try:
        result = await run_sweep_once()
        logger("JOB_ENGINE", "Manual sweep complete", level="INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_ENGINE", f"Sweep error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


