"""
RAG Analyser — deep job-freelancer match analysis.

Triggered when a freelancer opens a job detail page. Pulls job requirements,
the freelancer's profile, and their most relevant past contracts from the DB,
builds a grounded prompt, and asks the LLM to return a structured JSON with
match_score, strengths, gaps, recommendation, and skill_tips.
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


def _retrieve_freelancer_context(db, freelancer_id: str) -> dict:
    """
    Retrieve a freelancer's full profile including skills, specialities, languages,
    recent portfolio items, and recent work experience.

    Args:
        db: Active database connection.
        freelancer_id: UUID string of the freelancer.

    Returns:
        Dict with profile fields plus ``skills``, ``specialities``, ``languages``,
        ``portfolio`` (up to 3), and ``work_experience`` (up to 3) lists.
        Returns an empty dict if the freelancer is not found.
    """
    logger("RAG_ANALYSER", f"Retrieving freelancer context | freelancer_id={freelancer_id}", level="DEBUG")

    f_rows = db.execute_query(
        """
        SELECT f.full_name, f.bio, f.estimated_rate, f.rate_time, f.rate_currency,
               f.total_jobs,
               pr.overall_performance_score, pr.success_rate,
               pr.average_result_quality, pr.average_communication
        FROM freelancer f
        LEFT JOIN performance_rating pr ON pr.freelancer_id = f.freelancer_id
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

    # Languages
    langs = db.execute_query(
        """
        SELECT l.language_name, fl.proficiency_level
        FROM freelancer_language fl
        JOIN language l ON l.language_id = fl.language_id
        WHERE fl.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    fc["languages"] = [r["language_name"] for r in langs]

    # Portfolio (most recent 3)
    portfolio = db.execute_query(
        """
        SELECT project_title, project_description, project_url
        FROM portfolio
        WHERE freelancer_id = :fid
        ORDER BY completion_date DESC NULLS LAST
        LIMIT 3
        """,
        {"fid": freelancer_id},
    )
    fc["portfolio"] = [dict(p) for p in portfolio]

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
        f"| languages={fc['languages']} | portfolio={len(fc['portfolio'])} "
        f"| work_exp={len(fc['work_experience'])} | jobs={fc.get('total_jobs', 0)} "
        f"| performance={fc.get('overall_performance_score', 'N/A')} "
        f"| success_rate={fc.get('success_rate', 'N/A')}",
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
        "SELECT embedding_vector FROM job_embedding WHERE job_post_id = :jpid AND embedding_vector IS NOT NULL",
        {"jpid": job_post_id},
    )
    has_job_embedding = bool(job_embedding_check)

    if has_embeddings and has_job_embedding:
        logger("RAG_ANALYSER", "Using vector similarity to rank past contracts", level="DEBUG")
        rows = db.execute_query(
            """
            SELECT jp.job_title,
                   jp.job_description,
                   c.status          AS contract_status,
                   r.overall_rating,
                   r.review_text,
                   r.result_quality_score,
                   r.communication_score,
                   1 - (ce.embedding_vector <=> je.embedding_vector) AS similarity
            FROM contract_embedding ce
            JOIN contract c  ON c.contract_id   = ce.contract_id
            JOIN job_post jp ON jp.job_post_id  = c.job_post_id
            JOIN job_embedding je ON je.job_post_id = :jpid
            LEFT JOIN rating r ON r.contract_id = c.contract_id
            WHERE ce.freelancer_id = :fid
              AND ce.embedding_vector IS NOT NULL
              AND c.status = 'completed'
            ORDER BY ce.embedding_vector <=> je.embedding_vector
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
                   c.status          AS contract_status,
                   r.overall_rating,
                   r.review_text,
                   r.result_quality_score,
                   r.communication_score
            FROM contract c
            JOIN job_post jp   ON jp.job_post_id = c.job_post_id
            LEFT JOIN rating r ON r.contract_id  = c.contract_id
            WHERE c.freelancer_id = :fid
              AND c.status = 'completed'
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
    # ── Pre-compute per-role skill matching ───────────────────────────────────
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

    # ── Build context ─────────────────────────────────────────────────────────
    lines = []

    lines.append("=== JOB POST (background context) ===")
    lines.append(f"Title:       {job.get('job_title', '')}")
    lines.append(f"Type:        {job.get('project_type', '')} / {job.get('project_scope', '')}")
    lines.append(f"Duration:    {job.get('estimated_duration', 'N/A')}")
    lines.append(f"Description: {(job.get('job_description') or '')[:400]}")

    lines.append("\n=== FREELANCER PROFILE ===")
    lines.append(f"Name:         {fc.get('full_name', '')}")
    lines.append(f"Jobs done:    {fc.get('total_jobs', 0)} completed")
    if fc.get("overall_performance_score") is not None:
        lines.append(
            f"Performance:  {fc['overall_performance_score']}/100  |  "
            f"Success rate: {fc.get('success_rate', 'N/A')}%  |  "
            f"Avg quality: {fc.get('average_result_quality', 'N/A')}/5"
        )
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
        lines.append("  Portfolio:")
        for p in fc["portfolio"]:
            lines.append(f"    - {p['project_title']}: {(p.get('project_description') or '')[:120]}")
    if fc.get("work_experience"):
        lines.append("  Work Experience:")
        for w in fc["work_experience"]:
            lines.append(
                f"    - {w.get('job_title', '')} at {w.get('company_name', '')}: "
                f"{(w.get('description') or '')[:120]}"
            )

    if past_contracts:
        lines.append("\n=== PAST COMPLETED CONTRACTS (RAG context) ===")
        for c in past_contracts:
            rating_str = f"Rating: {c['overall_rating']}/5" if c.get("overall_rating") else "Not yet rated"
            review = (c.get("review_text") or "")[:180]
            lines.append(f"  - {c['job_title']} | {rating_str}")
            if review:
                lines.append(f"    Review: \"{review}\"")
    else:
        lines.append("\n=== PAST CONTRACTS ===\nNone yet.")

    # ── Per-role skill match section (primary evidence) ───────────────────────
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

    context = "\n".join(lines)

    # ── Per-role JSON template ────────────────────────────────────────────────
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
  3. Directly relevant past contracts and their ratings/reviews
  4. Portfolio items that demonstrate the role's core skills
  5. Work experience relevance to this specific role
  6. Performance score and success rate

Scoring guidance per role (skill coverage reference):
{role_bands}

Use coverage as the primary anchor but adjust meaningfully based on evidence from past work,
portfolio, and ratings. A freelancer with 50% required skills but strong relevant past contracts
can score higher than one with 50% skills and no track record.
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
- gaps: 2-4 items; for each gap explain: (a) what is missing, (b) why it matters specifically
  for this role, and (c) how significant the gap is. Write [] only if there are truly no gaps.
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
    logger("RAG_ANALYSER", f"Ollama raw preview | {raw[:200]}", level="DEBUG")
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
        config={"temperature": 0.15, "max_output_tokens": 4096},
    )
    raw = response.text.strip()
    logger("RAG_ANALYSER", f"Gemini response received | chars={len(raw)}", level="INFO")
    logger("RAG_ANALYSER", f"Gemini raw preview | {raw[:200]}", level="DEBUG")
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
            f"| match_score={result.get('match_score')} "
            f"| recommendation={result.get('recommendation')} "
            f"| strengths={len(result.get('strengths', []))} "
            f"| gaps={len(result.get('gaps', []))}",
            level="INFO",
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
    fc             = _retrieve_freelancer_context(db, freelancer_id)
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

            # Ceiling: coverage_pct + 25, so 0% → max 25, 50% → max 75, 100% → 100.
            # Prevents e.g. DevOps (33% coverage) from scoring 85 regardless of
            # how good the portfolio is.  LLM scores freely below the ceiling.
            ceiling   = min(100, ra["coverage_pct"] + 25)
            raw_score = int(role_result.get("match_score") or 0)
            role_result["match_score"] = min(ceiling, raw_score)

            # Recommendation always derived from the (capped) score
            s = role_result["match_score"]
            role_result["recommendation"] = (
                "apply" if s >= 65 else ("consider" if s >= 40 else "skip")
            )

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
