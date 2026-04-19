import io
import os
import re
import math
from typing import List, Optional
from fastapi import UploadFile
from groq import Groq
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_matching.embedding_service import get_embedding
from ai_related.job_matching.source_text_builder import build_freelancer_source_text


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
        try:
            import PyPDF2
        except ImportError as e:
            raise RuntimeError(
                "PDF parsing requires PyPDF2. Install it with `pip install PyPDF2` or send raw CV text instead."
            ) from e

        reader = PyPDF2.PdfReader(io.BytesIO(contents))
        pages = [page.extract_text() or "" for page in reader.pages]
        extracted = "\n".join(pages).strip()
        if not extracted:
            raise ValueError("Unable to extract text from the PDF. Ensure the PDF contains selectable text, not scanned images.")
        return extracted

    raise ValueError(
        "Unsupported CV file type. Use text (.txt) or PDF (.pdf), or submit `cv_text` directly."
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


async def build_cv_recommendations(
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

    if os.getenv("GROQ_API_KEY"):
        try:
            groq_advice = await _call_groq_for_cv_advice(
                cv_text=cv_text,
                profile_text=profile_text,
                similarity=similarity,
                skill_coverage=skill_coverage,
                matched_skills=matched_skills,
                missing_skills=missing_skills,
            )
            if groq_advice:
                recommendations.insert(0, groq_advice)
        except Exception as e:
            logger("CV_ANALYSIS", f"Groq fallback error: {e}", level="WARNING")

    return recommendations


async def _call_groq_for_cv_advice(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
) -> Optional[str]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None

    client = Groq(api_key=api_key)

    prompt = (
        "You are an AI assistant that helps improve CVs. "
        "Compare the CV text with the freelancer profile and give one brief recommendation to improve the match. "
        f"Assess semantic similarity {similarity:.3f} and skill coverage {skill_coverage if skill_coverage is not None else 'n/a'}. "
        "If profile skills are missing from the CV, mention them briefly. "
        "Provide the answer in English. "
        "CV:\n" + cv_text[:2000] + "\n\nPROFILE:\n" + profile_text[:2000]
    )

    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            max_tokens=120,
            temperature=0.7
        )

        if completion.choices and completion.choices[0].message:
            return completion.choices[0].message.content.strip()
    except Exception as e:
        logger("CV_ANALYSIS", f"Groq API error: {e}", level="WARNING")

    return None
