"""
Builds natural-language "profile documents" from DB data for embedding.

Freelancer: joins freelancer + specialities + skills + languages +
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

        # languages
        lang_rows = db.execute_query(
            """SELECT l.language_name, fl.proficiency_level
               FROM freelancer_language fl
               JOIN language l ON l.language_id = fl.language_id
               WHERE fl.freelancer_id = :fid""",
            {"fid": freelancer_id},
        )
        if lang_rows:
            langs = [
                f"{r['language_name']} ({r['proficiency_level']})" for r in lang_rows
            ]
            parts.append(f"Languages: {', '.join(langs)}")
            logger("SOURCE_TEXT_BUILDER", f"  {len(lang_rows)} language(s) added", level="DEBUG")

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
    Aggregates role title, job title, job description, and the client's
    review text so the vector captures what work was actually done and how
    it was received.  Returns None if the contract does not exist.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building contract source text | contract_id={contract_id}", level="INFO")
    try:
        db = get_db()

        rows = db.execute_query(
            """
            SELECT c.role_title,
                   jp.job_title,
                   jp.job_description,
                   r.overall_rating,
                   r.review_text
            FROM contract c
            JOIN job_post jp   ON jp.job_post_id = c.job_post_id
            LEFT JOIN rating r ON r.contract_id  = c.contract_id
            WHERE c.contract_id = :cid
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


def build_job_source_text(job_post_id: str) -> Optional[str]:
    """
    Denormalize a job post + all its roles + required skills into a single
    descriptive text. Returns None if the job post does not exist.
    """
    logger("SOURCE_TEXT_BUILDER", f"Building job post source text | job_post_id={job_post_id}", level="INFO")
    try:
        db = get_db()

        jp_rows = db.execute_query(
            """SELECT job_title, job_description, project_type, project_scope,
                      estimated_duration, experience_level
               FROM job_post
               WHERE job_post_id = :jpid""",
            {"jpid": job_post_id},
        )
        if not jp_rows:
            logger("SOURCE_TEXT_BUILDER", f"Job post {job_post_id} not found in DB — skipping", level="WARNING")
            return None
        jp = dict(jp_rows[0])
        logger("SOURCE_TEXT_BUILDER", f"Job post core loaded | title={jp.get('job_title')}", level="DEBUG")

        parts: list[str] = []
        parts.append(f"Job Title: {jp['job_title']}")

        if jp.get("job_description"):
            parts.append(f"Description: {jp['job_description']}")

        meta: list[str] = []
        if jp.get("project_type"):
            meta.append(f"Type: {jp['project_type']}")
        if jp.get("project_scope"):
            meta.append(f"Scope: {jp['project_scope']}")
        if jp.get("estimated_duration"):
            meta.append(f"Duration: {jp['estimated_duration']}")
        if jp.get("experience_level"):
            meta.append(f"Experience Required: {jp['experience_level']}")
        if meta:
            parts.append(" | ".join(meta))

        # roles and their skills
        role_rows = db.execute_query(
            """SELECT job_role_id, role_title, role_description,
                      role_budget, budget_currency, budget_type
               FROM job_role
               WHERE job_post_id = :jpid
               ORDER BY display_order ASC""",
            {"jpid": job_post_id},
        )
        logger("SOURCE_TEXT_BUILDER", f"  {len(role_rows) if role_rows else 0} role(s) found", level="DEBUG")

        for role in (role_rows or []):
            role_parts: list[str] = [f"Role: {role['role_title']}"]

            if role.get("role_description"):
                role_parts.append(f"Role Description: {role['role_description']}")

            if role.get("role_budget"):
                currency = role.get("budget_currency") or "USD"
                budget_type = role.get("budget_type") or ""
                role_parts.append(
                    f"Budget: {role['role_budget']} {currency} ({budget_type})"
                )

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
                {"jrid": str(role["job_role_id"])},
            )
            if skill_rows:
                required = [r["skill_name"] for r in skill_rows if r["is_required"]]
                preferred = [
                    f"{r['skill_name']} ({r['importance_level']})"
                    for r in skill_rows
                    if not r["is_required"]
                ]
                if required:
                    role_parts.append(f"Required Skills: {', '.join(required)}")
                if preferred:
                    role_parts.append(f"Preferred Skills: {', '.join(preferred)}")
                logger(
                    "SOURCE_TEXT_BUILDER",
                    f"  Role '{role['role_title']}': {len(required)} required + {len(preferred)} preferred skills",
                    level="DEBUG",
                )

            parts.append("\n".join(role_parts))

        source_text = "\n".join(parts)
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Job source text built | job_post_id={job_post_id} | roles={len(role_rows) if role_rows else 0} | total_chars={len(source_text)}",
            level="INFO",
        )
        return source_text

    except Exception as e:
        logger(
            "SOURCE_TEXT_BUILDER",
            f"Error building job source text | job_post_id={job_post_id} | error={e}",
            level="ERROR",
        )
        raise
