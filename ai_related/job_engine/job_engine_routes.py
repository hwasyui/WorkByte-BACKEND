import time
import uuid
from fastapi import APIRouter, Depends

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import get_freelancer_profile_for_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_engine.sweep_worker import run_sweep_once
from ai_related.job_engine.rag_analyser import analyse_role_match
from ai_related.job_engine.embedding_manager import mark_job_dirty

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
    RAG + LLM analysis of the freelancer's fit for a specific job role.
    Retrieves the role's requirements, the freelancer's profile, and relevant past
    contracts from the DB, then asks the LLM for a structured JSON response
    with match_score, strengths, gaps, recommendation, and skill_tips.
    LLM calls can take 5-30s; this is user-triggered so the latency is acceptable.
    """
    t_request = time.perf_counter()
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        logger(
            "JOB_ENGINE",
            f"analyse/role request started | freelancer_id={fid} | job_role_id={job_role_id}",
            level="INFO",
        )

        result = await analyse_role_match(db, fid, job_role_id)

        total_ms = (time.perf_counter() - t_request) * 1000

        if "error" in result and len(result) == 1:
            logger(
                "JOB_ENGINE",
                f"RAG analysis returned error | freelancer_id={fid} | job_role_id={job_role_id} "
                f"| error={result['error']} | time={total_ms:.0f}ms",
                level="WARNING",
            )
            return ResponseSchema.error(result["error"], 502)

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


@router.post("/embed/freelancer/{freelancer_id}")
async def embed_freelancer(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Mark a freelancer's embedding as dirty so the next sweep re-generates it.
    Creates the embedding row if it doesn't exist yet.
    Call POST /sweep right after to generate the vector immediately.
    """
    try:
        db = get_db()
        existing = db.execute_query(
            "SELECT embedding_id FROM freelancer_embedding WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )
        if existing:
            db.execute_query(
                "UPDATE freelancer_embedding SET embedding_dirty = TRUE WHERE freelancer_id = :fid",
                {"fid": freelancer_id},
            )
        else:
            db.execute_query(
                """INSERT INTO freelancer_embedding (embedding_id, freelancer_id, embedding_dirty)
                   VALUES (:eid, :fid, TRUE)""",
                {"eid": str(uuid.uuid4()), "fid": freelancer_id},
            )
        logger("JOB_ENGINE", f"Marked freelancer embedding dirty | freelancer_id={freelancer_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "freelancer_id": freelancer_id}, 202)
    except Exception as e:
        logger("JOB_ENGINE", f"Error queueing freelancer embed: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.post("/embed/job/{job_post_id}")
async def embed_job(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Mark all role embeddings for a job as dirty so the next sweep re-generates them.
    Creates dirty rows for any roles that don't have an embedding row yet.
    Call POST /sweep right after to generate the vectors immediately.
    """
    try:
        mark_job_dirty(job_post_id)
        logger("JOB_ENGINE", f"Marked all role embeddings dirty | job_post_id={job_post_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "job_post_id": job_post_id}, 202)
    except Exception as e:
        logger("JOB_ENGINE", f"Error queueing job embed: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.post("/sweep")
async def trigger_sweep(current_user: UserInDB = Depends(get_current_user)):
    """Manually trigger the dirty-embedding sweep (re-embeds all dirty records now)."""
    try:
        result = await run_sweep_once()
        logger("JOB_ENGINE", "Manual sweep complete", level="INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_ENGINE", f"Sweep error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


