import os
import json
import re
import time
import asyncio
import random

import httpx

from functions.logger import logger

_LLM_TIMEOUT = 90.0   # user-triggered, so a longer timeout is fine

# Evidence gating: at most EVIDENCE_CAP items (contracts + portfolio combined) are
# shown to the LLM, regardless of how many exist. A past contract only counts as
# relevant evidence if its cosine similarity to the role clears RELEVANCE_THRESHOLD;
# contracts retrieved via the recency fallback carry no similarity score at all and
# are treated as automatically relevant, since there's nothing to gate on.
EVIDENCE_CAP = 3
RELEVANCE_THRESHOLD = 0.3

# FX rates
_FX_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "currency_rates.json")


def _get_fx_rates() -> dict[str, float]:
    """Read USD-based FX rates from currency_rates.json. Falls back to USD-only if unavailable."""
    try:
        with open(_FX_CACHE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        rates = {k.upper(): float(v) for k, v in data.get("rates", {}).items()}
        if rates:
            logger("RAG_ANALYSER", f"FX rates loaded | {len(rates)} currencies | updated={data.get('updated', '?')}", level="DEBUG")
            return rates
    except Exception as e:
        logger("RAG_ANALYSER", f"FX rate file read failed ({e}); using USD-only fallback", level="WARNING")
    return {"USD": 1.0}


def _to_usd(amount: float, currency: str, fx: dict[str, float]) -> float:
    """Convert amount from currency to USD using USD-based fx rates."""
    rate = fx.get(currency.upper(), 1.0)
    return float(amount) / rate if rate > 0 else float(amount)


def _to_idr(amount: float, currency: str, fx: dict[str, float]) -> float:
    """Convert amount from currency to IDR via USD as the intermediate."""
    return _to_usd(amount, currency, fx) * fx.get("IDR", 16350.0)


def _retrieve_role_context(db, job_role_id: str) -> dict:
    """
    Retrieve a single job role with its parent job post context and its own
    required/preferred skills from the DB.

    Args:
        db: Active database connection.
        job_role_id: UUID string of the job role to retrieve.

    Returns:
        Dict with role fields, the parent job post's fields, and a ``skills`` list.
        Returns an empty dict if the role is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving role context | job_role_id={job_role_id}", level="DEBUG")

    rows = db.execute_query(
        """
        SELECT jr.role_title, jr.role_description, jr.role_budget, jr.budget_type, jr.budget_currency,
               jp.job_post_id, jp.job_title, jp.job_description, jp.project_type, jp.project_scope,
               jp.experience_level, jp.estimated_duration, jp.deadline,
               COALESCE(
                   array_agg(
                       s.skill_name || ' (' ||
                       CASE WHEN jrs.is_required THEN 'required' ELSE 'preferred' END ||
                       CASE WHEN jrs.importance_level IS NOT NULL
                            THEN ', ' || jrs.importance_level::text
                            ELSE ''
                       END || ')'
                   ) FILTER (WHERE s.skill_name IS NOT NULL),
                   ARRAY[]::text[]
               ) AS skills
        FROM job_role jr
        JOIN job_post jp ON jp.job_post_id = jr.job_post_id
        LEFT JOIN job_role_skill jrs ON jrs.job_role_id = jr.job_role_id
        LEFT JOIN skill s            ON s.skill_id       = jrs.skill_id
        WHERE jr.job_role_id = :jrid
        GROUP BY jr.job_role_id, jr.role_title, jr.role_description, jr.role_budget,
                 jr.budget_type, jr.budget_currency, jp.job_post_id, jp.job_title,
                 jp.job_description, jp.project_type, jp.project_scope,
                 jp.experience_level, jp.estimated_duration, jp.deadline
        """,
        {"jrid": job_role_id},
    )
    if not rows:
        logger("RAG_ANALYSER", f"Job role not found | job_role_id={job_role_id}", level="WARNING")
        return {}
    role = dict(rows[0])

    logger(
        "RAG_ANALYSER",
        f"Role context retrieved | job_role_id={job_role_id} | role='{role['role_title']}' "
        f"| job='{role['job_title']}' | skills={len(role.get('skills') or [])}",
        level="DEBUG",
    )
    return role


def _retrieve_freelancer_context(db, freelancer_id: str, job_role_id: str | None = None) -> dict:
    """
    Retrieve a freelancer's full profile including skills, portfolio items,
    and recent work experience.

    Portfolio items are ranked by cosine similarity to the target role when both
    portfolio_embedding vectors and the role's embedding are available. This surfaces
    the most *relevant* external projects rather than just the most recent ones.
    Falls back to recency order when embeddings are not yet ready.

    Args:
        db: Active database connection.
        freelancer_id: UUID string of the freelancer.
        job_role_id: UUID of the role being analysed. When provided, portfolio
            items are ranked by relevance to that role instead of by recency.

    Returns:
        Dict with profile fields plus ``skills``, ``portfolio`` (up to 3),
        ``portfolio_retrieval_method``, and ``work_experience`` (up to 3) lists.
        Returns an empty dict if the freelancer is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_rows = db.execute_query(
        """
        SELECT f.full_name, f.title, f.bio, f.estimated_rate, f.rate_time, f.rate_currency,
               f.total_jobs
        FROM freelancer f
        WHERE f.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    if not f_rows:
        logger("RAG_ANALYSER", f"Freelancer not found | freelancer_id={freelancer_id}", level="WARNING")
        return {}
    fc = dict(f_rows[0])

    # Skills with proficiency
    skills = db.execute_query(
        """
        SELECT s.skill_name, s.skill_category, fs.proficiency_level
        FROM freelancer_skill fs
        JOIN skill s ON s.skill_id = fs.skill_id
        WHERE fs.freelancer_id = :fid
        ORDER BY s.skill_name
        """,
        {"fid": freelancer_id},
    )
    fc["skills"] = [dict(s) for s in skills]

    # Portfolio ranked by cosine similarity to the target role when embeddings
    # are ready; otherwise fall back to most-recent-first.
    # portfolio_embedding contains manually-entered external projects (self-reported).
    # Contracts (auto-generated once a job completes on-platform) are retrieved
    # separately in _retrieve_past_contracts and carry higher credibility.
    portfolio_method = "recency_fallback"
    if job_role_id:
        portfolio_embed_check = db.execute_query(
            """
            SELECT COUNT(*) AS cnt
            FROM portfolio_embedding pe
            WHERE pe.freelancer_id = :fid AND pe.embedding_vector IS NOT NULL
            """,
            {"fid": freelancer_id},
        )
        has_portfolio_embeddings = portfolio_embed_check and int(portfolio_embed_check[0]["cnt"]) > 0

        role_embed_check = db.execute_query(
            "SELECT 1 FROM job_role_embedding WHERE job_role_id = :jrid AND embedding_vector IS NOT NULL LIMIT 1",
            {"jrid": job_role_id},
        )
        has_role_embedding = bool(role_embed_check)

        if has_portfolio_embeddings and has_role_embedding:
            portfolio_rows = db.execute_query(
                """
                SELECT p.project_title,
                       p.project_description,
                       p.project_url,
                       ROUND((1 - (pe.embedding_vector <=> jre.embedding_vector))::numeric, 3) AS relevance_score
                FROM portfolio_embedding pe
                JOIN portfolio p ON p.portfolio_id = pe.portfolio_id
                JOIN job_role_embedding jre ON jre.job_role_id = :jrid AND jre.embedding_vector IS NOT NULL
                WHERE pe.freelancer_id = :fid
                  AND pe.embedding_vector IS NOT NULL
                ORDER BY pe.embedding_vector <=> jre.embedding_vector
                LIMIT 3
                """,
                {"fid": freelancer_id, "jrid": job_role_id},
            )
            portfolio_method = "vector_similarity"
            logger(
                "RAG_ANALYSER",
                f"Portfolio ranked by cosine similarity | freelancer_id={freelancer_id} | count={len(portfolio_rows)}",
                level="DEBUG",
            )
        else:
            logger(
                "RAG_ANALYSER",
                f"Portfolio embeddings not ready, falling back to recency "
                f"(has_portfolio_embeddings={has_portfolio_embeddings}, has_role_embedding={has_role_embedding})",
                level="DEBUG",
            )
            portfolio_rows = db.execute_query(
                """
                SELECT project_title, project_description, project_url
                FROM portfolio
                WHERE freelancer_id = :fid
                ORDER BY completion_date DESC NULLS LAST
                LIMIT 3
                """,
                {"fid": freelancer_id},
            )
    else:
        portfolio_rows = db.execute_query(
            """
            SELECT project_title, project_description, project_url
            FROM portfolio
            WHERE freelancer_id = :fid
            ORDER BY completion_date DESC NULLS LAST
            LIMIT 3
            """,
            {"fid": freelancer_id},
        )

    fc["portfolio"] = [dict(p) for p in portfolio_rows]
    fc["portfolio_retrieval_method"] = portfolio_method

    # Work experience (most recent 3)
    work_exp = db.execute_query(
        """
        SELECT job_title, company_name, description
        FROM work_experience
        WHERE freelancer_id = :fid
        ORDER BY start_date DESC NULLS LAST
        LIMIT 3
        """,
        {"fid": freelancer_id},
    )
    fc["work_experience"] = [dict(w) for w in work_exp]

    logger(
        "RAG_ANALYSER",
        f"Freelancer context retrieved | freelancer_id={freelancer_id} | name='{fc.get('full_name', '?')}' "
        f"| skills={len(fc['skills'])} "
        f"| portfolio={len(fc['portfolio'])} ({portfolio_method}) "
        f"| work_exp={len(fc['work_experience'])} | jobs={fc.get('total_jobs', 0)}",
        level="DEBUG",
    )
    return fc


def _retrieve_past_contracts(db, freelancer_id: str, job_role_id: str) -> list[dict]:
    """
    Fetch completed contracts for the freelancer, ordered by cosine similarity to
    the target role when embeddings are available, or by recency if the sweep
    worker hasn't run yet. Returns a raw pool (up to 5); evidence gating in
    _build_evidence_list() decides how many actually reach the prompt.
    """
    logger(
        "RAG_ANALYSER",
        f"Retrieving past contracts (RAG context) | freelancer_id={freelancer_id} | job_role_id={job_role_id}",
        level="DEBUG",
    )

    embedding_check = db.execute_query(
        """
        SELECT COUNT(*) AS cnt
        FROM contract_embedding ce
        JOIN contract c ON c.contract_id = ce.contract_id
        WHERE ce.freelancer_id = :fid
          AND ce.embedding_vector IS NOT NULL
          AND c.status = 'completed'
        """,
        {"fid": freelancer_id},
    )
    has_embeddings = embedding_check and int(embedding_check[0]["cnt"]) > 0

    role_embedding_check = db.execute_query(
        "SELECT 1 FROM job_role_embedding WHERE job_role_id = :jrid AND embedding_vector IS NOT NULL LIMIT 1",
        {"jrid": job_role_id},
    )
    has_role_embedding = bool(role_embedding_check)

    if has_embeddings and has_role_embedding:
        logger("RAG_ANALYSER", "Using vector similarity to rank past contracts", level="DEBUG")
        rows = db.execute_query(
            """
            SELECT jp.job_title,
                   jp.job_description,
                   c.status                        AS contract_status,
                   ROUND(AVG(rr.score), 1)         AS overall_rating,
                   rwc.overall_comment             AS review_text,
                   1 - (ce.embedding_vector <=> jre.embedding_vector) AS similarity
            FROM contract_embedding ce
            JOIN contract c  ON c.contract_id   = ce.contract_id
            JOIN job_post jp ON jp.job_post_id  = c.job_post_id
            JOIN job_role_embedding jre ON jre.job_role_id = :jrid AND jre.embedding_vector IS NOT NULL
            LEFT JOIN reviews rv  ON rv.contract_id = c.contract_id AND rv.status = 'published'
            LEFT JOIN review_written_content rwc ON rwc.review_id = rv.id
            LEFT JOIN review_ratings rr ON rr.review_id = rv.id
            WHERE ce.freelancer_id = :fid
              AND ce.embedding_vector IS NOT NULL
              AND c.status = 'completed'
            GROUP BY jp.job_title, jp.job_description, c.status, rwc.overall_comment,
                     ce.embedding_vector, jre.embedding_vector
            ORDER BY ce.embedding_vector <=> jre.embedding_vector
            LIMIT 5
            """,
            {"fid": freelancer_id, "jrid": job_role_id},
        )
        retrieval_method = "vector_similarity"
    else:
        logger(
            "RAG_ANALYSER",
            f"Contract embeddings not ready, falling back to recency order "
            f"(has_contract_embeddings={has_embeddings}, has_role_embedding={has_role_embedding})",
            level="DEBUG",
        )
        rows = db.execute_query(
            """
            SELECT jp.job_title,
                   jp.job_description,
                   c.status                AS contract_status,
                   ROUND(AVG(rr.score), 1) AS overall_rating,
                   rwc.overall_comment     AS review_text
            FROM contract c
            JOIN job_post jp ON jp.job_post_id = c.job_post_id
            LEFT JOIN reviews rv  ON rv.contract_id = c.contract_id AND rv.status = 'published'
            LEFT JOIN review_written_content rwc ON rwc.review_id = rv.id
            LEFT JOIN review_ratings rr ON rr.review_id = rv.id
            WHERE c.freelancer_id = :fid
              AND c.status = 'completed'
            GROUP BY jp.job_title, jp.job_description, c.status, rwc.overall_comment, c.end_date
            ORDER BY c.end_date DESC NULLS LAST
            LIMIT 5
            """,
            {"fid": freelancer_id},
        )
        retrieval_method = "recency_fallback"

    contracts = [dict(r) for r in rows]
    rated = sum(1 for c in contracts if c.get("overall_rating") is not None)
    avg_rating = (
        sum(float(c["overall_rating"]) for c in contracts if c.get("overall_rating"))
        / rated if rated > 0 else None
    )
    logger(
        "RAG_ANALYSER",
        f"Past contracts retrieved | freelancer_id={freelancer_id} "
        f"| count={len(contracts)} | rated={rated} "
        f"| avg_rating={f'{avg_rating:.2f}' if avg_rating else 'N/A'} "
        f"| method={retrieval_method}",
        level="DEBUG",
    )
    return contracts


def _build_evidence_list(past_contracts: list[dict], portfolio_items: list[dict]) -> tuple[list[dict], list[dict], str]:
    """
    Merge past contracts and portfolio items into one evidence pool for the prompt,
    capped at EVIDENCE_CAP combined.

    A contract ranked by vector similarity must clear RELEVANCE_THRESHOLD to count;
    a contract from the recency fallback (no similarity score) is treated as
    automatically relevant, since there's nothing to gate on. Relevant contracts
    fill slots first (highest similarity first, already the query's own order),
    portfolio items fill whatever's left.

    Returns (used_contracts, used_portfolio, evidence_path), where evidence_path is
    'contract_only', 'portfolio_only', 'mixed', or 'none'.
    """
    relevant_contracts = [
        c for c in past_contracts
        if c.get("similarity") is None or float(c["similarity"]) >= RELEVANCE_THRESHOLD
    ]
    used_contracts = relevant_contracts[:EVIDENCE_CAP]
    remaining = EVIDENCE_CAP - len(used_contracts)
    used_portfolio = portfolio_items[:remaining] if remaining > 0 else []

    if used_contracts and used_portfolio:
        evidence_path = "mixed"
    elif used_contracts:
        evidence_path = "contract_only"
    elif used_portfolio:
        evidence_path = "portfolio_only"
    else:
        evidence_path = "none"

    logger(
        "RAG_ANALYSER",
        f"Evidence list built | contracts_pool={len(past_contracts)} used={len(used_contracts)} "
        f"| portfolio_pool={len(portfolio_items)} used={len(used_portfolio)} "
        f"| evidence_path={evidence_path}",
        level="DEBUG",
    )
    return used_contracts, used_portfolio, evidence_path


def _build_prompt(role: dict, fc: dict, used_contracts: list[dict], used_portfolio: list[dict], fx_rates: dict[str, float]) -> tuple[str, dict]:
    """
    Build the grounded LLM prompt for ONE role from role, freelancer, and gated
    evidence context.

    Pre-computes skill matching so the LLM receives explicit evidence
    (matched/missing skills, coverage %) rather than having to infer it.

    Returns:
        (prompt_string, skill_info); skill_info carries matched_req/missing_req/
        coverage_pct back to analyse_role_match() so it can stitch pre-computed
        fields into the result and apply the coverage ceiling without relying on
        the LLM to copy them verbatim.
    """
    freelancer_skill_map = {
        s["skill_name"].lower(): s for s in fc.get("skills", [])
    }

    skills_list = role.get("skills") or []
    required, preferred = [], []
    for skill_str in skills_list:
        name = skill_str.split("(")[0].strip()
        if "(required" in skill_str.lower():
            required.append(name)
        else:
            preferred.append(name)

    matched_req  = [s for s in required  if s.lower() in freelancer_skill_map]
    missing_req  = [s for s in required  if s.lower() not in freelancer_skill_map]
    matched_pref = [s for s in preferred if s.lower() in freelancer_skill_map]
    coverage_pct = int(len(matched_req) / len(required) * 100) if required else 100

    # Experience gap (pre-computed)
    _EXP_MAP_RAG   = {"entry": 1, "intermediate": 2, "expert": 3}
    _EXP_LABEL_RAG = {1: "entry", 2: "intermediate", 3: "expert"}
    job_exp_num = _EXP_MAP_RAG.get((role.get("experience_level") or "entry").lower(), 1)
    total_jobs  = int(fc.get("total_jobs") or 0)
    work_exp_count = len(fc.get("work_experience") or [])

    # Verified tier: driven entirely by in-platform completed contracts
    verified_exp_num = 3 if total_jobs >= 10 else (2 if total_jobs >= 3 else 1)
    # Unverified floor: self-reported work experience can lift to intermediate only
    unverified_exp_num = min(2, 1 + (1 if work_exp_count >= 1 else 0))

    if verified_exp_num >= unverified_exp_num:
        fl_exp_num = verified_exp_num
        exp_source = f"verified - {total_jobs} completed contract{'s' if total_jobs != 1 else ''}"
    else:
        fl_exp_num = unverified_exp_num
        exp_source = f"self-reported, unverified - {work_exp_count} work experience entr{'ies' if work_exp_count != 1 else 'y'}"

    exp_delta = fl_exp_num - job_exp_num
    if exp_delta >= 0:
        exp_fit_str = (
            f"✓ Meets requirement ({_EXP_LABEL_RAG[fl_exp_num]} ≥ {_EXP_LABEL_RAG[job_exp_num]}, {exp_source})"
        )
    elif exp_delta == -1:
        exp_fit_str = (
            f"△ Slightly under ({_EXP_LABEL_RAG[fl_exp_num]}, job requires {_EXP_LABEL_RAG[job_exp_num]}, {exp_source})"
        )
    else:
        exp_fit_str = (
            f"✗ Under-qualified ({_EXP_LABEL_RAG[fl_exp_num]}, job requires {_EXP_LABEL_RAG[job_exp_num]}, {exp_source})"
        )

    # Build context
    lines = []

    lines.append("JOB POST (background context)")
    lines.append(f"Title:       {role.get('job_title', '')}")
    lines.append(f"Type:        {role.get('project_type', '')} / {role.get('project_scope', '')}")
    lines.append(f"Duration:    {role.get('estimated_duration', 'N/A')}")
    lines.append(f"Description: {(role.get('job_description') or '')[:400]}")

    lines.append(f"\nROLE: {role.get('role_title', '')}")
    if role.get("role_description"):
        lines.append(f"Role description: {(role['role_description'] or '')[:200]}")
    lines.append(
        f"Budget: {role.get('role_budget', 'N/A')} {role.get('budget_currency', '')} "
        f"({role.get('budget_type', '')})"
    )

    lines.append("\nFREELANCER PROFILE")
    lines.append(f"Name:         {fc.get('full_name', '')}")
    if fc.get("title"):
        lines.append(f"Title:        {fc['title']}")
    lines.append(f"Jobs done:    {fc.get('total_jobs', 0)} completed in-platform")
    lines.append(f"Experience:   {_EXP_LABEL_RAG[fl_exp_num]} ({exp_source})")
    lines.append(f"Experience fit: {exp_fit_str}")
    if fc.get("estimated_rate"):
        rate_currency = fc.get("rate_currency") or "USD"
        rate_time     = fc.get("rate_time") or "hourly"
        lines.append(f"Rate:         {fc['estimated_rate']} {rate_currency}/{rate_time}")
    bio = (fc.get("bio") or "")[:250]
    if bio:
        lines.append(f"Bio:          {bio}")
    if fc.get("skills"):
        skill_strs = [
            f"{s['skill_name']}[{s['proficiency_level']}]" if s.get("proficiency_level")
            else s["skill_name"]
            for s in fc["skills"]
        ]
        lines.append(f"Skills:       {', '.join(skill_strs)}")
    if used_portfolio:
        method = fc.get("portfolio_retrieval_method", "recency_fallback")
        rank_label = "most relevant to this role" if method == "vector_similarity" else "most recent"
        lines.append(f"  Portfolio ({rank_label}), self-reported external projects, unverified credibility:")
        for p in used_portfolio:
            relevance = f" [relevance: {p['relevance_score']}]" if p.get("relevance_score") is not None else ""
            lines.append(f"    - {p['project_title']}{relevance}: {(p.get('project_description') or '')[:120]}")
    if fc.get("work_experience"):
        lines.append("  Work Experience:")
        for w in fc["work_experience"]:
            lines.append(
                f"    - {w.get('job_title', '')} at {w.get('company_name', '')}: "
                f"{(w.get('description') or '')[:120]}"
            )

    if used_contracts:
        lines.append("\nPAST COMPLETED CONTRACTS (verified in-app work, primary evidence, prioritise over portfolio)")
        for c in used_contracts:
            rating_str = f"Rating: {c['overall_rating']}/5" if c.get("overall_rating") else "Not yet rated"
            review = (c.get("review_text") or "")[:180]
            lines.append(f"  - {c['job_title']} | {rating_str}")
            if review:
                lines.append(f"    Review: \"{review}\"")
    else:
        lines.append("\nPAST CONTRACTS\nNone relevant on-platform yet.")

    # Rate-to-budget helpers
    _RATE_TO_MONTHLY: dict[str, float] = {
        "hourly": 160.0, "daily": 20.0, "weekly": 4.0, "monthly": 1.0, "annually": 1 / 12,
    }
    fl_rate          = float(fc.get("estimated_rate") or 0)
    fl_rate_time     = (fc.get("rate_time") or "hourly").lower()
    fl_rate_currency = (fc.get("rate_currency") or "USD").upper()
    fl_monthly_rate  = fl_rate * _RATE_TO_MONTHLY.get(fl_rate_time, 160.0)

    lines.append("\nSKILL MATCH (pre-computed, primary evidence for scoring)")
    lines.append(f"Required skill coverage: {len(matched_req)}/{len(required)} = {coverage_pct}%")
    if matched_req:
        matched_with_level = []
        for s in matched_req:
            lvl = freelancer_skill_map.get(s.lower(), {}).get("proficiency_level", "")
            matched_with_level.append(f"{s}[{lvl}]" if lvl else s)
        lines.append(f"Required PRESENT: {', '.join(matched_with_level)} ✓")
    if missing_req:
        lines.append(f"Required ABSENT:  {', '.join(missing_req)} ✗")
    if not required:
        lines.append("No required skills specified")
    if matched_pref:
        lines.append(f"Preferred PRESENT: {', '.join(matched_pref)}")

    # Budget fit: convert both to IDR so cross-currency comparisons are accurate
    role_budget_raw      = role.get("role_budget")
    role_budget_currency = role.get("budget_currency") or "USD"
    if fl_monthly_rate > 0 and role_budget_raw:
        fl_idr     = _to_idr(fl_monthly_rate, fl_rate_currency, fx_rates)
        budget_idr = _to_idr(float(role_budget_raw), role_budget_currency, fx_rates)
        fit        = "✓ Within budget" if fl_idr <= budget_idr * 1.1 else "✗ Exceeds budget"
        same_ccy   = fl_rate_currency.upper() == role_budget_currency.upper()
        if same_ccy and fl_rate_currency.upper() == "IDR":
            lines.append(
                f"Budget fit:  freelancer ~{fl_monthly_rate:,.0f} IDR/month "
                f"vs role budget {float(role_budget_raw):,.0f} IDR → {fit}"
            )
        else:
            lines.append(
                f"Budget fit:  freelancer ~{fl_monthly_rate:.0f} {fl_rate_currency}/month "
                f"(≈{fl_idr:,.0f} IDR) vs role budget {role_budget_raw} {role_budget_currency} "
                f"(≈{budget_idr:,.0f} IDR) → {fit}"
            )

    context = "\n".join(lines)

    band = (
        "score 10-35, recommendation skip"     if coverage_pct <= 33 else
        "score 36-59, recommendation consider" if coverage_pct <= 60 else
        "score 60-74, recommendation apply"    if coverage_pct <= 80 else
        "score 75-100, recommendation apply"
    )

    prompt = f"""You are an AI job matching assistant. Analyse the freelancer's fit for this ONE role.

{context}

Score this role holistically (0-100) considering:
  1. Required skill coverage (shown above, primary factor)
  2. Proficiency levels of matched skills
  3. Directly relevant past contracts (as evidence of experience, not platform credibility)
  4. Portfolio items that demonstrate the role's core skills
  5. Work experience relevance to this role
  6. Experience level fit (shown above, note if under/over-qualified)
  7. Budget fit (shown above, note if rate exceeds budget)

Scoring guidance (skill coverage reference): coverage={coverage_pct}% → {band}

Use coverage as the primary anchor but adjust based on evidence of relevant experience from past
contracts and portfolio. A freelancer with 50% required skills but directly relevant past work
can score higher than one with 50% skills and no demonstrated experience in the domain.
Never give 0 unless the freelancer has absolutely nothing relevant to the role.

VOICE: Write every text field in SECOND PERSON, speaking directly to the freelancer.
Use "you", "your", "you have", "you are missing" - never "the freelancer", "they", or "their".
Example: "Your advanced Python skills cover the core of this role." NOT "The freelancer has advanced Python skills."

Respond ONLY with valid JSON (no markdown, no explanation before or after):
{{
  "match_score": <integer 0-100, holistic score for this role>,
  "recommendation": "<apply/consider/skip>",
  "recommendation_reason": "<2-3 sentences in second person, cite skill coverage, relevant past contracts or portfolio items by name, and your experience level. Be specific and detailed.>",
  "matching_skills": {json.dumps(matched_req)},
  "missing_required_skills": {json.dumps(missing_req)},
  "strengths": ["<3-5 detailed items in second person, specific to this role. For each, explain WHY it matters and reference concrete evidence (e.g. specific past contract name, portfolio project name, your proficiency level, your work experience). Do not write generic statements.>"],
  "gaps": ["<2-4 items in second person about MISSING SKILLS or EXPERIENCE only, do NOT mention metrics, ratings, or success rates. For each: (a) what skill/experience you are missing, (b) why it matters for this role, (c) how significant it is. Write [] if there are truly no skill/experience gaps.>"],
  "skill_tips": ["<2-3 specific, actionable tips addressed directly to you. Name exact technologies, certifications, or project types to pursue to close each gap. Be concrete, not generic.>"]
}}

Rules:
- recommendation: "apply" if score ≥65, "consider" if 40-64, "skip" if <40
- matching_skills / missing_required_skills: already filled above, do NOT change

Return ONLY the JSON."""

    skill_info = {
        "matched_req":  matched_req,
        "missing_req":  missing_req,
        "coverage_pct": coverage_pct,
    }
    return prompt, skill_info


def _parse_llm_json(raw: str, source: str) -> dict:
    """
    Extract and parse a JSON object from the LLM response.

    Handles:
    - Plain JSON responses
    - Markdown fenced blocks (```json ... ```)
    - Responses with preamble/postamble text (finds the first {...} block)

    Args:
        raw: Raw string returned by the LLM.
        source: Label of the LLM source (e.g. "groq") used in debug logging.

    Returns:
        Parsed JSON as a dict. Raises ``json.JSONDecodeError`` if no valid JSON is found.
    """
    raw = raw.strip()

    # 1. Try markdown fences first
    if "```" in raw:
        logger("RAG_ANALYSER", f"Stripping markdown fences from {source} response", level="DEBUG")
        parts = raw.split("```")
        candidate = parts[1]
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass  # fall through to brace extraction

    # 2. Try to parse as-is
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 3. Extract the first complete {...} block (handles preamble/postamble)
    brace_match = re.search(r'\{.*\}', raw, re.DOTALL)
    if brace_match:
        logger("RAG_ANALYSER", f"Extracted JSON block from {source} preamble response", level="DEBUG")
        return json.loads(brace_match.group(0))

    # 4. Nothing worked, let it raise
    return json.loads(raw)


_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_RAG_MODELS = [
    "openai/gpt-oss-120b",      # primary
    "llama-3.3-70b-versatile",  # fallback: separate rate-limit bucket
]


async def _call_groq_rag(prompt: str) -> str:
    """Call GROQ LLM for RAG analysis; returns raw JSON-mode response text."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    max_retries = 3
    base_delay = 2.0

    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        for model in _GROQ_RAG_MODELS:
            for attempt in range(1, max_retries + 1):
                try:
                    body = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.15,
                        "response_format": {"type": "json_object"},
                    }
                    resp = await client.post(_GROQ_CHAT_URL, headers=headers, json=body)
                    resp.raise_for_status()
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    logger("RAG_ANALYSER", f"GROQ response received | model={model} | chars={len(content)}", level="INFO")
                    return content
                except httpx.HTTPStatusError as e:
                    status = e.response.status_code if e.response is not None else None
                    text = e.response.text if e.response is not None else ""
                    if status == 429 and attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
                        logger("RAG_ANALYSER", f"GROQ rate limit on {model}, retry {attempt}/{max_retries} after {delay:.1f}s", level="WARNING")
                        await asyncio.sleep(delay)
                        continue
                    if status == 400 and any(k in text.lower() for k in ("decommissioned", "model_not_found", "not supported")):
                        logger("RAG_ANALYSER", f"GROQ model {model} unavailable, trying next", level="WARNING")
                        break
                    raise
                except httpx.RequestError as e:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.0, 0.5)
                        logger("RAG_ANALYSER", f"GROQ request error on {model}, retry {attempt}/{max_retries}: {e}", level="WARNING")
                        await asyncio.sleep(delay)
                        continue
                    raise

    raise RuntimeError("No GROQ model succeeded for RAG analysis")



async def _call_llm(prompt: str) -> dict:
    """Call GROQ LLM and return parsed JSON result."""
    t0 = time.perf_counter()
    raw = ""
    try:
        logger("RAG_ANALYSER", f"LLM call started | source=groq | prompt_chars={len(prompt)} | timeout={_LLM_TIMEOUT}s", level="INFO")
        raw = await _call_groq_rag(prompt)
        llm_ms = (time.perf_counter() - t0) * 1000
        result = _parse_llm_json(raw, "groq")
        logger(
            "RAG_ANALYSER",
            f"LLM JSON parsed | source=groq | time={llm_ms:.0f}ms "
            f"| match_score={result.get('match_score')} "
            f"| recommendation={result.get('recommendation')}",
            level="INFO",
        )
        logger("RAG_ANALYSER", f"LLM result (full JSON) | source=groq |\n{json.dumps(result, indent=2, default=str)}", level="DEBUG")
        return result
    except json.JSONDecodeError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger("RAG_ANALYSER", f"JSON parse error | source=groq | time={elapsed:.0f}ms | error={exc} | raw_preview={raw[:300]}", level="ERROR")
        return {"error": "LLM returned non-JSON output", "raw_preview": raw[:200]}
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger("RAG_ANALYSER", f"LLM call failed | source=groq | time={elapsed:.0f}ms | error={exc}", level="ERROR")
        return {"error": str(exc)}


async def analyse_role_match(db, freelancer_id: str, job_role_id: str) -> dict:
    """
    Full RAG pipeline for ONE role: retrieve role + freelancer context + gated
    evidence from the DB, build a grounded prompt, call the LLM, and return the
    structured JSON result (match_score, strengths, gaps, recommendation,
    skill_tips). Returns {"error": "..."} on failure -- never a partial dict with
    bookkeeping fields attached, so callers can rely on "error" in result alone.
    """
    t_start = time.perf_counter()
    logger(
        "RAG_ANALYSER",
        f"RAG analysis started | freelancer_id={freelancer_id} | job_role_id={job_role_id}",
        level="INFO",
    )

    t_retrieval = time.perf_counter()
    role           = _retrieve_role_context(db, job_role_id)
    fc             = _retrieve_freelancer_context(db, freelancer_id, job_role_id=job_role_id)
    past_contracts = _retrieve_past_contracts(db, freelancer_id, job_role_id)
    fx_rates       = _get_fx_rates()
    retrieval_ms   = (time.perf_counter() - t_retrieval) * 1000

    if not role:
        logger("RAG_ANALYSER", f"Aborting, job role not found | job_role_id={job_role_id}", level="WARNING")
        return {"error": "Job role not found"}
    if not fc:
        logger("RAG_ANALYSER", f"Aborting, freelancer not found | freelancer_id={freelancer_id}", level="WARNING")
        return {"error": "Freelancer profile not found"}

    used_contracts, used_portfolio, evidence_path = _build_evidence_list(
        past_contracts, fc.get("portfolio", [])
    )

    logger(
        "RAG_ANALYSER",
        f"Context retrieval complete | time={retrieval_ms:.1f}ms "
        f"| freelancer_skills={len(fc.get('skills', []))} "
        f"| evidence_path={evidence_path}",
        level="INFO",
    )

    t_prompt = time.perf_counter()
    prompt, skill_info = _build_prompt(role, fc, used_contracts, used_portfolio, fx_rates)
    prompt_ms = (time.perf_counter() - t_prompt) * 1000
    logger(
        "RAG_ANALYSER",
        f"Prompt built | chars={len(prompt)} | time={prompt_ms:.1f}ms",
        level="DEBUG",
    )

    result = await _call_llm(prompt)

    if "error" not in result:
        # Always use server-computed skill lists; LLM value is discarded
        result["matching_skills"]         = skill_info["matched_req"]
        result["missing_required_skills"] = skill_info["missing_req"]

        # Ceiling: coverage_pct + 30, so 0% → max 30, 35% → max 65, 70%+ → 100.
        # The +30 offset means 35% required skill coverage is the minimum to ever
        # reach "apply" (35+30=65). The LLM cannot exceed this regardless of how
        # strong the portfolio or past contracts are, keeping skill coverage as the
        # primary gate while still giving qualitative evidence 30 points of influence.
        ceiling   = min(100, skill_info["coverage_pct"] + 30)
        raw_score = int(result.get("match_score") or 0)
        result["match_score"] = min(ceiling, raw_score)

        # Recommendation always derived from the (capped) score
        s = result["match_score"]
        result["recommendation"] = (
            "apply" if s >= 65 else ("consider" if s >= 40 else "skip")
        )

        # Guarantee all fields the frontend expects are always present
        result.setdefault("recommendation_reason", "")
        result.setdefault("strengths", [])
        result.setdefault("gaps", [])
        result.setdefault("skill_tips", [])

        result["role_title"]    = role.get("role_title", "")
        result["job_role_id"]   = job_role_id
        result["job_post_id"]   = str(role.get("job_post_id", ""))
        result["freelancer_id"] = freelancer_id
        result["rag_sources"]   = {
            "past_contracts_used": len(used_contracts),
            "portfolio_items":     len(used_portfolio),
            "work_experience":     len(fc.get("work_experience", [])),
            "freelancer_skills":   len(fc.get("skills", [])),
            "evidence_path":       evidence_path,
        }

    total_ms = (time.perf_counter() - t_start) * 1000
    status = "success" if "error" not in result else "error"
    logger(
        "RAG_ANALYSER",
        f"RAG analysis complete | status={status} | total_time={total_ms:.0f}ms "
        f"| match_score={result.get('match_score', 'N/A')} "
        f"| recommendation={result.get('recommendation', 'N/A')}",
        level="INFO",
    )
    return result
