import io
import os
import re
import math
import json
from typing import List, Optional, Dict, Any
from fastapi import UploadFile
from groq import Groq
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.source_text_builder import build_freelancer_source_text
from ai_related.job_matching.embedding_service import _get_model


def get_cv_embedding(text: str) -> List[float]:
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


async def extract_cv_text(cv_file: UploadFile) -> str:
    contents = await cv_file.read()
    if not contents:
        raise ValueError("CV file is empty")

    file_name = (cv_file.filename or "").lower()
    if cv_file.content_type.startswith("text/") or file_name.endswith(".txt"):
        return contents.decode("utf-8", errors="replace")

    if file_name.endswith(".pdf"):
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        pages = [page.extract_text() or "" for page in reader.pages]
        extracted = "\n".join(pages).strip()
        if not extracted:
            raise ValueError("Unable to extract text from the PDF. Ensure the PDF contains selectable text.")
        return extracted

    if file_name.endswith(".docx") or cv_file.content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        import docx
        doc = docx.Document(io.BytesIO(contents))
        extracted = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
        if not extracted:
            raise ValueError("Unable to extract text from the DOCX file.")
        return extracted

    raise ValueError("Unsupported CV file type. Please upload a PDF (.pdf) or Word document (.docx).")


def get_profile_skill_names(freelancer_id: str) -> List[str]:
    db = get_db()
    rows = db.execute_query(
        """
        SELECT s.skill_name
        FROM freelancer_skill fs
        JOIN skill s ON s.skill_id = fs.skill_id
        WHERE fs.freelancer_id = :fid
        """,
        {"fid": freelancer_id},
    )
    return [row["skill_name"] for row in rows] if rows else []


def extract_skills_from_text(text: str, skills: List[str]) -> List[str]:
    normalized_text = _normalize_text(text)
    matched = []
    for skill in skills:
        normalized_skill = _normalize_text(skill)
        if not normalized_skill:
            continue
        pattern = r"\b" + re.escape(normalized_skill) + r"\b"
        if re.search(pattern, normalized_text):
            matched.append(skill)
    return matched


def build_freelancer_profile_text(freelancer_id: str) -> Optional[str]:
    return build_freelancer_source_text(freelancer_id)


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        raise ValueError("Embeddings have incompatible lengths")
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return max(min(dot / (mag_a * mag_b), 1.0), -1.0)


def compute_resume_score(similarity: float, skill_coverage: Optional[float]) -> int:
    """Convert similarity + skill coverage to a 0-100 resume score."""
    sim_component = min(100.0, max(0.0, (similarity - 0.30) / 0.55 * 100.0))
    if skill_coverage is not None:
        cov_component = min(100.0, max(0.0, skill_coverage * 100.0))
        return int(round(sim_component * 0.6 + cov_component * 0.4))
    return int(round(sim_component))


def compute_overall_score(resume_score: int, ats_score: int) -> int:
    """Simple average of resume score and ATS score."""
    result = int(round((resume_score + ats_score) / 2))
    logger("CV_ANALYSIS", f"compute_overall_score({resume_score}, {ats_score}) = {result}", level="DEBUG")
    return result


def grade_overall_score(overall_score: int) -> str:
    """Map overall_score (0-100) to one of four grades."""
    if overall_score >= 80:
        return "excellent"
    if overall_score >= 60:
        return "good"
    if overall_score >= 40:
        return "fair"
    return "bad"


def classify_cv_quality(similarity: float, coverage: Optional[float], ats_score: Optional[int] = None) -> str:
    if coverage is not None:
        if similarity >= 0.82 and coverage >= 0.65:
            base = "good"
        elif similarity >= 0.70 and coverage >= 0.40:
            base = "enough"
        else:
            base = "bad"
    else:
        if similarity >= 0.78:
            base = "good"
        elif similarity >= 0.62:
            base = "enough"
        else:
            base = "bad"

    if ats_score is not None:
        if ats_score < 50:
            return "bad"
        if ats_score < 70 and base == "good":
            return "enough"

    return base


def check_ats_compliance(raw_text: str) -> dict:
    """Rule-based ATS compliance check. Returns ats_score (0–100) and ats_flags."""
    import re as _re
    text_lower = raw_text.lower()
    word_count = len(raw_text.split())
    flags: List[str] = []
    score = 0

    section_checks = [
        (["summary", "professional summary", "profile", "about", "objective"], "Missing a Summary or Profile section", 10),
        (["skills", "technical skills", "expertise", "competencies"], "Missing a Skills section", 10),
        (["experience", "work experience", "employment", "employment history"], "Missing a Work Experience section", 10),
        (["education", "academic", "qualification"], "Missing an Education section", 10),
    ]
    for keywords, flag_msg, pts in section_checks:
        if any(kw in text_lower for kw in keywords):
            score += pts
        else:
            flags.append(flag_msg)

    if _re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw_text):
        score += 8
    else:
        flags.append("No email address found")

    if _re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", raw_text):
        score += 7
    else:
        flags.append("No phone number found")

    if 300 <= word_count <= 800:
        score += 15
    elif 200 <= word_count < 300:
        score += 8
        flags.append("CV is too short — aim for at least 300 words")
    elif 800 < word_count <= 1200:
        score += 8
        flags.append("CV is quite long — aim for under 800 words for ATS readability")
    else:
        if word_count < 200:
            flags.append("CV is very short — add more detail on skills, experience, and education")
        else:
            flags.append("CV is very long — condense to the most relevant experience")

    if _re.search(r"\d+\s*(%|percent|users|clients|projects|increase|decrease|revenue|saving)", text_lower):
        score += 10
    else:
        flags.append("No quantifiable achievements found — add numbers, percentages, or metrics to your experience")

    cliches = [
        "team player", "hard-working", "hardworking", "go-getter",
        "think outside the box", "results-oriented", "self-starter",
        "detail-oriented", "passionate about",
    ]
    found_cliches = [c for c in cliches if c in text_lower]
    if not found_cliches:
        score += 10
    else:
        flags.append(f"Avoid cliché phrases: {', '.join(found_cliches[:3])}")

    if _re.search(r"\b(19|20)\d{2}\b", raw_text):
        score += 10
    else:
        flags.append("No dates found in work experience — include start/end years for each role")

    return {"ats_score": score, "ats_flags": flags}


def _call_gemini(system_prompt: str, user_prompt: str, json_mode: bool = False, max_tokens: int = 1500) -> Any:
    """Call Gemini as fallback when GROQ is unavailable."""
    from google import genai as google_genai
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    client = google_genai.Client(api_key=api_key)
    model  = os.getenv("GOOGLE_LLM", "gemini-2.5-flash")
    config: Dict[str, Any] = {
        "system_instruction": system_prompt,
        "temperature": 0.3,
        "max_output_tokens": max_tokens,
    }
    if json_mode:
        config["response_mime_type"] = "application/json"
    response = client.models.generate_content(
        model=model,
        contents=[user_prompt],
        config=config,
    )
    content = response.text.strip()
    if json_mode:
        return json.loads(content)
    return content


def _call_groq(system_prompt: str, user_prompt: str, json_mode: bool = False, max_tokens: int = 1500) -> Any:
    """Call GROQ LLM; falls back to Gemini on any connection/API error."""
    try:
        client = Groq()
        kwargs: Dict[str, Any] = {
            "model": "openai/gpt-oss-20b",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        completion = client.chat.completions.create(**kwargs)
        content = completion.choices[0].message.content.strip()
        if json_mode:
            return json.loads(content)
        return content
    except Exception as groq_err:
        logger("CV_ANALYSIS", f"GROQ failed ({groq_err}), falling back to Gemini", level="WARNING")
        return _call_gemini(system_prompt, user_prompt, json_mode=json_mode, max_tokens=max_tokens)


async def analyze_cv_with_llm(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
    ats_result: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Full structured CV analysis via GROQ.
    All content — assessment, per-section analysis, recommendations — comes from the LLM.
    """
    coverage_str = f"{skill_coverage:.0%}" if skill_coverage is not None else "N/A"
    matched_str = ", ".join(matched_skills[:10]) if matched_skills else "none"
    missing_str = ", ".join(missing_skills[:10]) if missing_skills else "none"
    ats_flags_str = "\n".join(f"- {f}" for f in ats_result.get("ats_flags", [])) or "None"

    system = (
        "You are an expert CV reviewer and career coach. "
        "Analyze the provided CV against the freelancer profile and return valid JSON only. "
        "Do not include markdown fences, explanations, or any text outside the JSON."
    )

    schema_example = {
        "resume_score": 72,
        "overall_assessment": "Concise overall assessment specific to this candidate's CV...",
        "profile_match_analysis": "How well the CV aligns with the freelancer's profile...",
        "sections": [
            {
                "title": "Skills Analysis",
                "analysis": "Specific analysis of skills present, missing, and their relevance...",
                "recommendations": ["Specific actionable recommendation 1", "Specific actionable recommendation 2"]
            },
            {
                "title": "Work Experience",
                "analysis": "Analysis of experience descriptions, impact, and relevance...",
                "recommendations": ["Add quantifiable metrics to each role", "Strengthen impact statements"]
            },
            {
                "title": "Education",
                "analysis": "Assessment of education section completeness and relevance...",
                "recommendations": ["Include relevant coursework or certifications"]
            },
            {
                "title": "ATS Optimization",
                "analysis": "ATS compliance assessment based on formatting and keyword usage...",
                "recommendations": ["Address specific ATS issues found"]
            }
        ]
    }

    user = (
        f"=== ANALYSIS METRICS ===\n"
        f"Profile-CV Similarity: {similarity:.2f} ({similarity * 100:.1f}%)\n"
        f"Skill Coverage: {coverage_str}\n"
        f"Skills Matched: {matched_str}\n"
        f"Skills Missing from CV: {missing_str}\n"
        f"ATS Score: {ats_result.get('ats_score', 0)}/100\n"
        f"ATS Issues Found:\n{ats_flags_str}\n\n"
        f"=== FREELANCER PROFILE ===\n{profile_text[:2500]}\n\n"
        f"=== CV CONTENT ===\n{cv_text[:2500]}\n\n"
        "Analyze this CV and return exactly one JSON object matching this structure:\n"
        f"{json.dumps(schema_example, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- resume_score: integer 0-100 based on CV quality, completeness, and profile alignment\n"
        "- All text must reference specific details from THIS CV and THIS profile — never generic advice\n"
        "- sections must contain exactly these four titles in order: "
        "'Skills Analysis', 'Work Experience', 'Education', 'ATS Optimization'\n"
        "- Each section must have 2-4 specific, actionable recommendations\n"
        "- Return only the JSON object, nothing else"
    )

    try:
        result = _call_groq(system, user, json_mode=True, max_tokens=1800)
        return {
            "resume_score": max(0, min(100, int(result.get("resume_score", 50)))),
            "overall_assessment": str(result.get("overall_assessment", "")),
            "profile_match_analysis": str(result.get("profile_match_analysis", "")),
            "sections": result.get("sections", []),
        }
    except Exception as e:
        logger("CV_ANALYSIS", f"LLM structured analysis failed: {e}", level="ERROR")
        return {
            "resume_score": compute_resume_score(similarity, skill_coverage),
            "overall_assessment": "",
            "profile_match_analysis": "",
            "sections": [],
        }


async def parse_cv_for_profile(cv_text: str) -> Dict[str, Any]:
    """
    Parse CV text into structured profile fields.
    Returns suggested data the frontend can offer as profile auto-fill.
    """
    system = (
        "You are an expert at parsing CVs and resumes into structured data. "
        "Extract profile information and return valid JSON only. "
        "Do not include markdown fences, explanations, or any text outside the JSON."
    )

    schema_example = {
        "suggested_bio": "Professional bio derived from CV content in 2-3 sentences...",
        "skills": ["Python", "FastAPI", "React", "PostgreSQL"],
        "languages": [
            {"name": "English", "proficiency": "fluent"},
            {"name": "Indonesian", "proficiency": "native"}
        ],
        "work_experience": [
            {
                "job_title": "Software Engineer",
                "company_name": "Tech Corp",
                "location": "Jakarta, Indonesia",
                "start_date": "2020-01",
                "end_date": "2022-06",
                "is_current": False,
                "description": "Role responsibilities and achievements..."
            }
        ],
        "education": [
            {
                "institution_name": "University Name",
                "degree": "Bachelor",
                "field_of_study": "Computer Science",
                "start_date": "2016",
                "end_date": "2020",
                "is_current": False,
                "grade": ""
            }
        ]
    }

    user = (
        f"=== CV TEXT ===\n{cv_text[:4000]}\n\n"
        "Parse this CV and return exactly one JSON object matching this structure:\n"
        f"{json.dumps(schema_example, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- suggested_bio: 2-3 sentence professional summary derived from CV content\n"
        "- skills: all technical and relevant soft skills mentioned\n"
        "- languages proficiency must be one of: basic, conversational, fluent, native\n"
        "- dates: use YYYY-MM format when month is known, YYYY when only year is known\n"
        "- is_current: true only if the role or education is ongoing\n"
        "- Do not invent information not present in the CV\n"
        "- Use empty string or empty list when a field cannot be found\n"
        "- Return only the JSON object, nothing else"
    )

    try:
        result = _call_groq(system, user, json_mode=True, max_tokens=2000)
        return result
    except Exception as e:
        logger("CV_ANALYSIS", f"CV profile parsing failed: {e}", level="ERROR")
        return {}


async def build_cv_recommendations(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> List[str]:
    """Flat recommendation list for backward compatibility with cv_upload_routes."""
    ats_result = check_ats_compliance(cv_text)
    analysis = await analyze_cv_with_llm(
        cv_text, profile_text, similarity, skill_coverage,
        matched_skills, missing_skills, ats_result,
    )
    recs: List[str] = []
    for section in analysis.get("sections", []):
        recs.extend(section.get("recommendations", []))
    return recs
