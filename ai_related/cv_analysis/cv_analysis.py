import io
import os
import re
import math
import httpx
from typing import List, Optional
from fastapi import UploadFile
from sentence_transformers import SentenceTransformer
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.source_text_builder import build_freelancer_source_text

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "cv-analysis-model", "cv-matching-model")
_cv_model: Optional[SentenceTransformer] = None


def _get_cv_model() -> SentenceTransformer:
    global _cv_model
    if _cv_model is None:
        logger("CV_ANALYSIS", f"Loading CV matching model from {_MODEL_PATH}", level="INFO")
        _cv_model = SentenceTransformer(_MODEL_PATH)
    return _cv_model


def get_cv_embedding(text: str) -> List[float]:
    model = _get_cv_model()
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
            raise ValueError("Unable to extract text from the PDF. Ensure the PDF contains selectable text, not scanned images.")
        return extracted

    if file_name.endswith(".docx") or cv_file.content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        import docx
        doc = docx.Document(io.BytesIO(contents))
        extracted = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
        if not extracted:
            raise ValueError("Unable to extract text from the DOCX file. Ensure the document contains text content.")
        return extracted

    raise ValueError(
        "Unsupported CV file type. Please upload a PDF (.pdf) or Word document (.docx)."
    )


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
    """
    Rule-based ATS compliance check.
    Returns ats_score (0–100) and ats_flags listing each failed check.
    """
    import re as _re
    text_lower = raw_text.lower()
    word_count = len(raw_text.split())
    flags: List[str] = []
    score = 0

    # Section presence (40 pts)
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

    # Contact info (15 pts)
    if _re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw_text):
        score += 8
    else:
        flags.append("No email address found")

    if _re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", raw_text):
        score += 7
    else:
        flags.append("No phone number found")

    # Word count (15 pts)
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

    # Content quality (30 pts)
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



def _build_llm_prompt(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> str:
    coverage_str = f"{skill_coverage:.0%}" if skill_coverage is not None else "unknown"
    matched_str  = ", ".join(matched_skills[:10]) if matched_skills else "none"
    missing_str  = ", ".join(missing_skills[:10]) if missing_skills else "none"

    return (
        "You are a professional CV reviewer. Carefully read the CV and the freelancer profile below, "
        "then provide exactly 3 specific, actionable recommendations to improve this CV's match with "
        "this particular profile. Each recommendation must reference concrete details from the CV or "
        "profile — do not give generic advice. Format your response as a numbered list (1. 2. 3.) "
        "with no preamble, no headers, and no trailing text.\n\n"
        f"=== ANALYSIS METRICS ===\n"
        f"Similarity score : {similarity:.2f} ({similarity*100:.1f}%)\n"
        f"Skill coverage   : {coverage_str}\n"
        f"Skills matched   : {matched_str}\n"
        f"Skills missing   : {missing_str}\n\n"
        f"=== FREELANCER PROFILE ===\n{profile_text[:2000]}\n\n"
        f"=== CV CONTENT ===\n{cv_text[:2000]}"
    )


async def _call_ollama(prompt: str) -> str:
    base = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
    if "/api/" in base:
        base = base[: base.index("/api/")]
    url = base.rstrip("/") + "/api/generate"
    if "127.0.0.1" in url:
        url = url.replace("127.0.0.1", "host.docker.internal")

    model = os.getenv("OLLAMA_LLM", "gemma4:e2b")
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.3, "num_predict": 512}}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama HTTP {resp.status_code}")

    raw = resp.json().get("response", "").strip()
    if not raw:
        raise RuntimeError("Ollama returned empty response")
    return raw


async def _call_gemini(prompt: str) -> str:
    from google import genai

    project_id = os.getenv("GOOGLE_PROJECT_ID")
    location = os.getenv("GOOGLE_LOCATION", "us-central1")
    model = os.getenv("GOOGLE_LLM", "gemini-2.5-flash")

    if project_id:
        client = genai.Client(vertexai=True, project=project_id, location=location)
    else:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("No GOOGLE_PROJECT_ID or GEMINI_API_KEY configured")
        client = genai.Client(api_key=api_key)

    response = await client.aio.models.generate_content(
        model=model,
        contents=prompt,
        config={"temperature": 0.3, "max_output_tokens": 512},
    )
    return response.text.strip()


async def _call_llm_for_recommendations(prompt: str) -> str:
    mode = os.getenv("LLM", "local").strip().lower()

    if mode == "local":
        try:
            return await _call_ollama(prompt)
        except Exception as e:
            logger("CV_ANALYSIS", f"Ollama failed ({e}) — falling back to Gemini", level="WARNING")
            return await _call_gemini(prompt)
    else:
        return await _call_gemini(prompt)


def _parse_llm_recommendations(llm_text: str) -> List[str]:
    """Split a numbered LLM response into individual recommendation strings."""
    items = re.split(r"\n\s*\d+\.\s+", llm_text)
    # First split element may be empty or a preamble before "1."
    cleaned = []
    for item in items:
        item = item.strip()
        # Strip leading "1. " if the text wasn't split (single-item response)
        item = re.sub(r"^\d+\.\s+", "", item)
        if item:
            cleaned.append(item)
    return cleaned if cleaned else [llm_text.strip()]


async def build_cv_recommendations(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> List[str]:
    prompt = _build_llm_prompt(
        cv_text, profile_text, similarity, skill_coverage, matched_skills, missing_skills
    )
    try:
        llm_text = await _call_llm_for_recommendations(prompt)
        if llm_text:
            return _parse_llm_recommendations(llm_text)
    except Exception as e:
        logger("CV_ANALYSIS", f"LLM recommendation failed: {e}", level="WARNING")

    return []
