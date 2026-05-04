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


def classify_cv_quality(similarity: float, coverage: Optional[float]) -> str:
    if coverage is not None:
        if similarity >= 0.82 and coverage >= 0.65:
            return "good"
        if similarity >= 0.70 and coverage >= 0.40:
            return "enough"
        return "bad"
    if similarity >= 0.78:
        return "good"
    if similarity >= 0.62:
        return "enough"
    return "bad"


def _build_rule_based_recommendations(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> List[str]:
    recommendations = []

    if similarity < 0.82:
        recommendations.append(
            "Improve CV alignment with your profile by adding a clear professional summary, quantifiable achievements, and the profile's key skills."
        )
    else:
        recommendations.append(
            "Your CV is reasonably aligned with your profile. Strengthen it by highlighting concrete results and relevant project achievements."
        )

    if missing_skills:
        recommendations.append(
            "Add or emphasize the following skills in your CV: "
            + ", ".join(missing_skills[:8])
        )
    elif matched_skills:
        recommendations.append(
            "Keep highlighting skills that already match your profile: "
            + ", ".join(matched_skills[:8])
        )

    if skill_coverage is not None and skill_coverage < 0.5:
        recommendations.append(
            "This CV currently covers only a small portion of your profile skills. Make sure key skills appear in the summary and work experience sections."
        )

    if len(_normalize_text(cv_text)) > len(_normalize_text(profile_text)) * 1.5:
        recommendations.append(
            "Your CV appears longer than your profile. Focus on the most relevant experience and remove redundancy."
        )

    if len(_normalize_text(cv_text)) < len(_normalize_text(profile_text)) * 0.5:
        recommendations.append(
            "Your CV is relatively short. Add more detail on experience, results, and skills that support your profile."
        )

    return recommendations


def _build_llm_prompt(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> str:
    coverage_str = f"{skill_coverage:.0%}" if skill_coverage is not None else "unknown"
    matched_str = ", ".join(matched_skills[:10]) if matched_skills else "none"
    missing_str = ", ".join(missing_skills[:10]) if missing_skills else "none"

    return (
        "You are a professional CV reviewer. Analyze the CV against the freelancer profile below "
        "and provide 2-3 specific, actionable recommendations to improve the CV's match with the profile. "
        "Be concise and practical. Return only the recommendations as a numbered list, no preamble.\n\n"
        f"Similarity score: {similarity:.2f} | Skill coverage: {coverage_str}\n"
        f"Skills matched: {matched_str}\n"
        f"Skills missing from CV: {missing_str}\n\n"
        f"FREELANCER PROFILE:\n{profile_text[:1500]}\n\n"
        f"CV:\n{cv_text[:1500]}"
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


async def build_cv_recommendations(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> List[str]:
    rule_based = _build_rule_based_recommendations(
        cv_text, profile_text, similarity, skill_coverage, matched_skills, missing_skills
    )

    try:
        prompt = _build_llm_prompt(
            cv_text, profile_text, similarity, skill_coverage, matched_skills, missing_skills
        )
        llm_text = await _call_llm_for_recommendations(prompt)
        if llm_text:
            return [llm_text] + rule_based
    except Exception as e:
        logger("CV_ANALYSIS", f"LLM recommendation failed, using rule-based only: {e}", level="WARNING")

    return rule_based
