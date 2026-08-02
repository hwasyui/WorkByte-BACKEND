import os
from typing import Dict

from functions.logger import logger

DAILY_JOB_FIT_ANALYSIS_LIMIT = int(os.getenv("JOB_FIT_ANALYSIS_DAILY_LIMIT", "10"))


def check_and_increment_daily_usage(db, freelancer_id: str) -> Dict:
    """Bump today's count and say whether the freelancer is still under the limit.

    Runs before the LLM call. A request that fails later still counts - the key pool
    was spent either way, and nothing hands the attempt back.
    Returns {allowed, usage_today, usage_limit, remaining_today}.
    """
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
    usage_today = int(row[0]["request_count"]) if row else 1
    allowed = usage_today <= DAILY_JOB_FIT_ANALYSIS_LIMIT
    remaining = max(0, DAILY_JOB_FIT_ANALYSIS_LIMIT - usage_today)

    logger(
        "JOB_ENGINE",
        f"Daily usage checked | freelancer_id={freelancer_id} | usage_today={usage_today} "
        f"| limit={DAILY_JOB_FIT_ANALYSIS_LIMIT} | allowed={allowed}",
        level="INFO" if allowed else "WARNING",
    )
    return {
        "allowed":         allowed,
        "usage_today":     usage_today,
        "usage_limit":     DAILY_JOB_FIT_ANALYSIS_LIMIT,
        "remaining_today": remaining,
    }


def get_daily_usage(db, freelancer_id: str) -> Dict:
    """
    Read-only lookup of today's usage count, without incrementing it.
    Lets the frontend show "X of N used today" or disable the analysis button
    ahead of time without spending a real call to find out.
    """
    row = db.execute_query(
        """
        SELECT request_count
        FROM job_fit_analysis_usage
        WHERE freelancer_id = :fid AND usage_date = CURRENT_DATE
        """,
        {"fid": freelancer_id},
    )
    usage_today = int(row[0]["request_count"]) if row else 0
    remaining = max(0, DAILY_JOB_FIT_ANALYSIS_LIMIT - usage_today)
    return {
        "usage_today":     usage_today,
        "usage_limit":     DAILY_JOB_FIT_ANALYSIS_LIMIT,
        "remaining_today": remaining,
    }
