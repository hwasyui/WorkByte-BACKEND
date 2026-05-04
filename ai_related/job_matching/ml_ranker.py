"""
ML Ranker — Stage 3 of the job matching pipeline.

Takes the top-100 pgvector candidates (after Stage 2 skill filter) and re-ranks
them with a CatBoost model that predicts match_probability for each (freelancer, job) pair.

Goal: "Find jobs that match the currently logged-in freelancer."

Features (18, all non-NaN for every freelancer):
  Semantic         : cosine_sim, portfolio_relevance
                     (bio_job_cosine removed — it duplicates cosine_sim, which is
                      already computed over freelancer source-text that includes
                      the bio, so adding it just splits importance without new signal)
  Skill fit        : skill_overlap_pct, skill_required_matched, skill_required_total,
                     skill_preferred_pct, skill_depth (proficiency-weighted)
  Experience fit   : experience_level_match, exp_delta, recency_score
  Budget fit       : rate_in_budget, rate_ratio
  Context          : language_match, speciality_match, domain_match
  Profile depth    : has_portfolio, work_exp_count, total_jobs

Cold-start fairness: `portfolio_relevance` falls back to `max(0, cosine_sim - 0.10)`
when the freelancer has no past-work evidence (no manual portfolio embeddings,
no completed contracts). This prevents new freelancers with strong skills from
being penalised twice — once via the feature value (would be 0) and once via the
label's √portfolio_relevance term in GENERATE_DATA.ipynb.

The training label (see machine_learning/GENERATE_DATA.ipynb) is generated with
non-linear interactions (skill × experience, skill_depth × √recency,
√portfolio_relevance), a non-monotonic over-qualification penalty, and unobserved
noise. CatBoost learns these from raw features via boosted decision trees — no
manual interaction engineering needed.

Each ranked job carries a `match_reasons` array of the top-3 features that pushed
its score up, derived from per-prediction SHAP contributions
(CatBoost's `get_feature_importance(Pool(X), type='ShapValues')`). This gives the
homepage a deterministic, fast explanation that complements the deeper RAG analysis
on the job-detail page.

Removed vs older versions: performance_score, success_rate_hist, is_cold_start.
Those were freelancer-quality penalties, not job-fit signals. A freelancer with a
less-than-perfect history sees the same jobs as a high-rated one with identical skills.
"""

import os
import json
import time

import numpy as np
import pandas as pd
import joblib

from functions.logger import logger

# loaded once on first call
_MODEL_DIR  = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "job_matcher.pkl")
_FEAT_PATH  = os.path.join(_MODEL_DIR, "feature_cols.json")

_model = None
_feat_cols: list[str] | None = None

# ── Currency helpers ──────────────────────────────────────────────────────────

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
        import urllib.request
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


# Hours per month by rate_time — normalises freelancer rate to a monthly cost
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
# exp_delta = -log(0.5)/24 in monthly natural-log units, used inline.
import math as _math
_RECENCY_DECAY_LAMBDA = _math.log(2.0) / _RECENCY_HALF_LIFE_MONTHS


# Human-readable labels for SHAP contribution output. Keys must match the
# feature names in feature_cols.json. Used only for surfacing match_reasons —
# does not affect scoring.
_FEATURE_LABELS: dict[str, str] = {
    "cosine_sim":             "Profile semantically matches the job",
    "skill_overlap_pct":      "Strong overlap with required skills",
    "skill_required_matched": "Many required skills matched",
    "skill_required_total":   "Job's required-skill count fits your profile",
    "skill_preferred_pct":    "Covers preferred (nice-to-have) skills",
    "experience_level_match": "Experience level meets the job's requirement",
    "exp_delta":              "Experience level aligned with the job",
    "rate_in_budget":         "Your rate fits the job's budget",
    "rate_ratio":             "Rate-to-budget ratio is favourable",
    "language_match":         "Speaks the platform's working languages",
    "speciality_match":       "Speciality appears in the job title",
    "domain_match":           "Speciality appears in the job description",
    "has_portfolio":          "Portfolio strengthens your profile",
    "work_exp_count":         "Past work experience supports the fit",
    "total_jobs":             "Track record of completed jobs",
    "recency_score":          "Recently active in this kind of work",
    "skill_depth":            "Strong proficiency in the required skills",
    "portfolio_relevance":    "Past work resembles this job",
}


def _top_match_reasons(
    contribs_row: np.ndarray,
    feat_cols: list[str],
    top_k: int = 3,
) -> list[dict]:
    """
    Convert one row of CatBoost SHAP contributions into the top-K positive drivers
    of the prediction. Negative contributions (signals pushing the score *down*)
    are ignored — the homepage only needs to explain why a job ranked high.

    Args:
        contribs_row: 1D numpy array of length len(feat_cols) + 1, where the last
            element is the model's bias term (dropped here).
        feat_cols: ordered feature names matching contribs_row[:-1].
        top_k: number of reasons to return.

    Returns:
        List of {"feature", "label", "contribution"} dicts, sorted descending.
    """
    feat_contribs = contribs_row[: len(feat_cols)]
    pos_idx = np.where(feat_contribs > 0)[0]
    if pos_idx.size == 0:
        return []
    top_idx = pos_idx[np.argsort(-feat_contribs[pos_idx])[:top_k]]
    return [
        {
            "feature":      feat_cols[i],
            "label":        _FEATURE_LABELS.get(feat_cols[i], feat_cols[i]),
            "contribution": round(float(feat_contribs[i]), 4),
        }
        for i in top_idx
    ]


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

    Fetches rate, job count, skills, specialities, languages, portfolio count,
    and work experience count. All signals are profile-based — no performance
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

    # Skills (UUID + proficiency — proficiency drives skill_depth feature).
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

    # Specialities (lowercased names — used for word-level matching against job title/desc)
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

    # Languages (lowercased)
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

    # Most recent work_experience end_date (or today if currently employed).
    # Drives recency_score: how fresh the freelancer's last documented work is.
    recency_row = db.execute_query(
        """SELECT MAX(GREATEST(
                    COALESCE(end_date,    CURRENT_DATE),
                    COALESCE(start_date,  CURRENT_DATE)
                  )) AS most_recent
           FROM work_experience
           WHERE freelancer_id = :fid""",
        {"fid": freelancer_id},
    )
    most_recent_work = recency_row[0]["most_recent"] if recency_row and recency_row[0]["most_recent"] else None

    total_jobs = int(row.get("total_jobs") or 0)
    exp_num    = _infer_exp_level(total_jobs)

    logger(
        "ML_RANKER",
        f"Freelancer context loaded | freelancer_id={freelancer_id} "
        f"| skills={len(skill_ids)} | specs={list(spec_names)} | langs={list(lang_names)} "
        f"| rate={row.get('estimated_rate')} {row.get('rate_time')} "
        f"| total_jobs={total_jobs} | exp_level={exp_num} "
        f"| portfolio={port[0]['cnt'] if port else 0} | work_exp={work_exp[0]['cnt'] if work_exp else 0} "
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
        "spec_names":        spec_names,
        "lang_names":        lang_names,
        "has_portfolio":     float(1 if port and port[0]["cnt"] > 0 else 0),
        "work_exp_count":    float(work_exp[0]["cnt"] if work_exp else 0),
        "most_recent_work":  most_recent_work,
    }


def _compute_job_features(db, fc: dict, job: dict) -> dict:
    """
    Compute all 18 ML features for a single (freelancer, job) pair.

    Features added beyond the original 15-feature setup:
      - skill_depth: proficiency-weighted skill overlap. Captures that two
        freelancers with skill_overlap_pct=1.0 are not equally good if one is
        "expert" everywhere and the other "beginner".
      - recency_score: 0..1, how fresh the freelancer's most recent
        work_experience is. Decays with a 24-month half-life.
      - portfolio_relevance: max recency-weighted cosine over the freelancer's
        manual portfolio_embedding rows + contract_embedding rows, against the
        job_embedding. Treats both sources as "past work evidence" — auto-generated
        portfolio rows live in contract_embedding (Path B), manual showcase items
        live in portfolio_embedding.

    bio_job_cosine was deliberately NOT added: the freelancer source-text used
    by `cosine_sim` already contains the bio plus skills + work experience, so
    a separate bio×job cosine would be a noisier strict-subset of cosine_sim
    and would just split feature importance between near-duplicate signals.

    Args:
        db: Active database connection.
        fc: Freelancer context from _load_freelancer_context.
        job: One candidate row from the pgvector query (needs job_post_id,
             similarity_score, job_title, job_description, experience_level,
             source_text).

    Returns:
        Dict of 18 feature values keyed by feature name.
    """
    jp_id      = str(job["job_post_id"])
    cosine_sim = float(job.get("similarity_score", 0))

    roles = db.execute_query(
        "SELECT job_role_id, role_budget, budget_currency, budget_type FROM job_role WHERE job_post_id = :jpid",
        {"jpid": jp_id},
    )

    req_skills: set[str] = set()
    pref_skills: set[str] = set()
    total_budget_usd = 0.0
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
            budget_currency = str(role.get("budget_currency") or "USD").upper()
            total_budget_usd += _to_usd(float(role["role_budget"]), budget_currency)
            budget_count += 1

    avg_budget_usd = total_budget_usd / budget_count if budget_count > 0 else 0.0

    # ── Skill features ────────────────────────────────────────────────────────
    skill_required_total   = len(req_skills)
    skill_required_matched = len(fc["skill_ids"] & req_skills)
    skill_overlap_pct      = (
        skill_required_matched / skill_required_total if skill_required_total > 0 else 0.0
    )
    skill_preferred_pct = (
        len(fc["skill_ids"] & pref_skills) / len(pref_skills) if pref_skills else 0.0
    )

    # ── Experience features ───────────────────────────────────────────────────
    job_exp_num = _EXP_MAP.get(str(job.get("experience_level") or "entry"), 1)
    experience_level_match = float(1 if fc["exp_num"] >= job_exp_num else 0)
    exp_delta = float(max(-2, min(2, fc["exp_num"] - job_exp_num)))

    # ── Rate / budget features ────────────────────────────────────────────────
    # Convert freelancer rate to monthly cost in USD for a fair comparison
    # against the job's fixed project budget (also in USD after conversion).
    monthly_multiplier = _RATE_TO_MONTHLY_HOURS.get(fc["rate_time"], 160.0)
    monthly_rate_usd   = _to_usd(fc["rate"] * monthly_multiplier, fc["rate_currency"])

    if avg_budget_usd > 0 and monthly_rate_usd > 0:
        rate_ratio     = min(3.0, monthly_rate_usd / avg_budget_usd)
        rate_in_budget = float(1 if monthly_rate_usd <= avg_budget_usd * 1.1 else 0)
    else:
        rate_ratio     = 1.0
        rate_in_budget = 1.0

    # ── Language match ────────────────────────────────────────────────────────
    common_lang_set = {"english", "indonesian", "bahasa indonesia"}
    language_match  = float(1 if fc["lang_names"] & common_lang_set else 0)

    # ── Speciality & domain match — word-level ────────────────────────────────
    # Check if any significant word (>3 chars) from the freelancer's speciality
    # names appears in the job title (speciality_match) or the combined
    # job title + description text (domain_match).
    # Word-level matching is more robust than full-phrase substring search,
    # since speciality names like "Backend Development" rarely appear verbatim
    # in job titles like "Senior Backend Engineer".
    job_title_lower = str(job.get("job_title", "")).lower()
    job_desc_lower  = str(job.get("job_description", "")).lower()
    job_text        = job_title_lower + " " + job_desc_lower[:300]

    speciality_match = float(
        1 if fc["spec_names"] and any(
            any(word in job_title_lower for word in sp.split() if len(word) > 3)
            for sp in fc["spec_names"]
        ) else 0
    )
    domain_match = float(
        1 if fc["spec_names"] and any(
            any(word in job_text for word in sp.split() if len(word) > 3)
            for sp in fc["spec_names"]
        ) else 0
    )

    # ── skill_depth ─────────────────────────────────────────────────────────
    # Proficiency-weighted overlap on required skills. Beginner=0.3 → Expert=1.0.
    # If skill_required_total=0, skill_depth=0 (job has no required skills).
    matched_ids = fc["skill_ids"] & req_skills
    skill_depth = (
        sum(fc["skill_prof"].get(sid, 0.5) for sid in matched_ids) / skill_required_total
        if skill_required_total > 0
        else 0.0
    )

    # ── recency_score ───────────────────────────────────────────────────────
    # exp(-λ × months_since_most_recent_work) — 1.0 today, ~0.5 at 24mo, ~0 at 5y+.
    most_recent_work = fc.get("most_recent_work")
    if most_recent_work is None:
        recency_score = 0.0
    else:
        from datetime import date as _date
        try:
            today = _date.today()
            months = max(0, (today - most_recent_work).days) / 30.0
            recency_score = float(_math.exp(-_RECENCY_DECAY_LAMBDA * months))
        except Exception:
            recency_score = 0.0

    # ── portfolio_relevance ─────────────────────────────────────────────────
    # "Have you done this kind of work before?" — distinct from cosine_sim,
    # which only captures "does your overall profile look like this job?".
    # Source: union of manual portfolio_embedding + contract_embedding.
    # Each past-work cosine is recency-weighted (24-month half-life).
    # Cold-start: returns 0 if the freelancer has no evidence at all. The
    # label generator (01_generate_data.ipynb compute_label) skips its
    # √portfolio_relevance term in that case, so cold-start freelancers are
    # not penalised for absence-of-evidence — they're just scored by skills,
    # cosine, and the rest.
    portfolio_relevance = 0.0
    if fc.get("freelancer_id_str"):
        rel_rows = db.execute_query(
            """
            WITH job_vec AS (
                SELECT embedding_vector FROM job_embedding WHERE job_post_id = :jpid
            )
            SELECT sim, age_months FROM (
                -- Manual portfolio entries (user-curated showcase items)
                SELECT 1 - (pe.embedding_vector <=> jv.embedding_vector) AS sim,
                       GREATEST(0, EXTRACT(EPOCH FROM (NOW() - pe.updated_at)) / (30.0 * 86400)) AS age_months
                FROM portfolio_embedding pe, job_vec jv
                WHERE pe.freelancer_id = :fid
                  AND pe.embedding_vector IS NOT NULL
                  AND jv.embedding_vector IS NOT NULL
                UNION ALL
                -- Completed contracts (more trustworthy: include rating + review)
                SELECT 1 - (ce.embedding_vector <=> jv.embedding_vector) AS sim,
                       GREATEST(0, EXTRACT(EPOCH FROM (NOW() - COALESCE(c.actual_completion_date::timestamp, ce.updated_at))) / (30.0 * 86400)) AS age_months
                FROM contract_embedding ce
                JOIN contract c ON c.contract_id = ce.contract_id
                JOIN job_vec jv ON TRUE
                WHERE ce.freelancer_id = :fid
                  AND ce.embedding_vector IS NOT NULL
                  AND jv.embedding_vector IS NOT NULL
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
                weighted = sim * _math.exp(-_RECENCY_DECAY_LAMBDA * age)
                if weighted > best:
                    best = weighted
            portfolio_relevance = max(0.0, min(1.0, best))

    logger(
        "ML_RANKER",
        f"Features computed | job_post_id={jp_id} | title='{job.get('job_title','')[:40]}' "
        f"| cosine={cosine_sim:.4f} | skill_overlap={skill_overlap_pct:.2%} "
        f"({skill_required_matched}/{skill_required_total}) "
        f"| skill_depth={skill_depth:.3f} | recency={recency_score:.3f} "
        f"| portfolio_rel={portfolio_relevance:.3f} "
        f"| exp_match={bool(experience_level_match)} | exp_delta={exp_delta:+.0f} "
        f"| rate_in_budget={bool(rate_in_budget)} | rate_ratio={rate_ratio:.2f} "
        f"| speciality_match={bool(speciality_match)} | domain_match={bool(domain_match)}",
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
        "total_jobs":             float(fc["total_jobs"]),
        "skill_depth":            float(skill_depth),
        "recency_score":          float(recency_score),
        "portfolio_relevance":    float(portfolio_relevance),
    }


def rank_jobs_with_ml(
    db,
    freelancer_id: str,
    job_rows: list[dict],
    top_n: int = 10,
) -> list[dict]:
    """
    Stage 3 — re-rank pgvector candidates with CatBoost.

    Every freelancer (new or experienced) goes through the same model path.
    There is no special cold-start heuristic — the fit-based model uses only
    profile signals that are available for all freelancers from day one.

    Args:
        db: Active database connection.
        freelancer_id: UUID string from freelancer.freelancer_id.
        job_rows: Stage-2 filtered candidates — each dict must have
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
            logger("ML_RANKER", f"Freelancer {freelancer_id} not found — falling back to cosine", level="WARNING")
            return _cosine_fallback(job_rows, top_n)

        # Compute 18 features per candidate job
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
        # Per-prediction SHAP contributions — last column is the expected value
        # (bias/baseline). Used to attach top-3 match_reasons to each job so the
        # homepage can show "why this ranked high" without a second model call.
        # CatBoost returns shape (n_samples, n_features + 1) matching LightGBM's
        # pred_contrib=True format, so _top_match_reasons works unchanged.
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
            job["match_reasons"] = _top_match_reasons(contribs[i], feat_cols, top_k=3)

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
            f"ML ranking failed after {total_ms:.1f}ms — falling back to cosine | error={e}",
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
    return sorted_rows
