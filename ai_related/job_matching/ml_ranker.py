"""
ML Ranker — Stage 3 of the job matching pipeline.

Takes the top-100 pgvector candidates and re-ranks them with a LightGBM model
that predicts match_probability for each (freelancer, job) pair.

Stage 1 (pgvector cosine → top-100) and Stage 2 (structured pre-filter) run
in job_matching_routes.py before this is called.

Feature columns must match what was used during training in
machine_learning/02_train_model.ipynb (saved in models/feature_cols.json).
"""

import os
import json
import time

import numpy as np
import pandas as pd
import joblib

from functions.logger import logger

# loaded once on first call
_MODEL_DIR = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "lgbm_job_matcher.pkl")
_FEAT_PATH  = os.path.join(_MODEL_DIR, "feature_cols.json")

_model = None
_feat_cols: list[str] | None = None


def _load_model():
    """
    Load the LightGBM model and feature column list from disk, caching them in module globals.

    Returns:
        Tuple of (loaded LightGBM model, list of feature column names).

    Raises:
        FileNotFoundError: If the model file does not exist at the expected path.
    """
    global _model, _feat_cols
    if _model is None:
        if not os.path.exists(_MODEL_PATH):
            raise FileNotFoundError(
                f"LightGBM model not found at {_MODEL_PATH}. "
                "Run 02_train_model.ipynb first."
            )
        t0 = time.perf_counter()
        _model = joblib.load(_MODEL_PATH)
        load_ms = (time.perf_counter() - t0) * 1000
        with open(_FEAT_PATH) as fh:
            _feat_cols = json.load(fh)
        model_kb = os.path.getsize(_MODEL_PATH) / 1024
        logger(
            "ML_RANKER",
            f"LightGBM model loaded | path={_MODEL_PATH} | size={model_kb:.0f}KB "
            f"| features={len(_feat_cols)} | load_time={load_ms:.1f}ms",
            level="INFO",
        )
    return _model, _feat_cols


_EXP_MAP = {"entry_level": 1, "intermediate": 2, "expert": 3}


def _infer_exp_level(total_projects: int) -> int:
    """
    Approximate a freelancer's experience level from their total completed projects.

    The freelancer table has no experience_level column, so this heuristic mirrors
    the one used during model training: 1 = entry_level, 2 = intermediate, 3 = expert.

    Args:
        total_projects: Total number of completed projects on the freelancer's profile.

    Returns:
        Integer level: 1 (entry_level, <3 projects), 2 (intermediate, 3-9), 3 (expert, 10+).
    """
    if total_projects >= 10:
        return 3  # expert
    if total_projects >= 3:
        return 2  # intermediate
    return 1      # entry_level


def _load_freelancer_context(db, freelancer_id: str) -> dict | None:
    """
    Load all freelancer signals needed for feature engineering in a single DB pass.

    Fetches rate, project count, skills, specialities, languages, portfolio count,
    work experience count, and performance metrics. Results are reused for every
    candidate job row so the DB is queried only once per ranking call.

    Args:
        db: Active database connection.
        freelancer_id: UUID string of the freelancer being ranked.

    Returns:
        Dict of freelancer signals, or None if the freelancer is not found.
    """
    logger("ML_RANKER", f"Loading freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_row = db.execute_query(
        """
        SELECT f.estimated_rate, f.rate_time, f.total_jobs,
               pr.overall_performance_score, pr.success_rate, pr.total_ratings_received
        FROM freelancer f
        LEFT JOIN performance_rating pr ON pr.freelancer_id = f.freelancer_id
        WHERE f.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    if not f_row:
        logger("ML_RANKER", f"Freelancer not found in DB | freelancer_id={freelancer_id}", level="WARNING")
        return None
    row = dict(f_row[0])

    # Skills
    f_skills = db.execute_query(
        "SELECT skill_id FROM freelancer_skill WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )
    skill_ids = {str(r["skill_id"]) for r in f_skills}

    # Specialities
    f_specs = db.execute_query(
        """
        SELECT s.speciality_name
        FROM freelancer_speciality fs
        JOIN speciality s ON s.speciality_id = fs.speciality_id
        WHERE fs.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    spec_names = {r["speciality_name"].lower() for r in f_specs}

    # Languages
    f_langs = db.execute_query(
        """
        SELECT l.language_name
        FROM freelancer_language fl
        JOIN language l ON l.language_id = fl.language_id
        WHERE fl.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    lang_names = {r["language_name"].lower() for r in f_langs}

    # Portfolio + work experience counts
    port = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM portfolio WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )
    work_exp = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM work_experience WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )

    total_proj = int(row.get("total_jobs") or 0)
    ratings_count = int(row.get("total_ratings_received") or 0)
    is_cold_start = 1 if ratings_count == 0 else 0
    exp_num = _infer_exp_level(total_proj)

    perf_score = (
        float(row["overall_performance_score"])
        if ratings_count > 0 and row.get("overall_performance_score") is not None
        else float("nan")
    )
    success_rate = (
        float(row["success_rate"])
        if ratings_count > 0 and row.get("success_rate") is not None
        else float("nan")
    )

    logger(
        "ML_RANKER",
        f"Freelancer context loaded | freelancer_id={freelancer_id} "
        f"| skills={len(skill_ids)} | specs={list(spec_names)} | langs={list(lang_names)} "
        f"| rate={row.get('estimated_rate')} | projects={total_proj} "
        f"| exp_level={exp_num} | cold_start={bool(is_cold_start)} "
        f"| performance={perf_score:.1f} | success_rate={success_rate:.1f} "
        f"| portfolio={port[0]['cnt'] if port else 0} | work_exp={work_exp[0]['cnt'] if work_exp else 0}",
        level="DEBUG",
    )

    return {
        "rate":           float(row.get("estimated_rate") or 0),
        "total_jobs": total_proj,
        "exp_num":        exp_num,
        "skill_ids":      skill_ids,
        "spec_names":     spec_names,
        "lang_names":     lang_names,
        "has_portfolio":  float(1 if port and port[0]["cnt"] > 0 else 0),
        "work_exp_count": float(work_exp[0]["cnt"] if work_exp else 0),
        "performance_score": perf_score,
        "success_rate_hist": success_rate,
        "is_cold_start": float(is_cold_start),
    }


def _compute_job_features(db, fc: dict, job: dict) -> dict:
    """
    Compute the 18 ML features for a single (freelancer, job) pair.

    Args:
        db: Active database connection used to fetch role skills and budgets.
        fc: Freelancer context dict returned by ``_load_freelancer_context``.
        job: One candidate row from the pgvector query, including ``job_post_id``
             and ``similarity_score``.

    Returns:
        Dict mapping each feature name to its computed float value, ready to be
        passed to the LightGBM model.
    """
    jp_id = str(job["job_post_id"])
    cosine_sim = float(job.get("similarity_score", 0))

    roles = db.execute_query(
        "SELECT job_role_id, role_budget, budget_type FROM job_role WHERE job_post_id = :jpid",
        {"jpid": jp_id},
    )

    req_skills: set[str] = set()
    pref_skills: set[str] = set()
    total_budget = 0.0
    budget_count = 0

    for role in roles:
        rid = str(role["job_role_id"])
        role_skills = db.execute_query(
            "SELECT skill_id, is_required FROM job_role_skill WHERE job_role_id = :rid",
            {"rid": rid},
        )
        for rs in role_skills:
            sid = str(rs["skill_id"])
            if rs["is_required"]:
                req_skills.add(sid)
            else:
                pref_skills.add(sid)

        if role.get("role_budget"):
            total_budget += float(role["role_budget"])
            budget_count += 1

    avg_budget = total_budget / budget_count if budget_count > 0 else 0.0

    skill_required_total   = len(req_skills)
    skill_required_matched = len(fc["skill_ids"] & req_skills)
    skill_overlap_pct      = (
        skill_required_matched / skill_required_total if skill_required_total > 0 else 0.0
    )
    skill_preferred_pct = (
        len(fc["skill_ids"] & pref_skills) / len(pref_skills) if pref_skills else 0.0
    )

    job_exp_num = _EXP_MAP.get(str(job.get("experience_level") or "entry_level"), 1)
    experience_level_match = float(1 if fc["exp_num"] >= job_exp_num else 0)
    exp_delta = float(max(-2, min(2, fc["exp_num"] - job_exp_num)))

    if avg_budget > 0 and fc["rate"] > 0:
        rate_ratio   = min(3.0, fc["rate"] / avg_budget)
        rate_in_budget = float(1 if fc["rate"] <= avg_budget * 1.1 else 0)
    else:
        rate_ratio   = 1.0
        rate_in_budget = 1.0

    common_lang_set = {"english", "indonesian", "bahasa indonesia"}
    language_match  = float(1 if fc["lang_names"] & common_lang_set else 0)

    job_title_lower = str(job.get("job_title", "")).lower()
    src_text_lower  = str(job.get("source_text", "")).lower()

    speciality_match = float(
        1 if fc["spec_names"] and any(sp in job_title_lower for sp in fc["spec_names"]) else 0
    )
    domain_match = float(
        1 if fc["spec_names"] and any(sp in src_text_lower for sp in fc["spec_names"]) else 0
    )

    logger(
        "ML_RANKER",
        f"Features computed | job_post_id={jp_id} | job_title='{job.get('job_title', '')[:40]}' "
        f"| cosine={cosine_sim:.4f} | skill_overlap={skill_overlap_pct:.2%} "
        f"({skill_required_matched}/{skill_required_total} required) "
        f"| preferred={skill_preferred_pct:.2%} | exp_match={bool(experience_level_match)} "
        f"| exp_delta={exp_delta:+.0f} | rate_in_budget={bool(rate_in_budget)} "
        f"| rate_ratio={rate_ratio:.2f} | domain_match={bool(domain_match)} "
        f"| lang_match={bool(language_match)} | spec_match={bool(speciality_match)}",
        level="DEBUG",
    )

    return {
        "job_post_id":            jp_id,
        "cosine_sim":             cosine_sim,
        "skill_overlap_pct":      skill_overlap_pct,
        "skill_required_matched": float(skill_required_matched),
        "skill_required_total":   float(skill_required_total),
        "skill_preferred_pct":    skill_preferred_pct,
        "experience_level_match": experience_level_match,
        "exp_delta":              exp_delta,
        "rate_in_budget":         rate_in_budget,
        "rate_ratio":             rate_ratio,
        "language_match":         language_match,
        "speciality_match":       speciality_match,
        "domain_match":           domain_match,
        "has_portfolio":          fc["has_portfolio"],
        "work_exp_count":         fc["work_exp_count"],
        "performance_score":      fc["performance_score"],
        "success_rate_hist":      fc["success_rate_hist"],
        "total_projects":         float(fc["total_jobs"]),  # LightGBM feature name unchanged (must match feature_cols.json)
        "is_cold_start":          fc["is_cold_start"],
    }


def rank_jobs_with_ml(
    db,
    freelancer_id: str,
    job_rows: list[dict],
    top_n: int = 10,
) -> list[dict]:
    """
    Stage 3 — re-rank pgvector candidates with LightGBM.

    Takes job_rows from the pgvector query (with similarity_score), runs them
    through the LightGBM model, and returns the top_n results sorted by
    match_probability. Each returned dict gets match_probability (0-100) and
    skill_overlap_pct (0-100) added. Falls back to cosine ordering if the model
    fails to load or predict.
    """
    if not job_rows:
        logger("ML_RANKER", "rank_jobs_with_ml called with empty job_rows — returning []", level="WARNING")
        return []

    logger(
        "ML_RANKER",
        f"Stage 3 started | freelancer_id={freelancer_id} | candidates={len(job_rows)} | top_n={top_n}",
        level="INFO",
    )
    t_start = time.perf_counter()

    try:
        model, feat_cols = _load_model()

        # Load freelancer signals once (reused for all 100 jobs)
        t_ctx = time.perf_counter()
        fc = _load_freelancer_context(db, freelancer_id)
        ctx_ms = (time.perf_counter() - t_ctx) * 1000
        logger("ML_RANKER", f"Freelancer context loaded | time={ctx_ms:.1f}ms", level="DEBUG")

        if fc is None:
            logger("ML_RANKER", f"Freelancer {freelancer_id} not found — skipping ML, falling back to cosine", level="WARNING")
            return job_rows[:top_n]

        # Compute features per job
        t_feat = time.perf_counter()
        feature_rows = [_compute_job_features(db, fc, job) for job in job_rows]
        feat_ms = (time.perf_counter() - t_feat) * 1000
        feat_df = pd.DataFrame(feature_rows)

        nan_cols = feat_df[feat_cols].isnull().any()
        nan_features = list(nan_cols[nan_cols].index)
        logger(
            "ML_RANKER",
            f"Features built | jobs={len(feature_rows)} | time={feat_ms:.1f}ms "
            f"| NaN features={nan_features} (expected: performance_score, success_rate_hist for cold start)",
            level="DEBUG",
        )

        # align column order to training, then batch predict
        X = feat_df[feat_cols]
        t_pred = time.perf_counter()
        probs = model.predict_proba(X)[:, 1]
        pred_ms = (time.perf_counter() - t_pred) * 1000

        logger(
            "ML_RANKER",
            f"LightGBM inference | candidates={len(job_rows)} | time={pred_ms:.2f}ms "
            f"| prob_range=[{probs.min():.3f}, {probs.max():.3f}] | prob_mean={probs.mean():.3f}",
            level="INFO",
        )

        is_cold_start = bool(fc["is_cold_start"])
        if is_cold_start or probs.max() < 0.05:
            cold_probs = []
            for feat_row in feature_rows:
                cosine  = float(feat_row["cosine_sim"])
                overlap = float(feat_row["skill_overlap_pct"])
                # base 5 % floor + cosine signal * skill confirmation
                p = 0.05 + max(0.0, cosine - 0.5) * 0.4 * overlap
                cold_probs.append(min(0.45, p))
            probs = np.array(cold_probs, dtype=float)
            logger(
                "ML_RANKER",
                f"Cold-start heuristic applied | is_cold_start={is_cold_start} "
                f"| prob_range=[{probs.min():.3f}, {probs.max():.3f}]",
                level="INFO",
            )

        for i, job in enumerate(job_rows):
            job["match_probability"] = round(float(probs[i]) * 100, 1)
            job["skill_overlap_pct"] = round(
                float(feat_df.iloc[i]["skill_overlap_pct"]) * 100, 1
            )

        ranked = sorted(job_rows, key=lambda j: j["match_probability"], reverse=True)
        top = ranked[:top_n]

        total_ms = (time.perf_counter() - t_start) * 1000
        top_preview = [
            f"#{i+1} {j.get('job_title','?')[:30]} → {j['match_probability']}% (cosine={j.get('similarity_score',0):.3f})"
            for i, j in enumerate(top[:5])
        ]
        logger(
            "ML_RANKER",
            f"Stage 3 complete | freelancer_id={freelancer_id} | returned={len(top)}/{len(job_rows)} "
            f"| total_time={total_ms:.1f}ms | top5={top_preview}",
            level="INFO",
        )
        return top

    except Exception as e:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger(
            "ML_RANKER",
            f"ML ranking failed after {total_ms:.1f}ms — falling back to cosine ordering | error={e}",
            level="WARNING",
        )
        # Graceful fallback — return cosine-ordered results unchanged
        return sorted(job_rows, key=lambda j: j.get("similarity_score", 0), reverse=True)[:top_n]
