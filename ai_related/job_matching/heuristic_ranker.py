"""
Heuristic Ranker — Stage 3 of the job matching pipeline.

Replaces the CatBoost ML ranker (backed up to /home/capstone/backup/job_matching/).
Takes Stage-2 filtered candidates and re-ranks them using a transparent weighted
composite score across five signals — no trained model, no synthetic data.

  heuristic_score = W_SEMANTIC × cosine_similarity          (0–1 from pgvector)
                  + W_SKILL    × required_skill_coverage     (best-matching role)
                  + W_EXP      × experience_compatibility    (smooth: 1.0 / 0.5 / 0.0)
                  + W_BUDGET   × budget_compatibility        (smooth: 1.0 → 0.0 as rate exceeds budget)
                  + W_DOMAIN   × domain_match                (speciality word-match in title/desc)

Weights are module-level constants — easy to audit and adjust.
Each signal is independently interpretable and maps directly to a match_reason
shown on the frontend card.
"""
import json
import os
import time

from functions.logger import logger

# ---------------------------------------------------------------------------
# Scoring weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
W_SEMANTIC = 0.40   # semantic cosine similarity — broad relevance signal
W_SKILL    = 0.30   # required-skill coverage on best-matching role
W_EXP      = 0.15   # experience level compatibility
W_BUDGET   = 0.10   # freelancer rate vs job role budget
W_DOMAIN   = 0.05   # speciality / domain word-level match

# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------
_CURRENCY_RATES_PATH = os.path.join(os.path.dirname(__file__), "currency_rates.json")
_currency_rates: dict | None = None
_currency_rates_fetched_at: float = 0.0
_CURRENCY_REFRESH_SECONDS = 604800  # weekly


def _load_currency_rates() -> dict:
    global _currency_rates, _currency_rates_fetched_at
    now = time.time()
    if _currency_rates is not None and (now - _currency_rates_fetched_at) < _CURRENCY_REFRESH_SECONDS:
        return _currency_rates
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.frankfurter.app/latest?from=USD", timeout=4) as resp:
            data = json.loads(resp.read())
        rates = data.get("rates", {})
        rates["USD"] = 1.0
        _currency_rates = {k.upper(): float(v) for k, v in rates.items()}
        _currency_rates_fetched_at = now
        return _currency_rates
    except Exception as e:
        logger("HEURISTIC_RANKER", f"Currency rate fetch failed ({e}); using bundled rates", level="WARNING")
    try:
        with open(_CURRENCY_RATES_PATH) as fh:
            data = json.load(fh)
        _currency_rates = {k.upper(): float(v) for k, v in data["rates"].items()}
        _currency_rates_fetched_at = now
        return _currency_rates
    except Exception as e:
        logger("HEURISTIC_RANKER", f"Failed to load bundled rates ({e}); defaulting USD-only", level="ERROR")
        _currency_rates = {"USD": 1.0}
        return _currency_rates


def _to_usd(amount: float, currency: str) -> float:
    if amount <= 0:
        return 0.0
    rates = _load_currency_rates()
    rate = rates.get(currency.upper(), 1.0)
    return amount / rate if rate > 0 else amount


# Hours per month for each rate_time — normalises freelancer rate to a monthly
# cost so it's comparable against a fixed project budget.
_RATE_TO_MONTHLY_HOURS: dict = {
    "hourly":   160.0,   # 40 hrs/week × 4 weeks
    "daily":     20.0,   # 5 days/week × 4 weeks
    "weekly":     4.0,
    "monthly":    1.0,
    "annually":   1.0 / 12.0,
}

_EXP_MAP = {"entry": 1, "intermediate": 2, "expert": 3}


# ---------------------------------------------------------------------------
# Freelancer context loader
# ---------------------------------------------------------------------------

def _load_freelancer_context(db, freelancer_id: str) -> dict | None:
    """Load the freelancer signals needed for the five heuristic components."""
    rows = db.execute_query(
        """
        SELECT estimated_rate, rate_time, rate_currency,
               experience_level, total_jobs
        FROM freelancer
        WHERE freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    if not rows:
        return None
    row = dict(rows[0])

    # Skills
    skill_rows = db.execute_query(
        "SELECT skill_id FROM freelancer_skill WHERE freelancer_id = :fid",
        {"fid": freelancer_id},
    )
    skill_ids = {str(r["skill_id"]) for r in skill_rows}

    # Specialities (lowercased for word-level matching)
    spec_rows = db.execute_query(
        """
        SELECT s.speciality_name
        FROM freelancer_speciality fs
        JOIN speciality s ON s.speciality_id = fs.speciality_id
        WHERE fs.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    spec_names = {r["speciality_name"].lower() for r in spec_rows}

    # Resolve experience level: use profile field, fall back to inferred from total_jobs
    total_jobs = int(row.get("total_jobs") or 0)
    profile_exp = str(row.get("experience_level") or "").lower()
    if profile_exp in _EXP_MAP:
        exp_num = _EXP_MAP[profile_exp]
    elif total_jobs >= 10:
        exp_num = 3
    elif total_jobs >= 3:
        exp_num = 2
    else:
        exp_num = 1

    return {
        "freelancer_id": str(freelancer_id),
        "rate":          float(row.get("estimated_rate") or 0),
        "rate_time":     str(row.get("rate_time") or "hourly").lower(),
        "rate_currency": str(row.get("rate_currency") or "USD").upper(),
        "exp_num":       exp_num,
        "skill_ids":     skill_ids,
        "spec_names":    spec_names,
    }


# ---------------------------------------------------------------------------
# Per-job signal computation
# ---------------------------------------------------------------------------

def _compute_signals(db, fc: dict, job: dict) -> dict:
    """
    Compute the five heuristic signals for one (freelancer, job) pair.

    Per-role best-match logic: iterates all roles in the job post and keeps
    the one where the freelancer has the highest required-skill overlap.
    This prevents penalising a specialist (e.g. backend engineer) for skills
    from unrelated roles (e.g. financial manager, UI designer) in the same post.
    """
    jp_id = str(job["job_post_id"])
    cosine_sim = float(job.get("similarity_score", 0))

    roles = db.execute_query(
        "SELECT job_role_id, role_budget, budget_currency FROM job_role WHERE job_post_id = :jpid",
        {"jpid": jp_id},
    )

    best_overlap = -1.0
    req_matched = 0
    req_total = 0
    avg_budget_usd = 0.0
    has_required_skills = False

    for role in (roles or []):
        rid = str(role["job_role_id"])
        req_rows = db.execute_query(
            "SELECT skill_id FROM job_role_skill WHERE job_role_id = :rid AND is_required = TRUE",
            {"rid": rid},
        )
        role_req_ids = {str(r["skill_id"]) for r in req_rows}

        budget_usd = 0.0
        if role.get("role_budget"):
            bc = str(role.get("budget_currency") or "USD").upper()
            budget_usd = _to_usd(float(role["role_budget"]), bc)

        if role_req_ids:
            has_required_skills = True
            overlap = len(fc["skill_ids"] & role_req_ids) / len(role_req_ids)
            if overlap > best_overlap:
                best_overlap = overlap
                req_matched = len(fc["skill_ids"] & role_req_ids)
                req_total = len(role_req_ids)
                avg_budget_usd = budget_usd
        elif best_overlap < 0:
            # No required skills on this role — use its budget as fallback
            avg_budget_usd = budget_usd

    # None signals "job has no required skills" → treated as neutral in scoring
    skill_overlap = max(0.0, best_overlap) if has_required_skills else None

    # ── Experience compatibility (smooth, not binary) ─────────────────────────
    # 1.0: meets or exceeds requirement
    # 0.5: one level below (still plausible)
    # 0.0: two levels below (entry applying for expert role)
    job_exp_num = _EXP_MAP.get(str(job.get("experience_level") or "entry"), 1)
    delta = fc["exp_num"] - job_exp_num
    if delta >= 0:
        exp_compat = 1.0
    elif delta == -1:
        exp_compat = 0.5
    else:
        exp_compat = 0.0

    # ── Budget compatibility (smooth 1.0 → 0.0) ──────────────────────────────
    # Converts freelancer rate to estimated monthly cost in USD, then compares
    # against the best-matching role's budget. Ratio ≤ 1.0 → full score; degrades
    # linearly between 1.0× and 1.5×; 0.0 above 1.5× (clearly over budget).
    monthly_mult = _RATE_TO_MONTHLY_HOURS.get(fc["rate_time"], 160.0)
    monthly_rate_usd = _to_usd(fc["rate"] * monthly_mult, fc["rate_currency"])

    if avg_budget_usd > 0 and monthly_rate_usd > 0:
        ratio = monthly_rate_usd / avg_budget_usd
        if ratio <= 1.0:
            budget_compat = 1.0
        elif ratio <= 1.5:
            budget_compat = max(0.0, 1.0 - (ratio - 1.0) / 0.5)
        else:
            budget_compat = 0.0
    else:
        budget_compat = 0.5  # missing budget info — neutral

    # ── Speciality / domain match ─────────────────────────────────────────────
    job_title_lower = str(job.get("job_title", "")).lower()
    job_desc_lower  = str(job.get("job_description", "")).lower()[:300]
    job_full_text   = job_title_lower + " " + job_desc_lower

    speciality_match = bool(
        fc["spec_names"] and any(
            any(word in job_title_lower for word in sp.split() if len(word) > 3)
            for sp in fc["spec_names"]
        )
    )
    domain_match = bool(
        fc["spec_names"] and any(
            any(word in job_full_text for word in sp.split() if len(word) > 3)
            for sp in fc["spec_names"]
        )
    )
    domain_signal = 1.0 if (speciality_match or domain_match) else 0.0

    return {
        "jp_id":            jp_id,
        "cosine_sim":       cosine_sim,
        "skill_overlap":    skill_overlap,       # None if job has no required skills
        "has_req_skills":   has_required_skills,
        "req_matched":      req_matched,
        "req_total":        req_total,
        "exp_compat":       exp_compat,
        "budget_compat":    budget_compat,
        "budget_usd":       avg_budget_usd,
        "domain_signal":    domain_signal,
        "speciality_match": speciality_match,
        "domain_match":     domain_match,
    }


# ---------------------------------------------------------------------------
# Scoring and reason generation
# ---------------------------------------------------------------------------

def _compute_score(signals: dict) -> float:
    """
    Weighted sum of the five signals.
    skill_overlap=None (no required skills on the job) is treated as neutral (0.5)
    so those jobs are not penalised for an absent signal.
    """
    skill_val = signals["skill_overlap"] if signals["skill_overlap"] is not None else 0.5
    return round(
        W_SEMANTIC * signals["cosine_sim"]
        + W_SKILL   * skill_val
        + W_EXP     * signals["exp_compat"]
        + W_BUDGET  * signals["budget_compat"]
        + W_DOMAIN  * signals["domain_signal"],
        4,
    )


def _build_match_reasons(signals: dict) -> list:
    reasons = []
    if signals["cosine_sim"] >= 0.70:
        reasons.append({"factor": "semantic_match",  "label": "Profile semantically matches the job"})
    if signals["skill_overlap"] is not None and signals["skill_overlap"] >= 0.60:
        reasons.append({"factor": "skill_overlap",   "label": f"Covers {signals['req_matched']}/{signals['req_total']} required skills"})
    if signals["exp_compat"] == 1.0:
        reasons.append({"factor": "experience",      "label": "Experience level meets the job requirement"})
    if signals["budget_compat"] == 1.0:
        reasons.append({"factor": "budget",          "label": "Rate fits the job budget"})
    if signals["speciality_match"]:
        reasons.append({"factor": "speciality",      "label": "Speciality matches the job title"})
    elif signals["domain_match"]:
        reasons.append({"factor": "domain",          "label": "Speciality aligns with the job domain"})
    return reasons[:3]


def _build_penalty_reasons(signals: dict) -> list:
    penalties = []
    if signals["has_req_skills"] and signals["skill_overlap"] is not None and signals["skill_overlap"] < 0.30:
        penalties.append({"factor": "skill_gap",     "label": f"Low required-skill coverage ({signals['req_matched']}/{signals['req_total']})"})
    if signals["exp_compat"] == 0.0:
        penalties.append({"factor": "experience",    "label": "Experience level is below the job requirement"})
    elif signals["exp_compat"] == 0.5:
        penalties.append({"factor": "experience",    "label": "Experience level is slightly below requirement"})
    if signals["budget_compat"] == 0.0:
        penalties.append({"factor": "budget",        "label": "Rate likely exceeds the job budget"})
    elif signals["budget_compat"] < 0.5:
        penalties.append({"factor": "budget",        "label": "Rate is on the higher end of the job budget"})
    return penalties[:2]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def rank_jobs_with_heuristic(
    db,
    freelancer_id: str,
    job_rows: list,
    top_n: int = 10,
) -> list:
    """
    Stage 3 — re-rank pgvector + skill-filtered candidates with the heuristic scorer.

    Each job receives a heuristic_score (0–100), skill_overlap_pct, match_reasons,
    and penalty_reasons. Falls back to cosine ordering if the freelancer context
    cannot be loaded.
    """
    if not job_rows:
        return []

    logger(
        "HEURISTIC_RANKER",
        f"Stage 3 started | freelancer_id={freelancer_id} | candidates={len(job_rows)} | top_n={top_n}",
        level="INFO",
    )
    t_start = time.perf_counter()

    try:
        fc = _load_freelancer_context(db, freelancer_id)
        if fc is None:
            logger("HEURISTIC_RANKER", f"Freelancer {freelancer_id} not found — falling back to cosine", level="WARNING")
            return _cosine_fallback(job_rows, top_n)

        for job in job_rows:
            sig = _compute_signals(db, fc, job)
            score = _compute_score(sig)

            job["heuristic_score"]  = round(score * 100, 1)
            job["skill_overlap_pct"] = (
                round(sig["skill_overlap"] * 100, 1)
                if sig["skill_overlap"] is not None
                else job.get("skill_overlap_pct")
            )
            job["match_reasons"]    = _build_match_reasons(sig)
            job["penalty_reasons"]  = _build_penalty_reasons(sig)

            logger(
                "HEURISTIC_RANKER",
                f"Scored | job_post_id={sig['jp_id']} | title='{job.get('job_title','')[:35]}' "
                f"| score={score:.4f} | cosine={sig['cosine_sim']:.3f} "
                f"| skill={sig['skill_overlap']:.2%} ({sig['req_matched']}/{sig['req_total']})"
                if sig["skill_overlap"] is not None else
                f"Scored | job_post_id={sig['jp_id']} | title='{job.get('job_title','')[:35]}' "
                f"| score={score:.4f} | cosine={sig['cosine_sim']:.3f} | skill=None",
                level="DEBUG",
            )

        ranked = sorted(job_rows, key=lambda j: j["heuristic_score"], reverse=True)
        top = ranked[:top_n]

        total_ms = (time.perf_counter() - t_start) * 1000
        top_preview = [
            f"#{i+1} {j.get('job_title','?')[:30]} → {j['heuristic_score']}"
            for i, j in enumerate(top[:5])
        ]
        logger(
            "HEURISTIC_RANKER",
            f"Stage 3 complete | freelancer_id={freelancer_id} | returned={len(top)}/{len(job_rows)} "
            f"| total_time={total_ms:.1f}ms | top5={top_preview}",
            level="INFO",
        )
        return top

    except Exception as e:
        total_ms = (time.perf_counter() - t_start) * 1000
        logger(
            "HEURISTIC_RANKER",
            f"Heuristic ranking failed after {total_ms:.1f}ms — falling back to cosine | error={e}",
            level="WARNING",
        )
        return _cosine_fallback(job_rows, top_n)


def _cosine_fallback(job_rows: list, top_n: int) -> list:
    """Sort by semantic cosine similarity when heuristic scoring cannot run."""
    sorted_rows = sorted(job_rows, key=lambda j: j.get("similarity_score", 0), reverse=True)[:top_n]
    for job in sorted_rows:
        job.setdefault("heuristic_score", round(float(job.get("similarity_score", 0)) * 100, 1))
        job.setdefault("skill_overlap_pct", job.get("skill_overlap_pct"))
        job.setdefault("match_reasons", [])
        job.setdefault("penalty_reasons", [])
    return sorted_rows
