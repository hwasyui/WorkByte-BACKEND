import io
import os
import re
import math
import httpx
from typing import List, Optional
from fastapi import UploadFile
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
            raise ValueError("Tidak dapat mengekstrak teks dari PDF. Pastikan file PDF berisi teks bukan gambar.")
        return extracted

    raise ValueError(
        "Tipe file CV tidak didukung. Gunakan teks (.txt) atau PDF (.pdf), atau kirim `cv_text` langsung."
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
            "Perkuat kesesuaian antara CV dan profil Anda dengan mencantumkan ringkasan profesional yang jelas, pencapaian kuantitatif, dan kata kunci utama dari profil."
        )
    else:
        recommendations.append(
            "CV Anda sudah cukup selaras dengan profil. Perkuat dengan menyorot hasil konkret dan capaian proyek yang relevan."
        )

    if missing_skills:
        recommendations.append(
            "Tambahkan atau tonjolkan keahlian berikut pada CV Anda: "
            + ", ".join(missing_skills[:8])
        )
    elif matched_skills:
        recommendations.append(
            "Pertahankan keahlian yang sudah cocok dengan profil Anda: "
            + ", ".join(matched_skills[:8])
        )

    if skill_coverage is not None and skill_coverage < 0.5:
        recommendations.append(
            "CV saat ini hanya mencakup sebagian kecil keahlian profil Anda. Pastikan skill penting ditampilkan dalam bagian ringkasan dan pengalaman kerja."
        )

    if len(_normalize_text(cv_text)) > len(_normalize_text(profile_text)) * 1.5:
        recommendations.append(
            "CV Anda tampak lebih panjang dari profil. Fokuskan pada ringkasan pengalaman paling relevan dan hilangkan redundansi."
        )

    if len(_normalize_text(cv_text)) < len(_normalize_text(profile_text)) * 0.5:
        recommendations.append(
            "CV Anda relatif singkat. Tambahkan detail pengalaman, hasil, dan keahlian yang mendukung profil Anda."
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

    model = os.getenv("GROQ_MODEL", "groq-1.0")
    endpoint = os.getenv("GROQ_API_URL", f"https://api.groq.com/v1/models/{model}/outputs")

    prompt = (
        "Anda adalah asisten AI yang membantu memperbaiki CV. "
        "Bandingkan teks CV dengan profil freelancer dan berikan satu saran singkat untuk meningkatkan kecocokan. "
        f"Nilai kesesuaian semantik {similarity:.3f} dan cakupan skill {skill_coverage if skill_coverage is not None else 'n/a'}. "
        "Jika skill profil hilang di CV, sebutkan secara singkat. "
        "Berikan jawaban dalam bahasa Indonesia. "
        "CV:\n" + cv_text[:2000] + "\n\nPROFIL:\n" + profile_text[:2000]
    )

    payload = {
        "input": prompt,
        "max_output_tokens": 120,
    }

    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    if isinstance(data, dict):
        text = data.get("output") or data.get("text") or data.get("response")
        if isinstance(text, list):
            text = "\n".join(str(x) for x in text)
        if isinstance(text, str):
            return text.strip()
    return None
