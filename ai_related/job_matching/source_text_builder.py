"""
Builds natural-language "profile documents" from DB data for embedding.

Freelancer: joins freelancer + specialities + skills +
            work_experience + education + portfolio.
Job:        joins job_post + job_role(s) + job_role_skill(s).

The resulting text is stored as source_text in the embedding tables so you
can audit exactly what went into each vector.
"""

from typing import Optional
from functions.db_manager import get_db
from functions.logger import logger


def build_freelancer_source_text(freelancer_id: str) -> Optional[str]:
    """
    Denormalize a freelancer's complete profile into a single descriptive text.
    Returns None if the freelancer does not exist.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building freelancer profile text | freelancer_id={freelancer_id}", level="INFO")
    try:
        db = get_db()

        rows = db.execute_query(
            """SELECT full_name, bio, estimated_rate, rate_time, rate_currency
               FROM freelancer WHERE freelancer_id = :fid""",
            {"fid": freelancer_id},
        )
        if not rows:
            logger("SOURCE_TEXT_BUILDER", f"Freelancer {freelancer_id} not found in DB — skipping", level="WARNING")
            return None
        f = dict(rows[0])
        logger("SOURCE_TEXT_BUILDER", f"Core profile loaded | name={f.get('full_name')}", level="DEBUG")

        parts: list[str] = []

        # specialities
        spec_rows = db.execute_query(
            """SELECT s.speciality_name, fs.is_primary
               FROM freelancer_speciality fs
               JOIN speciality s ON s.speciality_id = fs.speciality_id
               WHERE fs.freelancer_id = :fid
               ORDER BY fs.is_primary DESC, s.speciality_name ASC""",
            {"fid": freelancer_id},
        )
        if spec_rows:
            specs = [
                f"{r['speciality_name']}{' (primary)' if r['is_primary'] else ''}"
                for r in spec_rows
            ]
            parts.append(f"Specialities: {', '.join(specs)}")
            logger("SOURCE_TEXT_BUILDER", f"  {len(spec_rows)} speciality(s) added", level="DEBUG")
        else:
            logger("SOURCE_TEXT_BUILDER", "  No specialities found", level="DEBUG")

        # skills
        skill_rows = db.execute_query(
            """SELECT s.skill_name, s.skill_category, fs.proficiency_level
               FROM freelancer_skill fs
               JOIN skill s ON s.skill_id = fs.skill_id
               WHERE fs.freelancer_id = :fid
               ORDER BY
                 CASE fs.proficiency_level
                   WHEN 'expert'       THEN 1
                   WHEN 'advanced'     THEN 2
                   WHEN 'intermediate' THEN 3
                   WHEN 'beginner'     THEN 4
                   ELSE 5
                 END, s.skill_name ASC""",
            {"fid": freelancer_id},
        )
        if skill_rows:
            skill_parts = []
            for r in skill_rows:
                entry = r["skill_name"]
                if r["proficiency_level"]:
                    entry += f" ({r['proficiency_level']})"
                skill_parts.append(entry)
            parts.append(f"Skills: {', '.join(skill_parts)}")
            logger("SOURCE_TEXT_BUILDER", f"  {len(skill_rows)} skill(s) added", level="DEBUG")
        else:
            logger("SOURCE_TEXT_BUILDER", "  No skills found", level="DEBUG")

        if f.get("estimated_rate"):
            currency = f.get("rate_currency") or "USD"
            rate_time = f.get("rate_time") or "hourly"
            parts.append(f"Rate: {f['estimated_rate']} {currency}/{rate_time}")
            logger("SOURCE_TEXT_BUILDER", f"  Rate added: {f['estimated_rate']} {currency}/{rate_time}", level="DEBUG")

        if f.get("bio"):
            parts.append(f"Bio: {f['bio']}")
            logger("SOURCE_TEXT_BUILDER", f"  Bio added (len={len(f['bio'])})", level="DEBUG")

        # work experience
        work_rows = db.execute_query(
            """SELECT job_title, company_name, start_date, end_date, is_current, description
               FROM work_experience
               WHERE freelancer_id = :fid
               ORDER BY start_date DESC""",
            {"fid": freelancer_id},
        )
        if work_rows:
            exp_lines = []
            for r in work_rows:
                end = "Present" if r["is_current"] else (
                    str(r["end_date"]) if r["end_date"] else ""
                )
                duration = f"{r['start_date']} - {end}" if end else str(r["start_date"])
                line = f"{r['job_title']} at {r['company_name']} ({duration})"
                if r.get("description"):
                    line += f": {r['description']}"
                exp_lines.append(f"  - {line}")
            parts.append("Work Experience:\n" + "\n".join(exp_lines))
            logger("SOURCE_TEXT_BUILDER", f"  {len(work_rows)} work experience(s) added", level="DEBUG")

        # education
        edu_rows = db.execute_query(
            """SELECT degree, field_of_study, institution_name
               FROM education
               WHERE freelancer_id = :fid
               ORDER BY start_date DESC""",
            {"fid": freelancer_id},
        )
        if edu_rows:
            edu_lines = []
            for r in edu_rows:
                field = f" in {r['field_of_study']}" if r.get("field_of_study") else ""
                edu_lines.append(
                    f"  - {r['degree']}{field} from {r['institution_name']}"
                )
            parts.append("Education:\n" + "\n".join(edu_lines))
            logger("SOURCE_TEXT_BUILDER", f"  {len(edu_rows)} education record(s) added", level="DEBUG")

        # portfolio
        port_rows = db.execute_query(
            """SELECT project_title, project_description
               FROM portfolio
               WHERE freelancer_id = :fid
               ORDER BY created_at DESC""",
            {"fid": freelancer_id},
        )
        if port_rows:
            port_lines = []
            for r in port_rows:
                if r.get("project_description"):
                    port_lines.append(
                        f"  - {r['project_title']}: {r['project_description']}"
                    )
                else:
                    port_lines.append(f"  - {r['project_title']}")
            if port_lines:
                parts.append("Portfolio:\n" + "\n".join(port_lines))
                logger("SOURCE_TEXT_BUILDER", f"  {len(port_lines)} portfolio project(s) added", level="DEBUG")

        source_text = "\n".join(parts) if parts else f["full_name"]
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Freelancer source text built | freelancer_id={freelancer_id} | sections={len(parts)} | total_chars={len(source_text)}",
            level="INFO",
        )
        return source_text

    except Exception as e:
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Error building freelancer source text | freelancer_id={freelancer_id} | error={e}",
            level="ERROR",
        )
        raise


def build_contract_source_text(contract_id: str) -> Optional[str]:
    """
    Build source text for a completed contract embedding.
    Aggregates role title, job title, completion date, job description, and the
    client's review text so the vector captures what work was done, when, and
    how it was received. Returns None if the contract does not exist.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building contract source text | contract_id={contract_id}", level="INFO")
    try:
        db = get_db()

        rows = db.execute_query(
            """
            SELECT c.role_title,
                   c.actual_completion_date,
                   c.end_date,
                   jp.job_title,
                   jp.job_description,
                   ROUND(AVG(rr.score), 1) AS overall_rating,
                   rwc.overall_comment     AS review_text
            FROM contract c
            JOIN job_post jp ON jp.job_post_id = c.job_post_id
            LEFT JOIN reviews rv  ON rv.contract_id = c.contract_id AND rv.status = 'published'
            LEFT JOIN review_written_content rwc ON rwc.review_id = rv.id
            LEFT JOIN review_ratings rr ON rr.review_id = rv.id
            WHERE c.contract_id = :cid
            GROUP BY c.role_title, c.actual_completion_date, c.end_date,
                     jp.job_title, jp.job_description, rwc.overall_comment
            """,
            {"cid": contract_id},
        )
        if not rows:
            logger("SOURCE_TEXT_BUILDER", f"Contract {contract_id} not found in DB — skipping", level="WARNING")
            return None

        row = dict(rows[0])
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Contract record loaded | role='{row.get('role_title')}' | job='{row.get('job_title')}' "
            f"| rated={row.get('overall_rating') is not None}",
            level="DEBUG",
        )

        parts: list[str] = []
        parts.append(f"Completed Role: {row['role_title']}")
        parts.append(f"Job: {row['job_title']}")

        # Completion date — gives the embedding a recency signal so semantic
        # queries like "recent ETL work" can rank fresh contracts above stale ones.
        completion = row.get("actual_completion_date") or row.get("end_date")
        if completion:
            parts.append(f"Completed: {completion}")

        if row.get("job_description"):
            parts.append(f"Description: {row['job_description'][:600]}")

        if row.get("overall_rating") is not None:
            parts.append(f"Client Rating: {row['overall_rating']}/5")

        if row.get("review_text"):
            parts.append(f"Client Review: {row['review_text']}")

        source_text = "\n".join(parts)
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Contract source text built | contract_id={contract_id} | sections={len(parts)} | total_chars={len(source_text)}",
            level="INFO",
        )
        return source_text

    except Exception as e:
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Error building contract source text | contract_id={contract_id} | error={e}",
            level="ERROR",
        )
        raise


def build_portfolio_source_text(portfolio_id: str) -> Optional[str]:
    """
    Build source text for a manual portfolio embedding.

    Only meant for user-curated showcase items (`is_auto_generated = FALSE`).
    Auto-generated portfolio rows mirror contract data and are already covered
    by `contract_embedding`; embedding them again would duplicate vectors and
    create a sync problem when the contract's rating updates later. Callers
    must check `is_auto_generated` before invoking this.

    Returns None if the portfolio row does not exist or is auto-generated.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building portfolio source text | portfolio_id={portfolio_id}", level="INFO")
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT project_title, project_description, completion_date,
                      is_auto_generated
               FROM portfolio
               WHERE portfolio_id = :pid""",
            {"pid": portfolio_id},
        )
        if not rows:
            logger("SOURCE_TEXT_BUILDER", f"Portfolio {portfolio_id} not found in DB — skipping", level="WARNING")
            return None

        row = dict(rows[0])
        if row.get("is_auto_generated"):
            logger(
                "SOURCE_TEXT_BUILDER",
                f"Portfolio {portfolio_id} is auto-generated from a contract — skip embedding "
                "(already covered by contract_embedding)",
                level="INFO",
            )
            return None

        parts: list[str] = []
        parts.append(f"Portfolio Project: {row['project_title']}")

        completion = row.get("completion_date")
        if completion:
            parts.append(f"Completed: {completion}")

        if row.get("project_description"):
            parts.append(f"Description: {row['project_description']}")

        source_text = "\n".join(parts)
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Portfolio source text built | portfolio_id={portfolio_id} | sections={len(parts)} | total_chars={len(source_text)}",
            level="INFO",
        )
        return source_text

    except Exception as e:
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Error building portfolio source text | portfolio_id={portfolio_id} | error={e}",
            level="ERROR",
        )
        raise


def build_job_role_source_text(job_role_id: str) -> Optional[str]:
    """
    Build source text for a single job role.

    Includes parent job post context (title + description + meta) so the vector
    captures the project's overall intent, then adds the role-specific title,
    description, budget and skills. This keeps each role's vector precise while
    still encoding shared project context.

    Returns None if the role does not exist.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building job role source text | job_role_id={job_role_id}", level="INFO")
    try:
        db = get_db()

        rows = db.execute_query(
            """SELECT jp.job_title, jp.job_description, jp.project_type, jp.project_scope,
                      jp.estimated_duration, jp.experience_level,
                      jr.role_title, jr.role_description,
                      jr.role_budget, jr.budget_currency, jr.budget_type
               FROM job_role jr
               JOIN job_post jp ON jp.job_post_id = jr.job_post_id
               WHERE jr.job_role_id = :jrid""",
            {"jrid": job_role_id},
        )
        if not rows:
            logger("SOURCE_TEXT_BUILDER", f"Job role {job_role_id} not found in DB — skipping", level="WARNING")
            return None

        r = dict(rows[0])
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Job role loaded | role='{r.get('role_title')}' | job='{r.get('job_title')}'",
            level="DEBUG",
        )

        parts: list[str] = []
        parts.append(f"Job Title: {r['job_title']}")
        parts.append(f"Role: {r['role_title']}")

        if r.get("job_description"):
            parts.append(f"Description: {r['job_description']}")

        if r.get("role_description"):
            parts.append(f"Role Description: {r['role_description']}")

        meta: list[str] = []
        if r.get("project_type"):
            meta.append(f"Type: {r['project_type']}")
        if r.get("project_scope"):
            meta.append(f"Scope: {r['project_scope']}")
        if r.get("estimated_duration"):
            meta.append(f"Duration: {r['estimated_duration']}")
        if r.get("experience_level"):
            meta.append(f"Experience Required: {r['experience_level']}")
        if meta:
            parts.append(" | ".join(meta))

        if r.get("role_budget"):
            currency = r.get("budget_currency") or "USD"
            budget_type = r.get("budget_type") or ""
            parts.append(f"Budget: {r['role_budget']} {currency} ({budget_type})")

        skill_rows = db.execute_query(
            """SELECT s.skill_name, jrs.is_required, jrs.importance_level
               FROM job_role_skill jrs
               JOIN skill s ON s.skill_id = jrs.skill_id
               WHERE jrs.job_role_id = :jrid
               ORDER BY jrs.is_required DESC,
                 CASE jrs.importance_level
                   WHEN 'required'     THEN 1
                   WHEN 'preferred'    THEN 2
                   WHEN 'nice_to_have' THEN 3
                   ELSE 4
                 END""",
            {"jrid": job_role_id},
        )
        if skill_rows:
            required  = [s["skill_name"] for s in skill_rows if s["is_required"]]
            preferred = [
                f"{s['skill_name']} ({s['importance_level']})"
                for s in skill_rows if not s["is_required"]
            ]
            if required:
                parts.append(f"Required Skills: {', '.join(required)}")
            if preferred:
                parts.append(f"Preferred Skills: {', '.join(preferred)}")
            logger(
                "SOURCE_TEXT_BUILDER",
                f"  Skills: {len(required)} required + {len(preferred)} preferred",
                level="DEBUG",
            )

        source_text = "\n".join(parts)
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Job role source text built | job_role_id={job_role_id} | total_chars={len(source_text)}",
            level="INFO",
        )
        return source_text

    except Exception as e:
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Error building job role source text | job_role_id={job_role_id} | error={e}",
            level="ERROR",
        )
        raise
