"""
RAG Analyser — deep job-freelancer match analysis.

Triggered when a freelancer opens a job detail page. Pulls job requirements,
the freelancer's profile, and their most relevant past contracts from the DB,
builds a grounded prompt, and asks the LLM to return a structured JSON with
match_score, strengths, gaps, recommendation, and skill_tips.

─────────────────────────────────────────────────────────────────────────────
WHY THE RAG match_score DIFFERS FROM THE HOMEPAGE ML match_probability
─────────────────────────────────────────────────────────────────────────────

A freelancer will often see a different number on the homepage card than on
the job detail page. This is intentional — the two scores answer fundamentally
different questions using different methods, different inputs, and different
scales.

1. DIFFERENT QUESTION
   • Homepage ML (ml_ranker.py):
       "Given the ~30–80 jobs that survived Stage 2, which ones should be
       ranked higher than others for this freelancer?"
       → Optimised for RELATIVE ORDERING, not absolute assessment.
         A probability of 0.72 means "72% of the way up this candidate
         list," not "this job is 72% suitable."
   • RAG (rag_analyser.py):
       "How suitable is this freelancer for this specific job, expressed as
       a human-readable integer on a 0–100 scale with explicit apply/
       consider/skip thresholds?"
       → Optimised for ABSOLUTE ASSESSMENT displayed directly to the user.

2. DIFFERENT INPUTS AND DEPTH
   • Homepage ML uses 13 pre-computed numeric features (skill overlap %,
     budget ratio, experience delta, portfolio_relevance, cosine similarity,
     etc.) evaluated in < 1 ms per candidate.
   • RAG uses the full text of the job post, the freelancer's full profile,
     up to 5 past completed contracts ordered by semantic similarity to this
     job (with client reviews and ratings), and an explicit per-role skill
     breakdown. The LLM spends 5–30 s reasoning over this evidence to produce
     a holistic narrative score.
     A contract review saying "exceptional FastAPI work, delivered under
     pressure" is invisible to the ML ranker but contributes directly to
     the RAG score.

3. DIFFERENT SCALE AND CALIBRATION
   • ML probability is calibrated against a training distribution where
     ~30% of (freelancer, job) pairs are positive. A probability of 0.60
     is high in that distribution — it does not mean "60 out of 100."
   • RAG score IS the 0–100 number. Thresholds are explicit and designed
     for user interpretation: apply ≥ 65, consider ≥ 40, skip < 40.

4. HARD COVERAGE CEILING IN RAG (no equivalent in ML)
   RAG applies a server-side ceiling of min(100, coverage_pct + 25).
   A role requiring 5 skills where the freelancer matches 2 (40% coverage)
   has a ceiling of 65 — the LLM cannot score that role above 65 regardless
   of how strong the portfolio is. The ML ranker has no such ceiling; it
   can rank a 40%-overlap freelancer highly if other features (budget fit,
   experience, semantic similarity) are strong.
   This means a freelancer can rank high on the homepage (strong cosine +
   good budget + right experience) but receive a lower RAG score (not
   enough required skills for the specific role).

5. PER-ROLE VS. JOB-LEVEL SCORING
   • ML computes features against the single best-matching role in a job
     post and produces one probability per job.
   • RAG evaluates each role independently and uses the best role score as
     the overall score. A job with one very matching role and one
     mismatched role will score high in RAG on the matching role, while
     the ML ranker's single-job score blends signals across all roles.

6. THE ML PROBABILITY IS STRIPPED — ONLY THE RAG SCORE IS SHOWN
   `match_probability` is computed internally by ml_ranker.py but is
   stripped from the API response before it reaches the client
   (see job_matching_routes.py). It is not shown on the homepage card.
   Reason: it is a relative ranking signal calibrated to a ~30%-positive
   training distribution — a value of 87 means "high in this candidate
   pool," not "87% suitable." Showing it alongside the RAG match_score
   (which is purpose-built for absolute interpretation with apply/
   consider/skip thresholds) would create a confusing mismatch.
   The homepage instead surfaces SHAP match_reasons (why a job ranked
   well) — a more actionable signal than a context-dependent probability.
   The RAG match_score is the only percentage a user ever sees.

EXPECTED DIVERGENCE EXAMPLES
  • Freelancer has 100% skill coverage but zero past contracts:
      ML ranks them high (top skill signals), RAG scores ~65–75 (LLM notes
      absence of demonstrated past work, can't cite evidence).
  • Freelancer has 40% skill coverage but five 5-star relevant contracts:
      ML ranks them moderate (skill gap pulls probability down), RAG scores
      up to ceiling 65 (LLM credits contracts, but ceiling prevents "apply"
      on insufficient skill coverage).
  • Freelancer perfectly fits budget and experience but skills are only
      partially matched:
      ML ranks them high (budget + experience features are strong), RAG
      scores moderate (coverage ceiling limits the score regardless of
      profile depth).

In short: the homepage score is a fast relative ranking signal; the job
detail score is a slow absolute assessment grounded in evidence. They are
designed to complement each other, not agree with each other.
─────────────────────────────────────────────────────────────────────────────
"""

import os
import json
import time
import httpx

from functions.logger import logger

def _ollama_generate_url() -> str:
    """
    Build the Ollama generate endpoint URL from the OLLAMA_URL environment variable.

    Replaces 127.0.0.1 with host.docker.internal so container requests reach the host process.

    Returns:
        Full URL string for the Ollama /api/generate endpoint.
    """
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    if "127.0.0.1" in url:
        url = url.replace("127.0.0.1", "host.docker.internal")
    return url


_LLM_TIMEOUT = 90.0   # user-triggered, so a longer timeout is fine


def _retrieve_job_context(db, job_post_id: str) -> dict:
    """
    Retrieve a job post with all its roles and required/preferred skills from the DB.

    Args:
        db: Active database connection.
        job_post_id: UUID string of the job post to retrieve.

    Returns:
        Dict with job post fields plus a ``roles`` list, each role containing its
        aggregated skills. Returns an empty dict if the job post is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving job context | job_post_id={job_post_id}", level="DEBUG")

    job_rows = db.execute_query(
        """
        SELECT job_title, job_description, project_type, project_scope,
               experience_level, estimated_duration, deadline
        FROM job_post
        WHERE job_post_id = :jpid
        """,
        {"jpid": job_post_id},
    )
    if not job_rows:
        logger("RAG_ANALYSER", f"Job post not found | job_post_id={job_post_id}", level="WARNING")
        return {}
    job = dict(job_rows[0])

    roles = db.execute_query(
        """
        SELECT jr.role_title,
               jr.role_description,
               jr.role_budget,
               jr.budget_type,
               jr.budget_currency,
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
        LEFT JOIN job_role_skill jrs ON jrs.job_role_id = jr.job_role_id
        LEFT JOIN skill s            ON s.skill_id       = jrs.skill_id
        WHERE jr.job_post_id = :jpid
        GROUP BY jr.job_role_id, jr.role_title, jr.role_description,
                 jr.role_budget, jr.budget_type, jr.budget_currency
        ORDER BY jr.display_order
        """,
        {"jpid": job_post_id},
    )
    job["roles"] = [dict(r) for r in roles]

    total_skills = sum(len(r.get("skills") or []) for r in job["roles"])
    logger(
        "RAG_ANALYSER",
        f"Job context retrieved | job_post_id={job_post_id} | title='{job['job_title']}' "
        f"| roles={len(job['roles'])} | total_skills={total_skills}",
        level="DEBUG",
    )
    return job


def _retrieve_freelancer_context(db, freelancer_id: str, job_post_id: str | None = None) -> dict:
    """
    Retrieve a freelancer's full profile including skills, specialities,
    portfolio items, and recent work experience.

    Portfolio items are ranked by cosine similarity to the target job when both
    portfolio_embedding vectors and the job_embedding are available.  This surfaces
    the most *relevant* external projects rather than just the most recent ones.
    Falls back to recency order when embeddings are not yet ready.

    Args:
        db: Active database connection.
        freelancer_id: UUID string of the freelancer.
        job_post_id: UUID of the job being analysed.  When provided, portfolio
            items are ranked by relevance to the job instead of by recency.

    Returns:
        Dict with profile fields plus ``skills``, ``specialities``,
        ``portfolio`` (up to 3), ``portfolio_retrieval_method``, and
        ``work_experience`` (up to 3) lists.
        Returns an empty dict if the freelancer is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_rows = db.execute_query(
        """
        SELECT f.full_name, f.bio, f.estimated_rate, f.rate_time, f.rate_currency,
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

    # Specialities
    specs = db.execute_query(
        """
        SELECT s.speciality_name, fs.is_primary
        FROM freelancer_speciality fs
        JOIN speciality s ON s.speciality_id = fs.speciality_id
        WHERE fs.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    fc["specialities"] = [r["speciality_name"] for r in specs]

    # Portfolio — ranked by cosine similarity to the target job when embeddings
    # are ready; otherwise fall back to most-recent-first.
    # portfolio_embedding contains manually-entered external projects (self-reported).
    # Contracts (auto-generated once a job completes on-platform) are retrieved
    # separately in _retrieve_past_contracts and carry higher credibility.
    portfolio_method = "recency_fallback"
    if job_post_id:
        portfolio_embed_check = db.execute_query(
            """
            SELECT COUNT(*) AS cnt
            FROM portfolio_embedding pe
            WHERE pe.freelancer_id = :fid AND pe.embedding_vector IS NOT NULL
            """,
            {"fid": freelancer_id},
        )
        has_portfolio_embeddings = portfolio_embed_check and int(portfolio_embed_check[0]["cnt"]) > 0

        job_embed_check = db.execute_query(
            "SELECT 1 FROM job_role_embedding WHERE job_post_id = :jpid AND embedding_vector IS NOT NULL LIMIT 1",
            {"jpid": job_post_id},
        )
        has_job_embedding = bool(job_embed_check)

        if has_portfolio_embeddings and has_job_embedding:
            portfolio_rows = db.execute_query(
                """
                SELECT p.project_title,
                       p.project_description,
                       p.project_url,
                       ROUND((1 - (pe.embedding_vector <=> best_role.embedding_vector))::numeric, 3) AS relevance_score
                FROM portfolio_embedding pe
                JOIN portfolio p ON p.portfolio_id = pe.portfolio_id
                CROSS JOIN LATERAL (
                    SELECT jre.embedding_vector
                    FROM job_role_embedding jre
                    WHERE jre.job_post_id = :jpid
                      AND jre.embedding_vector IS NOT NULL
                    ORDER BY jre.embedding_vector <=> pe.embedding_vector
                    LIMIT 1
                ) best_role
                WHERE pe.freelancer_id = :fid
                  AND pe.embedding_vector IS NOT NULL
                ORDER BY pe.embedding_vector <=> best_role.embedding_vector
                LIMIT 3
                """,
                {"fid": freelancer_id, "jpid": job_post_id},
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
                f"Portfolio embeddings not ready — falling back to recency "
                f"(has_portfolio_embeddings={has_portfolio_embeddings}, has_job_embedding={has_job_embedding})",
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
        f"| skills={len(fc['skills'])} | specialities={fc['specialities']} "
        f"| portfolio={len(fc['portfolio'])} ({portfolio_method}) "
        f"| work_exp={len(fc['work_experience'])} | jobs={fc.get('total_jobs', 0)}",
        level="DEBUG",
    )
    return fc


def _retrieve_past_contracts(db, freelancer_id: str, job_post_id: str) -> list[dict]:
    """
    Fetch the most relevant completed contracts for the freelancer, ordered by
    cosine similarity to the target job when embeddings are available, or by
    recency if the sweep worker hasn't run yet.
    """
    logger(
        "RAG_ANALYSER",
        f"Retrieving past contracts (RAG context) | freelancer_id={freelancer_id} | job_post_id={job_post_id}",
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

    job_embedding_check = db.execute_query(
        "SELECT 1 FROM job_role_embedding WHERE job_post_id = :jpid AND embedding_vector IS NOT NULL LIMIT 1",
        {"jpid": job_post_id},
    )
    has_job_embedding = bool(job_embedding_check)

    if has_embeddings and has_job_embedding:
        logger("RAG_ANALYSER", "Using vector similarity to rank past contracts", level="DEBUG")
        rows = db.execute_query(
            """
            SELECT jp.job_title,
                   jp.job_description,
                   c.status                        AS contract_status,
                   ROUND(AVG(rr.score), 1)         AS overall_rating,
                   rwc.overall_comment             AS review_text,
                   1 - (ce.embedding_vector <=> best_role.embedding_vector) AS similarity
            FROM contract_embedding ce
            JOIN contract c  ON c.contract_id   = ce.contract_id
            JOIN job_post jp ON jp.job_post_id  = c.job_post_id
            CROSS JOIN LATERAL (
                SELECT jre.embedding_vector
                FROM job_role_embedding jre
                WHERE jre.job_post_id = :jpid
                  AND jre.embedding_vector IS NOT NULL
                ORDER BY jre.embedding_vector <=> ce.embedding_vector
                LIMIT 1
            ) best_role
            LEFT JOIN reviews rv  ON rv.contract_id = c.contract_id AND rv.status = 'published'
            LEFT JOIN review_written_content rwc ON rwc.review_id = rv.id
            LEFT JOIN review_ratings rr ON rr.review_id = rv.id
            WHERE ce.freelancer_id = :fid
              AND ce.embedding_vector IS NOT NULL
              AND c.status = 'completed'
            GROUP BY jp.job_title, jp.job_description, c.status, rwc.overall_comment,
                     ce.embedding_vector, best_role.embedding_vector
            ORDER BY ce.embedding_vector <=> best_role.embedding_vector
            LIMIT 5
            """,
            {"fid": freelancer_id, "jpid": job_post_id},
        )
        retrieval_method = "vector_similarity"
    else:
        logger(
            "RAG_ANALYSER",
            f"Contract embeddings not ready — falling back to recency order "
            f"(has_contract_embeddings={has_embeddings}, has_job_embedding={has_job_embedding})",
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


def _build_prompt(job: dict, fc: dict, past_contracts: list[dict]) -> tuple[str, list[dict]]:
    """
    Build the grounded LLM prompt from job, freelancer, and past-contract context.

    Analysis is ROLE-CENTRIC: each role receives a full independent evaluation
    (match_score, recommendation, strengths, gaps, skill_tips) based on its own
    required skills. The job post description is context only.

    Pre-computes skill matching per role so the LLM receives explicit evidence
    (matched/missing skills, coverage %) rather than having to infer it.

    Returns:
        (prompt_string, role_analyses_list) — the list is passed to analyse_job_match
        so it can stitch pre-computed fields back into the result without relying on
        the LLM to copy them verbatim.
    """
    # Pre-compute per-role skill matching
    freelancer_skill_map = {
        s["skill_name"].lower(): s for s in fc.get("skills", [])
    }

    role_analyses = []
    for role in job.get("roles", []):
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

        role_analyses.append({
            "role_title":    role.get("role_title", ""),
            "role_desc":     (role.get("role_description") or "")[:200],
            "budget_str":    (
                f"{role.get('role_budget', 'N/A')} {role.get('budget_currency', '')} "
                f"({role.get('budget_type', '')})"
            ),
            "required":      required,
            "preferred":     preferred,
            "matched_req":   matched_req,
            "missing_req":   missing_req,
            "matched_pref":  matched_pref,
            "coverage_pct":  coverage_pct,
        })

    # Experience gap (pre-computed)
    _EXP_MAP_RAG   = {"entry": 1, "intermediate": 2, "expert": 3}
    _EXP_LABEL_RAG = {1: "entry", 2: "intermediate", 3: "expert"}
    job_exp_num = _EXP_MAP_RAG.get((job.get("experience_level") or "entry").lower(), 1)
    total_jobs  = int(fc.get("total_jobs") or 0)
    fl_exp_num  = 3 if total_jobs >= 10 else (2 if total_jobs >= 3 else 1)
    exp_delta   = fl_exp_num - job_exp_num
    if exp_delta >= 0:
        exp_fit_str = (
            f"✓ Meets requirement ({_EXP_LABEL_RAG[fl_exp_num]} ≥ {_EXP_LABEL_RAG[job_exp_num]})"
        )
    elif exp_delta == -1:
        exp_fit_str = (
            f"△ Slightly under ({_EXP_LABEL_RAG[fl_exp_num]}, job requires {_EXP_LABEL_RAG[job_exp_num]})"
        )
    else:
        exp_fit_str = (
            f"✗ Under-qualified ({_EXP_LABEL_RAG[fl_exp_num]}, job requires {_EXP_LABEL_RAG[job_exp_num]})"
        )

    # Build context
    lines = []

    lines.append("=== JOB POST (background context) ===")
    lines.append(f"Title:       {job.get('job_title', '')}")
    lines.append(f"Type:        {job.get('project_type', '')} / {job.get('project_scope', '')}")
    lines.append(f"Duration:    {job.get('estimated_duration', 'N/A')}")
    lines.append(f"Description: {(job.get('job_description') or '')[:400]}")

    lines.append("\n=== FREELANCER PROFILE ===")
    lines.append(f"Name:         {fc.get('full_name', '')}")
    lines.append(f"Jobs done:    {fc.get('total_jobs', 0)} completed")
    lines.append(f"Experience:   {_EXP_LABEL_RAG[fl_exp_num]} (inferred from {total_jobs} completed jobs)")
    lines.append(f"Experience fit: {exp_fit_str}")
    if fc.get("estimated_rate"):
        rate_currency = fc.get("rate_currency") or "USD"
        rate_time     = fc.get("rate_time") or "hourly"
        lines.append(f"Rate:         {fc['estimated_rate']} {rate_currency}/{rate_time}")
    bio = (fc.get("bio") or "")[:250]
    if bio:
        lines.append(f"Bio:          {bio}")
    if fc.get("specialities"):
        lines.append(f"Specialities: {', '.join(fc['specialities'])}")
    if fc.get("skills"):
        skill_strs = [
            f"{s['skill_name']}[{s['proficiency_level']}]" if s.get("proficiency_level")
            else s["skill_name"]
            for s in fc["skills"]
        ]
        lines.append(f"Skills:       {', '.join(skill_strs)}")
    if fc.get("portfolio"):
        method = fc.get("portfolio_retrieval_method", "recency_fallback")
        rank_label = "most relevant to this job" if method == "vector_similarity" else "most recent"
        lines.append(f"  Portfolio ({rank_label}) — self-reported external projects, unverified credibility:")
        for p in fc["portfolio"]:
            relevance = f" [relevance: {p['relevance_score']}]" if p.get("relevance_score") is not None else ""
            lines.append(f"    - {p['project_title']}{relevance}: {(p.get('project_description') or '')[:120]}")
    if fc.get("work_experience"):
        lines.append("  Work Experience:")
        for w in fc["work_experience"]:
            lines.append(
                f"    - {w.get('job_title', '')} at {w.get('company_name', '')}: "
                f"{(w.get('description') or '')[:120]}"
            )

    if past_contracts:
        lines.append("\n=== PAST COMPLETED CONTRACTS (verified in-app work — primary evidence, prioritise over portfolio) ===")
        for c in past_contracts:
            rating_str = f"Rating: {c['overall_rating']}/5" if c.get("overall_rating") else "Not yet rated"
            review = (c.get("review_text") or "")[:180]
            lines.append(f"  - {c['job_title']} | {rating_str}")
            if review:
                lines.append(f"    Review: \"{review}\"")
    else:
        lines.append("\n=== PAST CONTRACTS ===\nNone on-platform yet.")

    # Per-role skill match section (primary evidence)
    # Rate-to-budget helpers (used per role below)
    _RATE_TO_MONTHLY: dict[str, float] = {
        "hourly": 160.0, "daily": 20.0, "weekly": 4.0, "monthly": 1.0, "annually": 1 / 12,
    }
    fl_rate          = float(fc.get("estimated_rate") or 0)
    fl_rate_time     = (fc.get("rate_time") or "hourly").lower()
    fl_rate_currency = (fc.get("rate_currency") or "USD").upper()
    fl_monthly_rate  = fl_rate * _RATE_TO_MONTHLY.get(fl_rate_time, 160.0)

    lines.append("\n=== PER-ROLE SKILL MATCH (pre-computed — primary evidence for scoring) ===")
    for ra in role_analyses:
        lines.append(f"\n  ROLE: {ra['role_title']}")
        lines.append(f"  Budget: {ra['budget_str']}")
        if ra["role_desc"]:
            lines.append(f"  Description: {ra['role_desc']}")
        lines.append(f"  Required skill coverage: {len(ra['matched_req'])}/{len(ra['required'])} = {ra['coverage_pct']}%")
        if ra["matched_req"]:
            matched_with_level = []
            for s in ra["matched_req"]:
                lvl = freelancer_skill_map.get(s.lower(), {}).get("proficiency_level", "")
                matched_with_level.append(f"{s}[{lvl}]" if lvl else s)
            lines.append(f"  Required PRESENT: {', '.join(matched_with_level)} ✓")
        if ra["missing_req"]:
            lines.append(f"  Required ABSENT:  {', '.join(ra['missing_req'])} ✗")
        if not ra["required"]:
            lines.append("  No required skills specified")
        if ra["matched_pref"]:
            lines.append(f"  Preferred PRESENT: {', '.join(ra['matched_pref'])}")
        # Budget fit
        role_budget_raw = next(
            (r.get("role_budget") for r in job.get("roles", []) if r.get("role_title") == ra["role_title"]),
            None,
        )
        role_budget_currency = next(
            (r.get("budget_currency") or "USD" for r in job.get("roles", []) if r.get("role_title") == ra["role_title"]),
            "USD",
        )
        if fl_monthly_rate > 0 and role_budget_raw:
            same_currency = fl_rate_currency.upper() == role_budget_currency.upper()
            if same_currency:
                fit = "✓ Within budget" if fl_monthly_rate <= float(role_budget_raw) * 1.1 else "✗ Exceeds budget"
                lines.append(
                    f"  Budget fit:  freelancer ~{fl_monthly_rate:.0f} {fl_rate_currency}/month "
                    f"vs role budget {role_budget_raw} {role_budget_currency} → {fit}"
                )
            else:
                lines.append(
                    f"  Budget fit:  freelancer ~{fl_monthly_rate:.0f} {fl_rate_currency}/month "
                    f"vs role budget {role_budget_raw} {role_budget_currency} (different currencies — evaluate contextually)"
                )

    context = "\n".join(lines)

    # Per-role JSON template
    roles_template = json.dumps(
        [
            {
                "role_title":               ra["role_title"],
                "match_score":              0,
                "recommendation":           "apply or consider or skip",
                "recommendation_reason":    "",
                "matching_skills":          ra["matched_req"],
                "missing_required_skills":  ra["missing_req"],
                "strengths":                [],
                "gaps":                     [],
                "skill_tips":               []
            }
            for ra in role_analyses
        ],
        indent=2,
    )

    # Coverage band rules per role (injected into prompt so model can apply per-role)
    role_bands = "\n".join(
        f"  {ra['role_title']}: coverage={ra['coverage_pct']}% → "
        + (
            "score 10-35, recommendation skip"   if ra["coverage_pct"] <= 33 else
            "score 36-59, recommendation consider" if ra["coverage_pct"] <= 60 else
            "score 60-74, recommendation apply"  if ra["coverage_pct"] <= 80 else
            "score 75-100, recommendation apply"
        )
        for ra in role_analyses
    )

    prompt = f"""You are an AI job matching assistant. Analyse the freelancer's fit for EACH ROLE SEPARATELY and independently. The job post description is background only — each role must be evaluated on its own required skills, budget, and description.

{context}

Score each role holistically (0-100) considering:
  1. Required skill coverage (shown above — primary factor)
  2. Proficiency levels of matched skills
  3. Directly relevant past contracts (as evidence of experience, not platform credibility)
  4. Portfolio items that demonstrate the role's core skills
  5. Work experience relevance to this specific role
  6. Experience level fit (shown above — note if under/over-qualified)
  7. Budget fit (shown per role above — note if rate exceeds budget)

Scoring guidance per role (skill coverage reference):
{role_bands}

Use coverage as the primary anchor but adjust based on evidence of relevant experience from past
contracts and portfolio. A freelancer with 50% required skills but directly relevant past work
can score higher than one with 50% skills and no demonstrated experience in the domain.
Never give 0 unless the freelancer has absolutely nothing relevant to the role.

Respond ONLY with valid JSON (no markdown, no explanation before or after):
{{
  "overall_match_score": <integer — score of the BEST matching role>,
  "overall_recommendation": "<apply/consider/skip — based on BEST fitting role>",
  "overall_recommendation_reason": "<one sentence: which role(s) fit well and which don't>",
  "roles": {roles_template}
}}

Rules for EACH role object (ALL fields required, never omit):
- match_score: integer 0-100 holistic score for THIS role
- recommendation: "apply" if score ≥65, "consider" if 40-64, "skip" if <40
- recommendation_reason: 2-3 sentences — cite THIS role's skill coverage, relevant past contracts
  or portfolio items by name, and the freelancer's experience level. Be specific and detailed.
- matching_skills: already filled — do NOT change
- missing_required_skills: already filled — do NOT change
- strengths: 3-5 detailed items specific to THIS role. For each strength, explain WHY it matters
  for this role and reference concrete evidence (e.g. specific past contract, portfolio project,
  proficiency level, work experience). Do not write generic statements.
- gaps: 2-4 items about MISSING SKILLS or EXPERIENCE only — do NOT mention metrics, ratings, or
  success rates. For each gap explain: (a) what skill/experience is missing, (b) why it matters
  for this role, and (c) how significant it is. Write [] if there are truly no skill/experience gaps.
- skill_tips: 2-3 specific, actionable tips for THIS role — name exact technologies, certifications,
  or projects the freelancer should pursue to close each gap. Be concrete, not generic.

Return ONLY the JSON."""

    return prompt, role_analyses


def _parse_llm_json(raw: str, source: str) -> dict:
    """
    Extract and parse a JSON object from the LLM response.

    Handles:
    - Plain JSON responses
    - Markdown fenced blocks (```json ... ```)
    - Responses with preamble/postamble text (finds the first {...} block)

    Args:
        raw: Raw string returned by the LLM.
        source: Label of the LLM source (e.g. "ollama", "gemini") used in debug logging.

    Returns:
        Parsed JSON as a dict. Raises ``json.JSONDecodeError`` if no valid JSON is found.
    """
    import re

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

    # 4. Nothing worked — let it raise
    return json.loads(raw)


async def _call_ollama(prompt: str) -> str:
    """
    Send a prompt to the local Ollama /api/generate endpoint and return the raw text response.

    Args:
        prompt: Full prompt string to send to the model.

    Returns:
        Raw response text from Ollama. Raises ``RuntimeError`` on non-200 status
        and ``httpx`` exceptions on network failures.
    """
    url   = _ollama_generate_url()
    model = os.getenv("OLLAMA_LLM", "gemma4:e2b")

    logger(
        "RAG_ANALYSER",
        f"Calling Ollama | url={url} | model={model} | prompt_chars={len(prompt)}",
        level="INFO",
    )
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.15, "num_predict": 4096},
    }
    async with httpx.AsyncClient(timeout=_LLM_TIMEOUT) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")

    raw = resp.json().get("response", "").strip()
    logger("RAG_ANALYSER", f"Ollama response received | chars={len(raw)}", level="INFO")
    if not raw:
        raise RuntimeError("Ollama returned empty response — falling back to Gemini")
    logger("RAG_ANALYSER", f"Ollama raw response (full) |\n{raw}", level="DEBUG")
    return raw


async def _call_google(prompt: str) -> str:
    """
    Send a prompt to Google Gemini via Vertex AI and return the raw text response.

    Args:
        prompt: Full prompt string to send to the model.

    Returns:
        Raw response text from Gemini (trimmed). Raises on network or API errors.
    """
    from google import genai

    project_id = os.getenv("GOOGLE_PROJECT_ID")
    location   = os.getenv("GOOGLE_LOCATION", "us-central1")
    model      = os.getenv("GOOGLE_LLM", "gemini-2.5-flash")

    logger(
        "RAG_ANALYSER",
        f"Calling Google Gemini | project={project_id} | model={model} | prompt_chars={len(prompt)}",
        level="INFO",
    )
    client = genai.Client(vertexai=True, project=project_id, location=location)
    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config={
            "temperature": 0.15,
            "max_output_tokens": 8192,
            "response_mime_type": "application/json",
        },
    )
    raw = response.text.strip()
    logger("RAG_ANALYSER", f"Gemini response received | chars={len(raw)}", level="INFO")
    logger("RAG_ANALYSER", f"Gemini raw response (full) |\n{raw}", level="DEBUG")
    return raw


async def _call_llm(prompt: str) -> dict:
    """
    Call the LLM and return the parsed JSON result.
    LLM="local" tries Ollama first and falls back to Google Gemini on any error.
    LLM="api" goes straight to Gemini.
    """
    mode = os.getenv("LLM", "local").strip().lower()
    logger(
        "RAG_ANALYSER",
        f"LLM call started | mode={mode} | prompt_chars={len(prompt)} | timeout={_LLM_TIMEOUT}s",
        level="INFO",
    )

    t0 = time.perf_counter()
    raw = ""
    source = ""

    try:
        if mode == "local":
            try:
                raw = await _call_ollama(prompt)
                source = "ollama"
            except Exception as ollama_err:
                logger(
                    "RAG_ANALYSER",
                    f"Ollama failed ({type(ollama_err).__name__}: {ollama_err}) — falling back to Gemini",
                    level="WARNING",
                )
                raw = await _call_google(prompt)
                source = "gemini_fallback"
        else:
            raw = await _call_google(prompt)
            source = "gemini"

        llm_ms = (time.perf_counter() - t0) * 1000
        result = _parse_llm_json(raw, source)

        logger(
            "RAG_ANALYSER",
            f"LLM JSON parsed | source={source} | time={llm_ms:.0f}ms "
            f"| overall_match_score={result.get('overall_match_score', result.get('match_score'))} "
            f"| overall_recommendation={result.get('overall_recommendation', result.get('recommendation'))} "
            f"| roles={len(result.get('roles', []))}",
            level="INFO",
        )
        logger(
            "RAG_ANALYSER",
            f"LLM result (full JSON) | source={source} |\n{json.dumps(result, indent=2, default=str)}",
            level="DEBUG",
        )
        return result

    except json.JSONDecodeError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger(
            "RAG_ANALYSER",
            f"JSON parse error | source={source} | time={elapsed:.0f}ms "
            f"| error={exc} | raw_preview={raw[:300]}",
            level="ERROR",
        )
        return {"error": "LLM returned non-JSON output", "raw_preview": raw[:200]}
    except httpx.TimeoutException:
        elapsed = (time.perf_counter() - t0) * 1000
        logger("RAG_ANALYSER", f"Ollama timed out after {elapsed:.0f}ms", level="ERROR")
        return {"error": "LLM request timed out — try again"}
    except httpx.ConnectError as exc:
        logger("RAG_ANALYSER", f"Cannot connect to Ollama | error={exc}", level="ERROR")
        return {"error": "Cannot connect to Ollama. Is it running?"}
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        logger("RAG_ANALYSER", f"LLM call failed | source={source} | time={elapsed:.0f}ms | error={exc}", level="ERROR")
        return {"error": str(exc)}


async def analyse_job_match(db, freelancer_id: str, job_post_id: str) -> dict:
    """
    Full RAG pipeline: retrieve job + freelancer context + past contracts from
    the DB, build a grounded prompt, call the LLM, and return the structured
    JSON result (match_score, strengths, gaps, recommendation, skill_tips).
    Returns {"error": "..."} on failure.
    """
    t_start = time.perf_counter()
    logger(
        "RAG_ANALYSER",
        f"RAG analysis started | freelancer_id={freelancer_id} | job_post_id={job_post_id}",
        level="INFO",
    )

    t_retrieval = time.perf_counter()
    job            = _retrieve_job_context(db, job_post_id)
    fc             = _retrieve_freelancer_context(db, freelancer_id, job_post_id=job_post_id)
    past_contracts = _retrieve_past_contracts(db, freelancer_id, job_post_id)
    retrieval_ms   = (time.perf_counter() - t_retrieval) * 1000

    if not job:
        logger("RAG_ANALYSER", f"Aborting — job post not found | job_post_id={job_post_id}", level="WARNING")
        return {"error": "Job post not found"}
    if not fc:
        logger("RAG_ANALYSER", f"Aborting — freelancer not found | freelancer_id={freelancer_id}", level="WARNING")
        return {"error": "Freelancer profile not found"}

    logger(
        "RAG_ANALYSER",
        f"Context retrieval complete | time={retrieval_ms:.1f}ms "
        f"| job_roles={len(job.get('roles', []))} "
        f"| freelancer_skills={len(fc.get('skills', []))} "
        f"| past_contracts={len(past_contracts)}",
        level="INFO",
    )

    t_prompt = time.perf_counter()
    prompt, role_analyses = _build_prompt(job, fc, past_contracts)
    prompt_ms = (time.perf_counter() - t_prompt) * 1000
    logger(
        "RAG_ANALYSER",
        f"Prompt built | chars={len(prompt)} | roles={len(role_analyses)} | time={prompt_ms:.1f}ms",
        level="DEBUG",
    )

    result = await _call_llm(prompt)

    # Post-process each role: stitch in pre-computed skill lists and apply a
    # coverage-based ceiling so the LLM can't wildly overscore a poor match.
    # The LLM still controls the actual score (considering portfolio, past
    # contracts, ratings, experience) — we only cap the maximum possible value.
    if "roles" in result and isinstance(result["roles"], list):
        ra_by_title = {ra["role_title"]: ra for ra in role_analyses}
        for role_result in result["roles"]:
            ra = ra_by_title.get(role_result.get("role_title"), {})
            if not ra:
                continue

            # Always use server-computed skill lists — LLM value is discarded
            role_result["matching_skills"]         = ra["matched_req"]
            role_result["missing_required_skills"] = ra["missing_req"]

            # Ceiling: coverage_pct + 25, so 0% → max 25, 40% → max 65, 80% → max 100.
            # The +25 offset was chosen to be consistent with the three-way threshold
            # design: Stage 2 requires ≥20% overlap to enter the feed; here, 40%
            # coverage is the minimum that can ever yield "apply" (40+25=65, exactly
            # the apply threshold). Below 40% coverage the LLM cannot recommend apply
            # regardless of portfolio strength. The headroom above coverage lets strong
            # past-contract and portfolio evidence meaningfully boost the score, but
            # not enough to mask a large required-skill gap.
            ceiling   = min(100, ra["coverage_pct"] + 25)
            raw_score = int(role_result.get("match_score") or 0)
            role_result["match_score"] = min(ceiling, raw_score)

            # Recommendation always derived from the (capped) score
            s = role_result["match_score"]
            role_result["recommendation"] = (
                "apply" if s >= 65 else ("consider" if s >= 40 else "skip")
            )

            # Guarantee all fields the frontend expects are always present
            role_result.setdefault("recommendation_reason", "")
            role_result.setdefault("strengths", [])
            role_result.setdefault("gaps", [])
            role_result.setdefault("skill_tips", [])

    # Overall = best role score (the freelancer applies for the role they fit)
    role_scores = [r.get("match_score", 0) for r in result.get("roles", [])]
    if role_scores:
        best = max(role_scores)
        result["overall_match_score"] = best
        result["overall_recommendation"] = (
            "apply" if best >= 65 else ("consider" if best >= 40 else "skip")
        )

    # Top-level aliases for the route logger and any flat consumers
    result["match_score"]    = result.get("overall_match_score",    result.get("match_score"))
    result["recommendation"] = result.get("overall_recommendation", result.get("recommendation"))
    result.setdefault("overall_match_score", 0)
    result.setdefault("overall_recommendation", "skip")
    result.setdefault("overall_recommendation_reason", "")
    result.setdefault("roles", [])

    result["job_post_id"]   = job_post_id
    result["freelancer_id"] = freelancer_id
    result["rag_sources"]   = {
        "past_contracts_used": len(past_contracts),
        "portfolio_items":     len(fc.get("portfolio", [])),
        "work_experience":     len(fc.get("work_experience", [])),
        "freelancer_skills":   len(fc.get("skills", [])),
        "job_roles":           len(job.get("roles", [])),
    }

    total_ms = (time.perf_counter() - t_start) * 1000
    status = "success" if "error" not in result else "error"
    role_scores = [r.get("match_score") for r in result.get("roles", [])]
    logger(
        "RAG_ANALYSER",
        f"RAG analysis complete | status={status} | total_time={total_ms:.0f}ms "
        f"| overall_score={result.get('match_score', 'N/A')} "
        f"| role_scores={role_scores} "
        f"| recommendation={result.get('recommendation', 'N/A')}",
        level="INFO",
    )
    return result
