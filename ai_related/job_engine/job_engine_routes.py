import time
import uuid
from fastapi import APIRouter, Depends, Query
from typing import Optional

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import get_freelancer_profile_for_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_engine.sweep_worker import run_sweep_once
from ai_related.job_engine.ml_ranker import rank_jobs_with_ml
from ai_related.job_engine.rag_analyser import analyse_job_match
from ai_related.job_engine.embedding_manager import mark_job_dirty

# Stage 2 minimum skill overlap: jobs below this are dropped before Stage 3.
_MIN_SKILL_OVERLAP = 0.20  # 20%
# Freelancers with fewer skills than this bypass the overlap filter entirely.
_SPARSE_SKILL_THRESHOLD = 5

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


@router.get("/match/freelancer-to-jobs")
async def match_freelancer_to_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    experience_level: Optional[str] = None,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    3-stage ranked job feed: pgvector cosine (top-100) then skill overlap filter
    (min 20%) then CatBoost ML re-ranking across 13 features.
    Returns top-N jobs sorted by match probability, each with match_reasons and
    penalty_reasons derived from SHAP values.
    """
    t_request = time.perf_counter()
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        logger(
            "JOB_ENGINE",
            f"freelancer-to-jobs request started | freelancer_id={fid} | limit={limit} "
            f"| experience_level_filter={experience_level}",
            level="INFO",
        )

        # Stage 1: pgvector cosine, top-100
        t1 = time.perf_counter()
        fe = db.execute_query(
            "SELECT embedding_vector FROM freelancer_embedding WHERE freelancer_id = :fid AND embedding_vector IS NOT NULL",
            {"fid": fid},
        )
        if not fe:
            logger("JOB_ENGINE", f"No embedding found for freelancer | freelancer_id={fid}", level="WARNING")
            return ResponseSchema.error(
                "Freelancer profile not yet indexed. Please wait a moment and try again.", 404
            )

        freelancer_vec = fe[0]["embedding_vector"]

        # inner_limit > stage1_limit so DISTINCT ON dedup still yields enough
        # unique job posts even when one job has many matching roles.
        params: dict = {"vec": freelancer_vec, "stage1_limit": 100, "inner_limit": 300}

        exp_filter = ""
        if experience_level:
            exp_filter = "AND jp.experience_level = :exp_level"
            params["exp_level"] = experience_level

        stage1_query = f"""
            SELECT * FROM (
                SELECT DISTINCT ON (jp.job_post_id)
                    jp.job_post_id,
                    jp.job_title,
                    jp.job_description,
                    jp.project_type,
                    jp.project_scope,
                    jp.experience_level,
                    jp.estimated_duration,
                    jp.working_days,
                    jp.deadline,
                    jp.proposal_count,
                    jre.source_text,
                    c.full_name AS client_name,
                    ROUND((1 - (jre.embedding_vector <=> CAST(:vec AS vector)))::numeric, 4) AS similarity_score
                FROM (
                    SELECT job_role_id, source_text, embedding_vector
                    FROM job_role_embedding
                    WHERE embedding_vector IS NOT NULL
                    ORDER BY embedding_vector <=> CAST(:vec AS vector)
                    LIMIT :inner_limit
                ) jre
                JOIN job_role jr ON jr.job_role_id = jre.job_role_id
                JOIN job_post jp ON jp.job_post_id = jr.job_post_id
                LEFT JOIN client c ON c.client_id = jp.client_id
                WHERE jp.status = 'active' {exp_filter}
                ORDER BY jp.job_post_id, similarity_score DESC
            ) deduped
            ORDER BY similarity_score DESC
            LIMIT :stage1_limit.
        """
        stage1_rows = db.execute_query(stage1_query, params)
        candidates = _serialize_rows(stage1_rows) if stage1_rows else []
        stage1_ms = (time.perf_counter() - t1) * 1000

        cosine_range = (
            f"[{min(c['similarity_score'] for c in candidates):.3f}, "
            f"{max(c['similarity_score'] for c in candidates):.3f}]"
            if candidates else "[]"
        )
        logger(
            "JOB_ENGINE",
            f"Stage 1 complete | freelancer_id={fid} | candidates={len(candidates)} "
            f"| cosine_range={cosine_range} | time={stage1_ms:.1f}ms",
            level="INFO",
        )

        # Stage 2: drop jobs with too little skill overlap
        t2 = time.perf_counter()
        f_skills = db.execute_query(
            "SELECT skill_id FROM freelancer_skill WHERE freelancer_id = :fid",
            {"fid": fid},
        )
        f_skill_ids = {str(r["skill_id"]) for r in f_skills}
        sparse_profile = len(f_skill_ids) < _SPARSE_SKILL_THRESHOLD
        logger(
            "JOB_ENGINE",
            f"Stage 2 started | freelancer_id={fid} | freelancer_skill_count={len(f_skill_ids)} "
            f"| min_overlap_threshold={_MIN_SKILL_OVERLAP:.0%}"
            + (" | skill_filter=bypassed (sparse profile)" if sparse_profile else ""),
            level="DEBUG",
        )

        if sparse_profile:
            for job in candidates:
                job["skill_overlap_pct"] = None
            filtered = candidates
            stage2_ms = (time.perf_counter() - t2) * 1000
            logger(
                "JOB_ENGINE",
                f"Stage 2 complete | freelancer_id={fid} | passed={len(filtered)} "
                f"| dropped_low_overlap=0 | dropped_no_skills_job=0 | time={stage2_ms:.1f}ms",
                level="INFO",
            )
        else:
            filtered = []
            dropped_no_skills = 0
            dropped_overlap = 0
            for job in candidates:
                jp_id = str(job["job_post_id"])

                role_rows = db.execute_query(
                    "SELECT job_role_id FROM job_role WHERE job_post_id = :jpid",
                    {"jpid": jp_id},
                )

                best_overlap = 0.0
                has_any_required_skills = False
                for role in (role_rows or []):
                    role_req = db.execute_query(
                        "SELECT skill_id FROM job_role_skill WHERE job_role_id = :rid AND is_required = TRUE",
                        {"rid": str(role["job_role_id"])},
                    )
                    role_req_ids = {str(r["skill_id"]) for r in role_req}
                    if role_req_ids:
                        has_any_required_skills = True
                        role_overlap = len(f_skill_ids & role_req_ids) / len(role_req_ids)
                        best_overlap = max(best_overlap, role_overlap)

                if has_any_required_skills:
                    if best_overlap < _MIN_SKILL_OVERLAP:
                        logger(
                            "JOB_ENGINE",
                            f"Stage 2 drop | job_post_id={jp_id} | title='{job.get('job_title','?')[:40]}' "
                            f"| best_role_overlap={best_overlap:.2%} < {_MIN_SKILL_OVERLAP:.0%}",
                            level="DEBUG",
                        )
                        dropped_overlap += 1
                        continue
                    job["skill_overlap_pct"] = round(best_overlap * 100, 1)
                else:
                    dropped_no_skills += 1
                    job["skill_overlap_pct"] = None

                filtered.append(job)

            stage2_ms = (time.perf_counter() - t2) * 1000
            logger(
                "JOB_ENGINE",
                f"Stage 2 complete | freelancer_id={fid} | passed={len(filtered)} "
                f"| dropped_low_overlap={dropped_overlap} | dropped_no_skills_job={dropped_no_skills} "
                f"| time={stage2_ms:.1f}ms",
                level="INFO",
            )

        if not filtered:
            total_ms = (time.perf_counter() - t_request) * 1000
            logger(
                "JOB_ENGINE",
                f"No candidates passed Stage 2 filter | freelancer_id={fid} | total_time={total_ms:.1f}ms",
                level="WARNING",
            )
            return ResponseSchema.success({
                "matches": [],
                "count": 0,
                "stage": "pre-filter_empty",
                "_debug": {
                    "stage1_candidates": len(candidates),
                    "stage2_dropped_overlap": dropped_overlap,
                    "stage2_dropped_no_skills": dropped_no_skills,
                    "freelancer_skill_count": len(f_skill_ids),
                },
            }, 200)

        # Stage 3: ML re-rank
        t3 = time.perf_counter()
        ranked = rank_jobs_with_ml(db, fid, filtered, top_n=limit)
        stage3_ms = (time.perf_counter() - t3) * 1000

        # match_probability is a ranking signal calibrated to a ~30%-positive
        # training distribution; it is not an absolute "% suitable" score and
        # would mislead users if displayed alongside the RAG match_score (which
        # uses explicit 0-100 thresholds). Strip it before sending to the client;
        # the ordering is already applied and match_reasons carry the explanation.
        for job in ranked:
            job.pop("match_probability", None)

        total_ms = (time.perf_counter() - t_request) * 1000
        logger(
            "JOB_ENGINE",
            f"freelancer-to-jobs complete | freelancer_id={fid} | returned={len(ranked)} "
            f"| stage1={stage1_ms:.0f}ms | stage2={stage2_ms:.0f}ms | stage3={stage3_ms:.0f}ms "
            f"| total={total_ms:.0f}ms",
            level="INFO",
        )
        return ResponseSchema.success({"matches": ranked, "count": len(ranked)}, 200)

    except Exception as e:
        total_ms = (time.perf_counter() - t_request) * 1000
        logger("JOB_ENGINE", f"Error in freelancer-to-jobs after {total_ms:.0f}ms | error={e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.get("/analyse/job/{job_post_id}")
async def analyse_job(
    job_post_id: str,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    RAG + LLM analysis of the freelancer's fit for a specific job.
    Retrieves job requirements, the freelancer's profile, and relevant past
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
            f"analyse/job request started | freelancer_id={fid} | job_post_id={job_post_id}",
            level="INFO",
        )

        result = await analyse_job_match(db, fid, job_post_id)

        total_ms = (time.perf_counter() - t_request) * 1000

        if "error" in result and len(result) == 1:
            logger(
                "JOB_ENGINE",
                f"RAG analysis returned error | freelancer_id={fid} | job_post_id={job_post_id} "
                f"| error={result['error']} | time={total_ms:.0f}ms",
                level="WARNING",
            )
            return ResponseSchema.error(result["error"], 502)

        logger(
            "JOB_ENGINE",
            f"analyse/job complete | freelancer_id={fid} | job_post_id={job_post_id} "
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


