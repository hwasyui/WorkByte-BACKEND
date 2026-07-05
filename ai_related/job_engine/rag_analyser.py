import os
import json
import re
import time
import asyncio
import random

import httpx

from functions.logger import logger

_LLM_TIMEOUT = 90.0

RELEVANCE_THRESHOLD = 0.3

# max combined evidence items (past contracts + portfolio) shown to the LLM
EVIDENCE_CAP = 3


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
               estimated_duration, deadline
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
        GROUP BY jr.job_role_id, jr.role_title, jr.role_description
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
    Retrieve a freelancer's full profile including skills, portfolio items,
    and recent work experience.

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
        Dict with profile fields plus ``skills``, ``portfolio`` (up to EVIDENCE_CAP candidates,
        for the evidence gate in analyse_job_match to draw from), ``portfolio_retrieval_method``,
        and ``work_experience`` (up to 3) lists.
        Returns an empty dict if the freelancer is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_rows = db.execute_query(
        """
        SELECT f.full_name, f.title, f.bio, f.estimated_rate, f.rate_time, f.rate_currency
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
                f"""
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
                  AND COALESCE(p.is_auto_generated, FALSE) = FALSE
                ORDER BY pe.embedding_vector <=> best_role.embedding_vector
                LIMIT {EVIDENCE_CAP}
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
                f"Portfolio embeddings not ready, falling back to recency "
                f"(has_portfolio_embeddings={has_portfolio_embeddings}, has_job_embedding={has_job_embedding})",
                level="DEBUG",
            )
            portfolio_rows = db.execute_query(
                f"""
                SELECT project_title, project_description, project_url
                FROM portfolio
                WHERE freelancer_id = :fid
                  AND COALESCE(is_auto_generated, FALSE) = FALSE
                ORDER BY completion_date DESC NULLS LAST
                LIMIT {EVIDENCE_CAP}
                """,
                {"fid": freelancer_id},
            )
    else:
        portfolio_rows = db.execute_query(
            f"""
            SELECT project_title, project_description, project_url
            FROM portfolio
            WHERE freelancer_id = :fid
              AND COALESCE(is_auto_generated, FALSE) = FALSE
            ORDER BY completion_date DESC NULLS LAST
            LIMIT {EVIDENCE_CAP}
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
        f"| work_exp={len(fc['work_experience'])}",
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
            f"""
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
            LIMIT {EVIDENCE_CAP}
            """,
            {"fid": freelancer_id, "jpid": job_post_id},
        )
        retrieval_method = "vector_similarity"
    else:
        logger(
            "RAG_ANALYSER",
            f"Contract embeddings not ready, falling back to recency order "
            f"(has_contract_embeddings={has_embeddings}, has_job_embedding={has_job_embedding})",
            level="DEBUG",
        )
        rows = db.execute_query(
            f"""
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
            LIMIT {EVIDENCE_CAP}
            """,
            {"fid": freelancer_id},
        )
        retrieval_method = "recency_fallback"

    contracts = [dict(r) for r in rows]
    if retrieval_method == "recency_fallback":
        # no similarity score exists yet in this mode, evidence gating treats these as relevant
        for c in contracts:
            c["similarity"] = None
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


def _build_evidence_list(past_contracts: list[dict], portfolio_candidates: list[dict]) -> tuple[list[dict], dict]:
    """
    Merge past contracts and portfolio items into one evidence list, capped at
    EVIDENCE_CAP, using a relevance-gated, contract-first fill strategy.

    Contracts with cosine similarity >= RELEVANCE_THRESHOLD are "relevant" and take
    priority slots, highest similarity first. Remaining slots (if any) are filled
    with portfolio items ranked by cosine similarity to the same job embedding.
    Zero relevant contracts means the evidence list is portfolio-only.

    When contracts have no similarity score (recency fallback, embeddings not ready),
    there is nothing to gate on, so all retrieved contracts are treated as relevant,
    preserving prior behavior for that fallback path.

    Returns:
        (evidence, stats). Each evidence item is the original contract or portfolio
        dict plus a "source" key ("contract" or "portfolio"). stats holds the counts
        used for structured logging.
    """
    contracts_have_scores = any(c.get("similarity") is not None for c in past_contracts)

    if contracts_have_scores:
        relevant_contracts = [
            c for c in past_contracts if (c.get("similarity") or 0.0) >= RELEVANCE_THRESHOLD
        ]
        relevant_contracts.sort(key=lambda c: c.get("similarity") or 0.0, reverse=True)
    else:
        relevant_contracts = list(past_contracts)

    evidence = [{**c, "source": "contract"} for c in relevant_contracts[:EVIDENCE_CAP]]

    remaining_slots = EVIDENCE_CAP - len(evidence)
    portfolio_used = portfolio_candidates[:remaining_slots] if remaining_slots > 0 else []
    evidence.extend({**p, "source": "portfolio"} for p in portfolio_used)

    if relevant_contracts and portfolio_used:
        path = "mixed"
    elif relevant_contracts:
        path = "contract_only"
    else:
        path = "portfolio_only"

    stats = {
        "relevant_contracts_found": len(relevant_contracts),
        "portfolio_used_to_enrich": len(portfolio_used),
        "top_contract_similarity": relevant_contracts[0].get("similarity") if relevant_contracts else None,
        "path": path,
    }
    return evidence, stats


def _build_prompt(job: dict, fc: dict, evidence: list[dict]) -> tuple[str, list[dict]]:
    """
    Build the grounded LLM prompt from job, freelancer, and evidence context.

    ``evidence`` is the relevance-gated, contract-first merged list built by
    _build_evidence_list: verified contracts fill priority slots first, portfolio
    items fill any remaining slots up to EVIDENCE_CAP total. Each item is labeled
    by source so the LLM still knows contracts are the more credible evidence.

    Analysis is ROLE-CENTRIC: each role receives a full independent evaluation
    (match_score, recommendation, strengths, gaps, skill_tips) based on its own
    required skills. The job post description is context only.

    Pre-computes skill matching per role so the LLM receives explicit evidence
    (matched/missing skills, coverage %) rather than having to infer it.

    Returns:
        (prompt_string, role_analyses_list); the list is passed to analyse_job_match
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
            "required":      required,
            "preferred":     preferred,
            "matched_req":   matched_req,
            "missing_req":   missing_req,
            "matched_pref":  matched_pref,
            "coverage_pct":  coverage_pct,
        })

    # Build context
    lines = []

    lines.append("JOB POST (background context)")
    lines.append(f"Title:       {job.get('job_title', '')}")
    lines.append(f"Type:        {job.get('project_type', '')} / {job.get('project_scope', '')}")
    lines.append(f"Duration:    {job.get('estimated_duration', 'N/A')}")
    lines.append(f"Description: {(job.get('job_description') or '')[:400]}")

    lines.append("\nFREELANCER PROFILE")
    lines.append(f"Name:         {fc.get('full_name', '')}")
    if fc.get("title"):
        lines.append(f"Title:        {fc['title']}")
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
    if fc.get("work_experience"):
        lines.append("  Work Experience:")
        for w in fc["work_experience"]:
            lines.append(
                f"    - {w.get('job_title', '')} at {w.get('company_name', '')}: "
                f"{(w.get('description') or '')[:120]}"
            )

    if evidence:
        lines.append("\nEVIDENCE (ranked by relevance, verified contracts prioritised over self-reported portfolio)")
        for item in evidence:
            if item["source"] == "contract":
                rating_str = f"Rating: {item['overall_rating']}/5" if item.get("overall_rating") else "Not yet rated"
                review = (item.get("review_text") or "")[:180]
                lines.append(f"  - [verified completed contract] {item['job_title']} | {rating_str}")
                if review:
                    lines.append(f"    Review: \"{review}\"")
            else:
                relevance = f" [relevance: {item['relevance_score']}]" if item.get("relevance_score") is not None else ""
                lines.append(
                    f"  - [self-reported portfolio]{relevance} {item['project_title']}: "
                    f"{(item.get('project_description') or '')[:120]}"
                )
    else:
        lines.append("\nEVIDENCE\nNo completed contracts or portfolio items on-platform yet.")

    # Per-role skill match section (primary evidence)
    lines.append("\nPER-ROLE SKILL MATCH (pre-computed, primary evidence for scoring)")
    for ra in role_analyses:
        lines.append(f"\n  ROLE: {ra['role_title']}")
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

    prompt = f"""You are an AI job matching assistant. Analyse the freelancer's fit for EACH ROLE SEPARATELY and independently. The job post description is background only, each role must be evaluated on its own required skills and description.

{context}

Score each role holistically (0-100) considering:
  1. Required skill coverage (shown above, primary factor)
  2. Proficiency levels of matched skills
  3. Directly relevant past contracts (as evidence of experience, not platform credibility)
  4. Portfolio items that demonstrate the role's core skills
  5. Work experience relevance to this specific role

Scoring guidance per role (skill coverage reference):
{role_bands}

Use coverage as the primary anchor but adjust based on evidence of relevant experience from past
contracts and portfolio. A freelancer with 50% required skills but directly relevant past work
can score higher than one with 50% skills and no demonstrated experience in the domain.
Never give 0 unless the freelancer has absolutely nothing relevant to the role.

VOICE: Write every text field in SECOND PERSON, speaking directly to the freelancer.
Use "you", "your", "you have", "you are missing" - never "the freelancer", "they", or "their".
Example: "Your advanced Python skills cover the core of this role." NOT "The freelancer has advanced Python skills."

Respond ONLY with valid JSON (no markdown, no explanation before or after):
{{
  "overall_match_score": <integer, score of the BEST matching role>,
  "overall_recommendation": "<apply/consider/skip, based on BEST fitting role>",
  "overall_recommendation_reason": "<one sentence in second person: which role(s) fit you well and which don't>",
  "roles": {roles_template}
}}

Rules for EACH role object (ALL fields required, never omit):
- match_score: integer 0-100 holistic score for THIS role
- recommendation: "apply" if score ≥65, "consider" if 40-64, "skip" if <40
- recommendation_reason: 2-3 sentences in second person, cite THIS role's skill coverage, relevant
  past contracts or portfolio items by name, and your experience level. Be specific and detailed.
- matching_skills: already filled, do NOT change
- missing_required_skills: already filled, do NOT change
- strengths: 3-5 detailed items in second person, specific to THIS role. For each strength, explain
  WHY it matters for this role and reference concrete evidence (e.g. specific past contract name,
  portfolio project name, your proficiency level, your work experience). Do not write generic statements.
- gaps: 2-4 items in second person about MISSING SKILLS or EXPERIENCE only, do NOT mention metrics,
  ratings, or success rates. For each gap explain: (a) what skill/experience you are missing,
  (b) why it matters for this role, and (c) how significant it is.
  Write [] if there are truly no skill/experience gaps.
- skill_tips: 2-3 specific, actionable tips for THIS role addressed directly to you. Name exact
  technologies, certifications, or project types to pursue to close each gap. Be concrete, not generic.

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
    "llama-3.3-70b-versatile",  # primary: best quality available on Groq free tier
    "llama-3.1-8b-instant",     # fallback: separate rate-limit bucket, fast
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
            f"| overall_match_score={result.get('overall_match_score', result.get('match_score'))} "
            f"| overall_recommendation={result.get('overall_recommendation', result.get('recommendation'))} "
            f"| roles={len(result.get('roles', []))}",
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
        logger("RAG_ANALYSER", f"Aborting, job post not found | job_post_id={job_post_id}", level="WARNING")
        return {"error": "Job post not found"}
    if not fc:
        logger("RAG_ANALYSER", f"Aborting, freelancer not found | freelancer_id={freelancer_id}", level="WARNING")
        return {"error": "Freelancer profile not found"}

    logger(
        "RAG_ANALYSER",
        f"Context retrieval complete | time={retrieval_ms:.1f}ms "
        f"| job_roles={len(job.get('roles', []))} "
        f"| freelancer_skills={len(fc.get('skills', []))} "
        f"| past_contracts={len(past_contracts)}",
        level="INFO",
    )

    evidence, evidence_stats = _build_evidence_list(past_contracts, fc.get("portfolio", []))
    logger(
        "RAG_ANALYSER",
        f"Evidence gate applied | relevant_contracts={evidence_stats['relevant_contracts_found']} "
        f"| portfolio_used_to_enrich={evidence_stats['portfolio_used_to_enrich']} "
        f"| top_contract_similarity={evidence_stats['top_contract_similarity']} "
        f"| path={evidence_stats['path']} | threshold={RELEVANCE_THRESHOLD}",
        level="INFO",
    )

    t_prompt = time.perf_counter()
    prompt, role_analyses = _build_prompt(job, fc, evidence)
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
    # contracts, ratings, experience); we only cap the maximum possible value.
    if "roles" in result and isinstance(result["roles"], list):
        ra_by_title = {ra["role_title"]: ra for ra in role_analyses}
        for role_result in result["roles"]:
            ra = ra_by_title.get(role_result.get("role_title"), {})
            if not ra:
                continue

            # Always use server-computed skill lists; LLM value is discarded
            role_result["matching_skills"]         = ra["matched_req"]
            role_result["missing_required_skills"] = ra["missing_req"]

            # Ceiling: coverage_pct + 30, so 0% → max 30, 35% → max 65, 70%+ → 100.
            # The +30 offset means 35% required skill coverage is the minimum to ever
            # reach "apply" (35+30=65). The LLM cannot exceed this regardless of how
            # strong the portfolio or past contracts are, keeping skill coverage as the
            # primary gate while still giving qualitative evidence 30 points of influence.
            ceiling   = min(100, ra["coverage_pct"] + 30)
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
        "past_contracts_used": sum(1 for e in evidence if e["source"] == "contract"),
        "portfolio_items":     sum(1 for e in evidence if e["source"] == "portfolio"),
        "work_experience":     len(fc.get("work_experience", [])),
        "freelancer_skills":   len(fc.get("skills", [])),
        "job_roles":           len(job.get("roles", [])),
        "evidence_path":       evidence_stats["path"],
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
