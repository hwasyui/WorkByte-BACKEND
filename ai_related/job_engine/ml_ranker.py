import os
import json
import math
import re
import time
import urllib.request
from datetime import date

import numpy as np
import pandas as pd
import joblib

from functions.logger import logger

# loaded once on first call
_MODEL_DIR  = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "job_recommender.pkl")
_FEAT_PATH  = os.path.join(_MODEL_DIR, "feature_cols.json")

_model = None
_feat_cols: list[str] | None = None

# Currency helpers

_CURRENCY_RATES_PATH = os.path.join(os.path.dirname(__file__), "currency_rates.json")
_currency_rates: dict[str, float] | None = None
_currency_rates_fetched_at: float = 0.0
_CURRENCY_REFRESH_SECONDS = 604800  # re-fetch once per week


def _load_currency_rates() -> dict[str, float]:
    """
    Return exchange rates (X per 1 USD). Tries to refresh from frankfurter.app
    once per week; falls back to the bundled currency_rates.json on any failure.
    """
    global _currency_rates, _currency_rates_fetched_at

    now = time.time()
    if _currency_rates is not None and (now - _currency_rates_fetched_at) < _CURRENCY_REFRESH_SECONDS:
        return _currency_rates

    try:
        with urllib.request.urlopen(
            "https://api.frankfurter.app/latest?from=USD", timeout=4
        ) as resp:
            data = json.loads(resp.read())
        rates = data.get("rates", {})
        rates["USD"] = 1.0
        _currency_rates = {k.upper(): float(v) for k, v in rates.items()}
        _currency_rates_fetched_at = now
        logger("ML_RANKER", f"Currency rates refreshed | {len(_currency_rates)} currencies", level="INFO")
        return _currency_rates
    except Exception as e:
        logger("ML_RANKER", f"Currency rate fetch failed ({e}), using bundled rates", level="WARNING")

    try:
        with open(_CURRENCY_RATES_PATH) as fh:
            data = json.load(fh)
        _currency_rates = {k.upper(): float(v) for k, v in data["rates"].items()}
        _currency_rates_fetched_at = now
        return _currency_rates
    except Exception as e:
        logger("ML_RANKER", f"Failed to load bundled rates ({e}), defaulting USD-only", level="ERROR")
        _currency_rates = {"USD": 1.0}
        return _currency_rates


def _to_usd(amount: float, currency: str) -> float:
    """Convert an amount in any currency to USD."""
    if amount <= 0:
        return 0.0
    rates = _load_currency_rates()
    rate = rates.get(currency.upper(), 1.0)
    return amount / rate if rate > 0 else amount


# Hours per month by rate_time, normalises freelancer rate to a monthly cost
# so it's comparable to the job's fixed project budget.
_RATE_TO_MONTHLY_HOURS: dict[str, float] = {
    "hourly":   160.0,   # 40 hrs/week × 4 weeks
    "daily":     20.0,   # 5 days/week × 4 weeks
    "weekly":     4.0,
    "monthly":    1.0,
    "annually":   1.0 / 12.0,
}


def _load_model():
    """
    Load the CatBoost model and feature column list from disk, caching in module globals.

    feature_cols.json can be either a plain list of feature names or an object
    of the form {"features": [...], ...}. Both shapes are accepted so the model
    artefact format from 02_train_model.ipynb stays flexible.

    Returns:
        Tuple of (model, feature_cols).

    Raises:
        FileNotFoundError: If the model file does not exist.
    """
    global _model, _feat_cols
    if _model is None:
        if not os.path.exists(_MODEL_PATH):
            raise FileNotFoundError(
                f"CatBoost model not found at {_MODEL_PATH}. "
                "Run TRAIN_MODEL.ipynb first."
            )
        t0 = time.perf_counter()
        _model = joblib.load(_MODEL_PATH)
        load_ms = (time.perf_counter() - t0) * 1000
        with open(_FEAT_PATH) as fh:
            raw = json.load(fh)

        if isinstance(raw, list):
            _feat_cols = raw
        else:
            _feat_cols = list(raw.get("features", []))

        model_kb = os.path.getsize(_MODEL_PATH) / 1024
        logger(
            "ML_RANKER",
            f"CatBoost model loaded | size={model_kb:.0f}KB "
            f"| features={len(_feat_cols)} | load_time={load_ms:.1f}ms",
            level="INFO",
        )
    return _model, _feat_cols


_EXP_MAP = {"entry": 1, "intermediate": 2, "expert": 3}

_DEFAULT_DURATION_MONTHS = 2.0


def _parse_duration_months(working_days=None, estimated_duration: str | None = None) -> float:
    """
    Return project duration in months to convert a total fixed budget into a
    monthly equivalent. Priority: working_days → estimated_duration text → default.
    project_scope is not used; it is auto-detected from the budget in this app,
    making it circular as a duration proxy.
    """
    if working_days and int(working_days) > 0:
        return int(working_days) / 20.0

    if estimated_duration:
        text = str(estimated_duration).lower().strip()
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if nums:
            avg = sum(float(n) for n in nums) / len(nums)
            if "week" in text:
                return max(0.25, avg / 4.33)
            if "day" in text:
                return max(0.05, avg / 20.0)
            if "month" in text:
                return max(0.25, avg)
            if "year" in text:
                return avg * 12.0

    return _DEFAULT_DURATION_MONTHS


# Proficiency → numeric weight for skill_depth. Trees see this as a continuous
# signal that distinguishes "all skills at expert" from "all skills at beginner",
# even when skill_overlap_pct is identical.
_PROFICIENCY_WEIGHT: dict[str, float] = {
    "expert":       1.00,
    "advanced":     0.75,
    "intermediate": 0.50,
    "beginner":     0.30,
}

# Recency-decay half-life (months) for portfolio/contract embedding cosine.
# A 24-month half-life means: same cosine, work done 2 years ago counts half as
# much as work done today. Tunes how aggressively recency shifts ranking.
_RECENCY_HALF_LIFE_MONTHS = 24.0
_RECENCY_DECAY_LAMBDA = math.log(2.0) / _RECENCY_HALF_LIFE_MONTHS


# Minimum actual feature value required to show a positive label.
# Features below this threshold are suppressed even when SHAP is positive,
# a 0.49 portfolio cosine vs social media shouldn't say "closely resembles".
_POS_LABEL_MIN_VALUE: dict[str, float] = {
    "cosine_sim":          0.65,
    "portfolio_relevance": 0.65,
}

# Minimum absolute SHAP contribution to show a reason at all.
# Stops weak contributors from padding the card to 3 when the match isn't strong.
_MIN_POS_SHAP = 0.02

# Positive-context labels: shown when a feature pushes the score UP.
_FEATURE_LABELS_POS: dict[str, str] = {
    "cosine_sim":             "Your profile closely matches this job",
    "skill_overlap_pct":      "You have all the required skills",
    "skill_required_matched": "You match many of the required skills",
    "skill_required_total":   "This role has a broad skill set you can cover",
    "experience_level_match": "Your experience level meets the requirement",
    "exp_delta":              "You exceed the required experience level",
    "rate_in_budget":         "Your rate fits within the job's budget",
    "rate_ratio":             "Your rate is well within the budget",
    "has_portfolio":          "Your portfolio supports your application",
    "work_exp_count":         "Your work history strengthens the fit",
    "total_jobs":             "Your completed jobs build credibility",
    "recency_score":          "You've recently worked on similar projects",
    "skill_depth":            "You're highly proficient in the required skills",
    "portfolio_relevance":    "Your past work closely resembles this project",
    "preferred_skill_pct":    "You also cover the nice-to-have skills",
}

# Negative-context labels: shown when a feature pulls the score DOWN.
_FEATURE_LABELS_NEG: dict[str, str] = {
    "cosine_sim":             "Your profile doesn't closely match this job description",
    "skill_overlap_pct":      "You're missing some of the required skills",
    "skill_required_matched": "Few of your skills match what's required",
    "skill_required_total":   "This role has many requirements you don't yet cover",
    "experience_level_match": "Your experience level is below what this job requires",
    "exp_delta":              "You need more experience to be competitive here",
    "rate_in_budget":         "Your rate may be above this job's budget",
    "rate_ratio":             "Your rate significantly exceeds this role's budget",
    "has_portfolio":          "Adding portfolio examples would strengthen your application",
    "work_exp_count":         "More work history would improve your chances",
    "total_jobs":             "Building more completed jobs will boost your ranking",
    "recency_score":          "You haven't worked on similar projects recently",
    "skill_depth":            "Your proficiency level may not meet what's needed",
    "portfolio_relevance":    "Your past work isn't closely related to this project",
    "preferred_skill_pct":    "You're missing some of the preferred skills",
}

# Features in the same group represent the same underlying concept.
# Only the strongest contributor per group is shown across BOTH lists,
# this prevents contradictory signals like "you have all required skills [+]"
# and "few skills match [−]" appearing on the same job card.
_FEATURE_GROUP: dict[str, str] = {
    "skill_overlap_pct":      "skill_coverage",
    "skill_required_matched": "skill_coverage",
    "skill_required_total":   "skill_coverage",
    "skill_depth":            "skill_proficiency",
    "preferred_skill_pct":    "skill_preferred",
    "experience_level_match": "experience",
    "exp_delta":              "experience",
    "rate_in_budget":         "budget",
    "rate_ratio":             "budget",
    "cosine_sim":             "semantic",
    "portfolio_relevance":    "portfolio",
    "work_exp_count":         "track_record",
    "total_jobs":             "track_record",
    "recency_score":          "recency",
    "has_portfolio":          "portfolio_existence",
}


def _build_reason_lists(
    contribs_row: np.ndarray,
    feat_cols: list[str],
    feat_vals: dict[str, float] | None = None,
    pos_k: int = 3,
    neg_k: int = 2,
) -> tuple[list[dict], list[dict]]:
    """
    Build match_reasons and penalty_reasons together so group deduplication
    is enforced across both lists.

    Features are processed by absolute contribution (strongest first). The
    first feature to claim a group wins it; subsequent features in the same
    group are skipped regardless of sign. This prevents the same concept
    (e.g. skill coverage, experience level) from appearing as both a positive
    and a negative signal on the same job card.

    feat_vals: actual feature values used to suppress positive labels whose
    raw value falls below _POS_LABEL_MIN_VALUE, even when SHAP is positive.
    """
    feat_contribs = contribs_row[: len(feat_cols)]
    sorted_idx    = np.argsort(-np.abs(feat_contribs))

    seen_groups:    set[str]   = set()
    match_reasons:  list[dict] = []
    penalty_reasons: list[dict] = []

    for i in sorted_idx:
        if len(match_reasons) >= pos_k and len(penalty_reasons) >= neg_k:
            break
        contrib = float(feat_contribs[i])
        if contrib == 0:
            continue
        feat  = feat_cols[i]
        group = _FEATURE_GROUP.get(feat, feat)
        if group in seen_groups:
            continue

        if contrib > 0 and len(match_reasons) < pos_k:
            if contrib < _MIN_POS_SHAP:
                continue  # contribution too weak, skip
            min_val = _POS_LABEL_MIN_VALUE.get(feat)
            if min_val is not None and feat_vals is not None:
                if float(feat_vals.get(feat, 0.0)) < min_val:
                    continue  # value too low, suppress positive label
            seen_groups.add(group)
            match_reasons.append({
                "feature":      feat,
                "label":        _FEATURE_LABELS_POS.get(feat, feat),
                "contribution": round(contrib, 4),
            })
        elif contrib < 0 and len(penalty_reasons) < neg_k:
            seen_groups.add(group)
            penalty_reasons.append({
                "feature":      feat,
                "label":        _FEATURE_LABELS_NEG.get(feat, feat),
                "contribution": round(contrib, 4),
            })

    return match_reasons, penalty_reasons


def _infer_exp_level(total_jobs: int) -> int:
    """
    Approximate experience level from total completed jobs.
    Mirrors training data: 1=entry, 2=intermediate, 3=expert.
    """
    if total_jobs >= 10:
        return 3
    if total_jobs >= 3:
        return 2
    return 1


def _load_freelancer_context(db, freelancer_id: str) -> dict | None:
    """
    Load all freelancer signals needed for feature engineering in one DB pass.

    Fetches rate, job count, skills, portfolio count, and work experience count.
    All signals are profile-based; no performance
    history or rating signals are included, so every freelancer (new or experienced)
    gets an equally complete feature vector.

    Args:
        db: Active database connection.
        freelancer_id: UUID string from freelancer.freelancer_id.

    Returns:
        Dict of freelancer signals, or None if the freelancer is not found.
    """
    logger("ML_RANKER", f"Loading freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_row = db.execute_query(
        """
        SELECT f.estimated_rate, f.rate_time, f.rate_currency, f.total_jobs
        FROM freelancer f
        WHERE f.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    if not f_row:
        logger("ML_RANKER", f"Freelancer not found | freelancer_id={freelancer_id}", level="WARNING")
        return None
    row = dict(f_row[0])

    # Skills (UUID + proficiency, proficiency drives skill_depth feature).
    # Proficiency weights: expert=1.0, advanced=0.7, intermediate=0.5, beginner=0.3, NULL=0.5.
    f_skills = db.execute_query(
        "SELECT skill_id, proficiency_level FROM freelancer_skill WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )
    skill_ids: set[str] = set()
    skill_prof: dict[str, float] = {}
    for r in f_skills:
        sid = str(r["skill_id"])
        skill_ids.add(sid)
        prof = (r.get("proficiency_level") or "").lower()
        skill_prof[sid] = _PROFICIENCY_WEIGHT.get(prof, 0.5)

    # Portfolio + completed contracts + work experience counts.
    # has_portfolio = 1 if the freelancer has ANY past-work evidence, either manual
    # portfolio items OR completed contracts. Consistent with portfolio_relevance
    # which already unions portfolio_embedding and contract_embedding.
    port = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM portfolio WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )
    completed = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM contract WHERE freelancer_id = :fid AND status = 'completed'",
        {"fid": freelancer_id},
    )
    work_exp = db.execute_query(
        "SELECT COUNT(*) AS cnt FROM work_experience WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )

    # Most recent activity date across three sources:
    #   1. work_experience end/start dates (traditional employment)
    #   2. portfolio completion_date for manually-added external projects
    #   3. contract actual_completion_date for platform-completed jobs
    # Using all three prevents pure gig workers (no work_experience rows) from
    # getting recency_score=0 just because they left that section empty.
    recency_row = db.execute_query(
        """
        SELECT MAX(activity_date) AS most_recent
        FROM (
            SELECT GREATEST(
                COALESCE(end_date,   CURRENT_DATE),
                COALESCE(start_date, CURRENT_DATE)
            ) AS activity_date
            FROM work_experience
            WHERE freelancer_id = :fid

            UNION ALL

            SELECT completion_date AS activity_date
            FROM portfolio
            WHERE freelancer_id = :fid
              AND completion_date IS NOT NULL

            UNION ALL

            SELECT actual_completion_date AS activity_date
            FROM contract
            WHERE freelancer_id = :fid
              AND status = 'completed'
              AND actual_completion_date IS NOT NULL
        ) AS activity
        """,
        {"fid": freelancer_id},
    )
    most_recent_work = recency_row[0]["most_recent"] if recency_row and recency_row[0]["most_recent"] else None

    total_jobs = int(row.get("total_jobs") or 0)
    exp_num    = _infer_exp_level(total_jobs)

    logger(
        "ML_RANKER",
        f"Freelancer context loaded | freelancer_id={freelancer_id} "
        f"| skills={len(skill_ids)} "
        f"| rate={row.get('estimated_rate')} {row.get('rate_time')} "
        f"| total_jobs={total_jobs} | exp_level={exp_num} "
        f"| portfolio={port[0]['cnt'] if port else 0} | completed_contracts={completed[0]['cnt'] if completed else 0} "
        f"| work_exp={work_exp[0]['cnt'] if work_exp else 0} "
        f"| most_recent_work={most_recent_work}",
        level="DEBUG",
    )

    return {
        "freelancer_id_str": str(freelancer_id),
        "rate":              float(row.get("estimated_rate") or 0),
        "rate_time":         str(row.get("rate_time") or "hourly").lower(),
        "rate_currency":     str(row.get("rate_currency") or "USD").upper(),
        "total_jobs":        total_jobs,
        "exp_num":           exp_num,
        "skill_ids":         skill_ids,
        "skill_prof":        skill_prof,
        "has_portfolio": float(
            1 if (port and port[0]["cnt"] > 0) or (completed and completed[0]["cnt"] > 0) else 0
        ),
        "work_exp_count":    float(work_exp[0]["cnt"] if work_exp else 0),
        "most_recent_work":  most_recent_work,
    }


def _compute_job_features(db, fc: dict, job: dict) -> dict:
    """
    Compute the 13 ML features for a single (freelancer, job) pair.

    portfolio_relevance uses the recency-weighted cosine over the freelancer's
    manual portfolio_embedding rows and contract_embedding rows against the
    job_role_embedding, treating both as past-work evidence.

    recency_score and has_portfolio are computed here and used internally
    (e.g. for cold-start fallback logic) but are not returned as model features.

    Args:
        db: Active database connection.
        fc: Freelancer context from _load_freelancer_context.
        job: One candidate row from the pgvector query (needs job_post_id,
             similarity_score, job_title, job_description, experience_level,
             source_text).

    Returns:
        Dict of 13 model feature values keyed by feature name.
    """
    jp_id      = str(job["job_post_id"])
    cosine_sim = float(job.get("similarity_score", 0))

    roles = db.execute_query(
        "SELECT job_role_id, role_budget, budget_currency, budget_type FROM job_role WHERE job_post_id = :jpid",
        {"jpid": jp_id},
    )

    # Per-role best-match: iterate all roles, keep the one where the freelancer
    # has the highest required-skill overlap. This prevents penalising a specialist
    # (e.g. backend engineer) for skills from unrelated roles in the same job post
    # (e.g. financial manager, UI designer), a flaw that inflated skill_required_total
    # and deflated skill_overlap_pct when all roles were naively merged into one pool.
    best_overlap:   float    = -1.0
    req_skills:     set[str] = set()
    pref_skills:    set[str] = set()
    avg_budget_usd: float    = 0.0

    for role in roles:
        rid = str(role["job_role_id"])
        role_skills = db.execute_query(
            "SELECT skill_id, is_required FROM job_role_skill WHERE job_role_id = :rid",
            {"rid": rid},
        )
        role_req:  set[str] = set()
        role_pref: set[str] = set()
        for rs in role_skills:
            sid = str(rs["skill_id"])
            if rs["is_required"]:
                role_req.add(sid)
            else:
                role_pref.add(sid)

        role_budget_usd = 0.0
        if role.get("role_budget"):
            bc = str(role.get("budget_currency") or "USD").upper()
            role_budget_usd = _to_usd(float(role["role_budget"]), bc)

        if role_req:
            overlap = len(fc["skill_ids"] & role_req) / len(role_req)
            if overlap > best_overlap:
                best_overlap   = overlap
                req_skills     = role_req
                pref_skills    = role_pref
                avg_budget_usd = role_budget_usd
        elif best_overlap < 0:
            # Role has no required skills, use as fallback if no better role found
            req_skills     = role_req
            pref_skills    = role_pref
            avg_budget_usd = role_budget_usd

    # Skill features
    skill_required_total   = len(req_skills)
    skill_required_matched = len(fc["skill_ids"] & req_skills)
    skill_overlap_pct      = (
        skill_required_matched / skill_required_total if skill_required_total > 0 else 0.0
    )
    # Experience features
    job_exp_num = _EXP_MAP.get(str(job.get("experience_level") or "entry"), 1)
    experience_level_match = float(1 if fc["exp_num"] >= job_exp_num else 0)
    exp_delta = float(max(-2, min(2, fc["exp_num"] - job_exp_num)))

    # Rate / budget features
    # Convert freelancer rate to monthly cost in USD, then divide the total role
    # budget by the actual project duration in months (from working_days or
    # estimated_duration text) to get a monthly budget equivalent.
    monthly_multiplier = _RATE_TO_MONTHLY_HOURS.get(fc["rate_time"], 160.0)
    monthly_rate_usd   = _to_usd(fc["rate"] * monthly_multiplier, fc["rate_currency"])

    duration_months    = _parse_duration_months(
        working_days=job.get("working_days"),
        estimated_duration=job.get("estimated_duration"),
    )
    monthly_budget_usd = avg_budget_usd / duration_months if avg_budget_usd > 0 else 0.0

    if monthly_budget_usd > 0 and monthly_rate_usd > 0:
        rate_ratio     = min(3.0, monthly_rate_usd / monthly_budget_usd)
        rate_in_budget = float(1 if monthly_rate_usd <= monthly_budget_usd * 1.1 else 0)
    else:
        rate_ratio     = 1.0
        rate_in_budget = 1.0

    # skill_depth: proficiency-weighted overlap on required skills
    # Proficiency-weighted overlap on required skills. Beginner=0.3 → Expert=1.0.
    # If skill_required_total=0, skill_depth=0 (job has no required skills).
    matched_ids = fc["skill_ids"] & req_skills
    skill_depth = (
        sum(fc["skill_prof"].get(sid, 0.5) for sid in matched_ids) / skill_required_total
        if skill_required_total > 0
        else 0.0
    )

    # preferred_skill_pct: fraction of preferred/nice-to-have skills the freelancer has.
    # 0.0 when the role lists no preferred skills, treated as neutral.
    pref_skill_total   = len(pref_skills)
    pref_skill_matched = len(fc["skill_ids"] & pref_skills)
    preferred_skill_pct = (
        pref_skill_matched / pref_skill_total if pref_skill_total > 0 else 0.0
    )

    # recency_score: not a model feature, used for cold-start context
    # exp(-λ × months_since_most_recent_activity): 1.0 today, ~0.5 at 24mo, ~0 at 5y+.
    # Activity = MAX(work_experience dates, portfolio completion_date, contract completion_date).
    most_recent_work = fc.get("most_recent_work")
    if most_recent_work is None:
        recency_score = 0.0
    else:
        try:
            today = date.today()
            months = max(0, (today - most_recent_work).days) / 30.0
            recency_score = float(math.exp(-_RECENCY_DECAY_LAMBDA * months))
        except Exception:
            recency_score = 0.0

    # portfolio_relevance: best recency-weighted cosine over past-work embeddings
    # "Have you done this kind of work before?" - distinct from cosine_sim,
    # which only captures "does your overall profile look like this job?".
    # Source: union of manual portfolio_embedding + contract_embedding.
    # Each past-work cosine is recency-weighted (24-month half-life).
    # Cold-start: returns 0 if the freelancer has no evidence at all. The
    # label generator (01_generate_data.ipynb compute_label) skips its
    # √portfolio_relevance term in that case, so cold-start freelancers are
    # not penalised for absence-of-evidence; they're just scored by skills,
    # cosine, and the rest.
    portfolio_relevance = 0.0
    if fc.get("freelancer_id_str"):
        rel_rows = db.execute_query(
            """
            WITH best_role_vec AS (
                -- Best-matching role vector for this job post (same LATERAL logic as Stage 1)
                SELECT jre.embedding_vector
                FROM job_role_embedding jre
                JOIN job_role jr ON jr.job_role_id = jre.job_role_id
                WHERE jr.job_post_id = :jpid
                  AND jre.embedding_vector IS NOT NULL
                ORDER BY jre.updated_at DESC
                LIMIT 1
            )
            SELECT sim, age_months FROM (
                -- Manual portfolio entries (user-curated showcase items)
                SELECT 1 - (pe.embedding_vector <=> jv.embedding_vector) AS sim,
                       GREATEST(0, EXTRACT(EPOCH FROM (NOW() - pe.updated_at)) / (30.0 * 86400)) AS age_months
                FROM portfolio_embedding pe, best_role_vec jv
                WHERE pe.freelancer_id = :fid
                  AND pe.embedding_vector IS NOT NULL
                UNION ALL
                -- Completed contracts (more trustworthy: include rating + review)
                SELECT 1 - (ce.embedding_vector <=> jv.embedding_vector) AS sim,
                       GREATEST(0, EXTRACT(EPOCH FROM (NOW() - COALESCE(c.actual_completion_date::timestamp, ce.updated_at))) / (30.0 * 86400)) AS age_months
                FROM contract_embedding ce
                JOIN contract c ON c.contract_id = ce.contract_id, best_role_vec jv
                WHERE ce.freelancer_id = :fid
                  AND ce.embedding_vector IS NOT NULL
            ) AS evidence
            WHERE sim IS NOT NULL
            """,
            {"fid": fc["freelancer_id_str"], "jpid": jp_id},
        )
        if rel_rows:
            best = 0.0
            for r in rel_rows:
                sim = float(r["sim"] or 0.0)
                age = float(r["age_months"] or 0.0)
                weighted = sim * math.exp(-_RECENCY_DECAY_LAMBDA * age)
                if weighted > best:
                    best = weighted
            portfolio_relevance = max(0.0, min(1.0, best))

    logger(
        "ML_RANKER",
        f"Features computed | job_post_id={jp_id} | title='{job.get('job_title','')[:40]}' "
        f"| cosine={cosine_sim:.4f} | skill_overlap={skill_overlap_pct:.2%} "
        f"({skill_required_matched}/{skill_required_total}) "
        f"| skill_depth={skill_depth:.3f} | pref_skill={preferred_skill_pct:.2%} "
        f"| recency={recency_score:.3f} "
        f"| portfolio_rel={portfolio_relevance:.3f} "
        f"| exp_match={bool(experience_level_match)} | exp_delta={exp_delta:+.0f} "
        f"| rate_in_budget={bool(rate_in_budget)} | rate_ratio={rate_ratio:.2f}",
        level="DEBUG",
    )

    return {
        "job_post_id":            jp_id,
        "cosine_sim":             cosine_sim,
        "portfolio_relevance":    float(portfolio_relevance),
        "skill_overlap_pct":      skill_overlap_pct,
        "skill_required_matched": float(skill_required_matched),
        "skill_required_total":   float(skill_required_total),
        "skill_depth":            float(skill_depth),
        "preferred_skill_pct":    float(preferred_skill_pct),
        "experience_level_match": experience_level_match,
        "exp_delta":              exp_delta,
        "rate_in_budget":         rate_in_budget,
        "rate_ratio":             rate_ratio,
        "work_exp_count":         fc["work_exp_count"],
        "total_jobs":             float(fc["total_jobs"]),
    }


def rank_jobs_with_ml(
    db,
    freelancer_id: str,
    job_rows: list[dict],
    top_n: int = 10,
) -> list[dict]:
    """
    Stage 3: re-rank pgvector candidates with CatBoost.

    Every freelancer (new or experienced) goes through the same model path.
    There is no special cold-start heuristic; the fit-based model uses only
    profile signals that are available for all freelancers from day one.

    Args:
        db: Active database connection.
        freelancer_id: UUID string from freelancer.freelancer_id.
        job_rows: Stage-2 filtered candidates, each dict must have
                  job_post_id, similarity_score, job_title, job_description,
                  experience_level, and source_text.
        top_n: Number of results to return (default 10).

    Returns:
        Top-N job dicts sorted by match_probability (0-100).
        Each dict gains match_probability and skill_overlap_pct.
        Falls back to cosine ordering if the model fails.
    """
    if not job_rows:
        logger("ML_RANKER", "rank_jobs_with_ml called with empty job_rows", level="WARNING")
        return []

    logger(
        "ML_RANKER",
        f"Stage 3 started | freelancer_id={freelancer_id} | candidates={len(job_rows)} | top_n={top_n}",
        level="INFO",
    )
    t_start = time.perf_counter()

    try:
        model, feat_cols = _load_model()

        t_ctx = time.perf_counter()
        fc = _load_freelancer_context(db, freelancer_id)
        ctx_ms = (time.perf_counter() - t_ctx) * 1000
        logger("ML_RANKER", f"Freelancer context loaded | time={ctx_ms:.1f}ms", level="DEBUG")

        if fc is None:
            logger("ML_RANKER", f"Freelancer {freelancer_id} not found, falling back to cosine", level="WARNING")
            return _cosine_fallback(job_rows, top_n)

        # Compute 13 features per candidate job
        t_feat = time.perf_counter()
        feature_rows = [_compute_job_features(db, fc, job) for job in job_rows]
        feat_ms = (time.perf_counter() - t_feat) * 1000
        feat_df = pd.DataFrame(feature_rows)

        nan_count = feat_df[feat_cols].isnull().sum().sum()
        logger(
            "ML_RANKER",
            f"Features built | jobs={len(feature_rows)} | time={feat_ms:.1f}ms | NaN={nan_count} (expected 0)",
            level="DEBUG",
        )

        X = feat_df[feat_cols]
        t_pred = time.perf_counter()
        probs = model.predict_proba(X)[:, 1]
        # Per-prediction SHAP contributions, last column is the expected value
        # (bias/baseline). Used to attach top-3 match_reasons to each job so the
        # homepage can show "why this ranked high" without a second model call.
        # CatBoost returns shape (n_samples, n_features + 1) matching LightGBM's
        # pred_contrib=True format, so _build_reason_lists works unchanged.
        try:
            from catboost import Pool as _CatPool
            contribs = model.get_feature_importance(_CatPool(X), type="ShapValues")
        except Exception:
            contribs = np.zeros((len(X), len(feat_cols) + 1))
        pred_ms = (time.perf_counter() - t_pred) * 1000

        logger(
            "ML_RANKER",
            f"CatBoost inference | candidates={len(job_rows)} | time={pred_ms:.2f}ms "
            f"| prob_range=[{probs.min():.3f}, {probs.max():.3f}] | prob_mean={probs.mean():.3f}",
            level="INFO",
        )

        for i, job in enumerate(job_rows):
            job["match_probability"] = round(float(probs[i]) * 100, 1)
            job["skill_overlap_pct"] = round(
                float(feat_df.iloc[i]["skill_overlap_pct"]) * 100, 1
            )
            job["match_reasons"], job["penalty_reasons"] = _build_reason_lists(
                contribs[i], feat_cols, feat_vals=feat_df.iloc[i].to_dict(), pos_k=3, neg_k=2
            )

        ranked = sorted(job_rows, key=lambda j: j["match_probability"], reverse=True)
        top = ranked[:top_n]

        total_ms = (time.perf_counter() - t_start) * 1000
        top_preview = [
            f"#{i+1} {j.get('job_title','?')[:30]} → {j['match_probability']}%"
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
            f"ML ranking failed after {total_ms:.1f}ms, falling back to cosine | error={e}",
            level="WARNING",
        )
        return _cosine_fallback(job_rows, top_n)


def _cosine_fallback(job_rows: list[dict], top_n: int) -> list[dict]:
    """Graceful fallback: return top_n jobs sorted by cosine similarity."""
    sorted_rows = sorted(job_rows, key=lambda j: j.get("similarity_score", 0), reverse=True)[:top_n]
    for job in sorted_rows:
        job.setdefault("match_probability", round(float(job.get("similarity_score", 0)) * 100, 1))
        job.setdefault("skill_overlap_pct", 0.0)
        job.setdefault("match_reasons", [])
        job.setdefault("penalty_reasons", [])
    return sorted_rows
