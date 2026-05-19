"""
Job matching routes, all mounted under /ai/job_matching (set in main.py).

  GET  /match/freelancer-to-jobs        — 3-stage ranked feed (pgvector → skill filter → ML)
  GET  /analyse/job/{job_post_id}       — RAG + LLM deep-fit analysis for a specific job
  POST /embed/freelancer/{id}           — queue freelancer re-embedding
  POST /embed/job/{id}                  — queue job re-embedding
  POST /sweep                           — run dirty-embedding sweep immediately
  GET  /test_ai_local                   — Ollama connectivity check

Client-side candidate search (cosine only) lives at:
  GET /job-posts/{job_post_id}/candidates
"""

import os
import time
import uuid
import httpx
from fastapi import APIRouter, Depends, Query
from typing import Optional

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import get_freelancer_profile_for_user
from functions.response_utils import ResponseSchema
from functions.logger import logger
from functions.db_manager import get_db
from ai_related.job_matching.sweep_worker import run_sweep_once
from ai_related.job_matching.ml_ranker import rank_jobs_with_ml
from ai_related.job_matching.rag_analyser import analyse_job_match

# Stage 2 minimum skill overlap — jobs below this are dropped before Stage 3.
_MIN_SKILL_OVERLAP = 0.20  # 20%

router = APIRouter()


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
    3-stage ranked job feed for the authenticated freelancer.

    Stage 1  pgvector cosine search — retrieves the top-100 semantically
             relevant open jobs using the freelancer's embedding vector.

    Stage 2  Skill filter — drops any job whose best-matching role has less
             than 20% required-skill overlap with the freelancer's skills.
             Per-role logic: overlap is computed independently for each role
             in the job post and the highest value is kept, so a backend
             engineer is not penalised for skills from an unrelated role in
             the same post (e.g. financial manager, UI designer).

    Stage 3  ML re-ranking — CatBoost model scores each remaining candidate
             across 13 features and returns a calibrated match_probability (0–100).
             Features: cosine_sim, portfolio_relevance, skill_overlap_pct,
             skill_required_matched, skill_required_total, skill_depth,
             experience_level_match, exp_delta, rate_in_budget, rate_ratio,
             speciality_match, work_exp_count, total_jobs.
             Returns the top-N sorted by match_probability, each with
             match_reasons (up to 3 positive SHAP contributors) and
             penalty_reasons (up to 2 negative SHAP contributors).
    """
    t_request = time.perf_counter()
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        logger(
            "JOB_MATCHING",
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
            logger("JOB_MATCHING", f"No embedding found for freelancer | freelancer_id={fid}", level="WARNING")
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
                WHERE jp.status = 'active' {exp_filter}
                ORDER BY jp.job_post_id, similarity_score DESC
            ) deduped
            ORDER BY similarity_score DESC
            LIMIT :stage1_limit
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
            "JOB_MATCHING",
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
        logger(
            "JOB_MATCHING",
            f"Stage 2 started | freelancer_id={fid} | freelancer_skill_count={len(f_skill_ids)} "
            f"| min_overlap_threshold={_MIN_SKILL_OVERLAP:.0%}",
            level="DEBUG",
        )

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
                        "JOB_MATCHING",
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
            "JOB_MATCHING",
            f"Stage 2 complete | freelancer_id={fid} | passed={len(filtered)} "
            f"| dropped_low_overlap={dropped_overlap} | dropped_no_skills_job={dropped_no_skills} "
            f"| time={stage2_ms:.1f}ms",
            level="INFO",
        )

        if not filtered:
            total_ms = (time.perf_counter() - t_request) * 1000
            logger(
                "JOB_MATCHING",
                f"No candidates passed Stage 2 filter | freelancer_id={fid} | total_time={total_ms:.1f}ms",
                level="WARNING",
            )
            return ResponseSchema.success({"matches": [], "count": 0, "stage": "pre-filter_empty"}, 200)

        # Stage 3: ML re-rank
        t3 = time.perf_counter()
        ranked = rank_jobs_with_ml(db, fid, filtered, top_n=limit)
        stage3_ms = (time.perf_counter() - t3) * 1000

        total_ms = (time.perf_counter() - t_request) * 1000
        logger(
            "JOB_MATCHING",
            f"freelancer-to-jobs complete | freelancer_id={fid} | returned={len(ranked)} "
            f"| stage1={stage1_ms:.0f}ms | stage2={stage2_ms:.0f}ms | stage3={stage3_ms:.0f}ms "
            f"| total={total_ms:.0f}ms",
            level="INFO",
        )
        return ResponseSchema.success({"matches": ranked, "count": len(ranked)}, 200)

    except Exception as e:
        total_ms = (time.perf_counter() - t_request) * 1000
        logger("JOB_MATCHING", f"Error in freelancer-to-jobs after {total_ms:.0f}ms | error={e}", level="ERROR")
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
    LLM calls can take 5-30s — this is user-triggered so the latency is fine.
    """
    t_request = time.perf_counter()
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        logger(
            "JOB_MATCHING",
            f"analyse/job request started | freelancer_id={fid} | job_post_id={job_post_id}",
            level="INFO",
        )

        result = await analyse_job_match(db, fid, job_post_id)

        total_ms = (time.perf_counter() - t_request) * 1000

        if "error" in result and len(result) == 1:
            logger(
                "JOB_MATCHING",
                f"RAG analysis returned error | freelancer_id={fid} | job_post_id={job_post_id} "
                f"| error={result['error']} | time={total_ms:.0f}ms",
                level="WARNING",
            )
            return ResponseSchema.error(result["error"], 502)

        logger(
            "JOB_MATCHING",
            f"analyse/job complete | freelancer_id={fid} | job_post_id={job_post_id} "
            f"| match_score={result.get('match_score')} | recommendation={result.get('recommendation')} "
            f"| total_time={total_ms:.0f}ms",
            level="INFO",
        )
        return ResponseSchema.success(result, 200)

    except Exception as e:
        total_ms = (time.perf_counter() - t_request) * 1000
        logger("JOB_MATCHING", f"Error in RAG analysis after {total_ms:.0f}ms | error={e}", level="ERROR")
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
        logger("JOB_MATCHING", f"Marked freelancer embedding dirty | freelancer_id={freelancer_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "freelancer_id": freelancer_id}, 202)
    except Exception as e:
        logger("JOB_MATCHING", f"Error queueing freelancer embed: {e}", level="ERROR")
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
        from ai_related.job_matching.embedding_manager import mark_job_dirty
        mark_job_dirty(job_post_id)
        logger("JOB_MATCHING", f"Marked all role embeddings dirty | job_post_id={job_post_id}", level="INFO")
        return ResponseSchema.success({"message": "Embedding queued", "job_post_id": job_post_id}, 202)
    except Exception as e:
        logger("JOB_MATCHING", f"Error queueing job embed: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.post("/sweep")
async def trigger_sweep(current_user: UserInDB = Depends(get_current_user)):
    """Manually trigger the dirty-embedding sweep (re-embeds all dirty records now)."""
    try:
        result = await run_sweep_once()
        logger("JOB_MATCHING", "Manual sweep complete", level="INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_MATCHING", f"Sweep error: {e}", level="ERROR")
        return ResponseSchema.error(str(e), 500)


@router.get("/test_ai_local")
async def test_ai_local():
    """Test the local Ollama instance by sending a simple prompt."""
    ollama_url = os.getenv("OLLAMA_URL")
    if not ollama_url:
        return ResponseSchema.error("OLLAMA_URL not set in environment", 500)
    if "127.0.0.1" in ollama_url:
        ollama_url = ollama_url.replace("127.0.0.1", "host.docker.internal")

    payload = {
        "model": os.getenv("OLLAMA_LLM", "gemma4:e2b"),
        "prompt": "Hello, can you respond with a simple greeting?",
        "stream": False,
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(ollama_url, json=payload, timeout=30.0)
            if response.status_code == 200:
                return ResponseSchema.success({"response": response.json().get("response", "")})
            return ResponseSchema.error(f"Ollama error: {response.status_code}", response.status_code)
    except httpx.ConnectError:
        return ResponseSchema.error("Cannot connect to Ollama. Ensure it is running.", 503)
    except httpx.TimeoutException:
        return ResponseSchema.error("Ollama request timed out.", 504)
    except Exception as e:
        return ResponseSchema.error(str(e), 500)
