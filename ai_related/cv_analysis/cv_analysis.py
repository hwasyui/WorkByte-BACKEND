import asyncio
import re
import math
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
import numpy as np
import joblib
from fastapi import UploadFile
from groq import Groq
from sentence_transformers import SentenceTransformer
from functions.db_manager import get_db
from functions.logger import logger
from ai_related.job_engine.source_text_builder import build_freelancer_source_text
from ai_related.job_engine.embedding_service import _get_model


def get_cv_embedding(text: str) -> List[float]:
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


async def extract_cv_text(cv_file: UploadFile) -> str:
    """Returns "" when extraction yields no text; raises ValueError for empty
    files or unsupported types."""
    contents = await cv_file.read()
    if not contents:
        raise ValueError("CV file is empty")

    file_name = (cv_file.filename or "").lower()
    content_type = cv_file.content_type or ""

    if content_type.startswith("text/") or file_name.endswith(".txt"):
        return contents.decode("utf-8", errors="replace")

    # extraction lives in cv_upload; reused here rather than duplicated
    from routes.cv_upload.cv_upload_functions import _extract_text_from_pdf, _extract_text_from_docx

    if file_name.endswith(".pdf") or content_type == "application/pdf":
        return await asyncio.to_thread(_extract_text_from_pdf, contents)

    if file_name.endswith(".docx") or content_type in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ):
        try:
            return await asyncio.to_thread(_extract_text_from_docx, contents)
        except RuntimeError:
            return ""

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
    """Rule-based ATS checklist. Returns ats_flags (human-readable issues) plus a
    legacy ats_score — the score is no longer used for the response, only ats_flags
    (numeric ATS scoring now comes from predict_ats(), see below)."""
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

    if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", raw_text):
        score += 8
    else:
        flags.append("No email address found")

    if re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", raw_text):
        score += 7
    else:
        flags.append("No phone number found")

    if 300 <= word_count <= 800:
        score += 15
    elif 200 <= word_count < 300:
        score += 8
        flags.append("CV is too short. Aim for at least 300 words.")
    elif 800 < word_count <= 1200:
        score += 8
        flags.append("CV is quite long. Aim for under 800 words for ATS readability.")
    else:
        if word_count < 200:
            flags.append("CV is very short. Add more detail on skills, experience, and education.")
        else:
            flags.append("CV is very long. Condense to the most relevant experience.")

    if re.search(r"\d+\s*(%|percent|users|clients|projects|increase|decrease|revenue|saving)", text_lower):
        score += 10
    else:
        flags.append("No quantifiable achievements found. Add numbers, percentages, or metrics to your experience.")

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

    if re.search(r"\b(19|20)\d{2}\b", raw_text):
        score += 10
    else:
        flags.append("No dates found in work experience. Include start/end years for each role.")

    return {"ats_score": score, "ats_flags": flags}


# ─────────────────────────────────────────────────────────────────────────
# XGBoost scoring models (match / ATS tier / section scores).
# Trained offline — see cv_analysis_xgb_models/xgboost_cv_analysis_final.ipynb.
# All numeric CV scoring comes from these models; the LLM (below) only writes
# the narrative assessment and recommendations grounded in these numbers.
# ─────────────────────────────────────────────────────────────────────────

_XGB_MODELS_DIR = Path(__file__).parent / "cv_analysis_xgb_models"
_XGB_PCA_DIR = _XGB_MODELS_DIR / "model_pkl"

# The match/section models were trained against nomic-embed-text-v1 (NOT v1.5,
# which the rest of the backend uses for RAG job matching) — a different
# encoder here would silently feed the PCA/XGBoost models out-of-distribution
# vectors, so this is a dedicated singleton, separate from job_engine's model.
_MATCH_ENCODER_NAME = "nomic-ai/nomic-embed-text-v1"
_MATCH_CONFIDENCE_THRESHOLD = 0.60

_ATS_TIER_SCORE = {"High ATS": 100, "Medium ATS": 50, "Low ATS": 20}
_WEIGHT_SECTION_OVERALL = 0.75
_WEIGHT_ATS = 0.25

_match_encoder: Optional[SentenceTransformer] = None
_match_model = None
_ats_model = None
_section_model = None
_pca_match = None
_pca_section = None
_ats_feature_names: Optional[List[str]] = None
_ats_label_map: Optional[Dict[int, str]] = None
_section_target_cols: Optional[List[str]] = None


def _get_match_encoder() -> SentenceTransformer:
    global _match_encoder
    if _match_encoder is None:
        logger("CV_ANALYSIS", f"Loading {_MATCH_ENCODER_NAME} for XGB scoring ...", level="INFO")
        _match_encoder = SentenceTransformer(_MATCH_ENCODER_NAME, trust_remote_code=True)
        logger("CV_ANALYSIS", f"{_MATCH_ENCODER_NAME} loaded", level="INFO")
    return _match_encoder


def _load_xgb_models() -> None:
    global _match_model, _ats_model, _section_model, _pca_match, _pca_section
    global _ats_feature_names, _ats_label_map, _section_target_cols
    if _match_model is not None:
        return
    logger("CV_ANALYSIS", "Loading CV-analysis XGBoost models ...", level="INFO")
    _match_model         = joblib.load(_XGB_MODELS_DIR / "match_scorer_xgb.pkl")
    _ats_model           = joblib.load(_XGB_MODELS_DIR / "ats_scorer_xgb.pkl")
    _section_model       = joblib.load(_XGB_MODELS_DIR / "section_scorer_xgb.pkl")
    _pca_match           = joblib.load(_XGB_PCA_DIR / "pca_match.pkl")
    _pca_section         = joblib.load(_XGB_PCA_DIR / "pca_section.pkl")
    _ats_feature_names   = joblib.load(_XGB_MODELS_DIR / "ats_feature_names.pkl")
    _ats_label_map       = joblib.load(_XGB_MODELS_DIR / "ats_label_map.pkl")
    _section_target_cols = joblib.load(_XGB_MODELS_DIR / "section_target_cols.pkl")
    logger("CV_ANALYSIS", "CV-analysis XGBoost models loaded", level="INFO")


# Feature recipes below are copied verbatim from xgboost_cv_analysis_final.ipynb —
# they must stay byte-for-byte identical to what the models were trained on.

_ATS_SECTION_KEYWORDS = {
    "has_summary":    ["summary", "profile", "about", "objective"],
    "has_skills":     ["skills", "expertise", "competencies", "technical"],
    "has_experience": ["experience", "employment", "work history"],
    "has_education":  ["education", "academic", "qualification", "degree"],
}
_ATS_CLICHES = [
    "team player", "hard-working", "go-getter", "self-starter",
    "detail-oriented", "results-oriented", "passionate about",
]


def _extract_ats_features(text: str) -> Dict[str, float]:
    t     = text.lower()
    words = text.split()
    wc    = len(words)
    feats: Dict[str, float] = {}
    for key, kws in _ATS_SECTION_KEYWORDS.items():
        feats[key] = int(any(k in t for k in kws))
    feats["has_email"]    = int(bool(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text)))
    feats["has_phone"]    = int(bool(re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", text)))
    feats["has_dates"]    = int(bool(re.search(r"\b(19|20)\d{2}\b", text)))
    feats["has_metrics"]  = int(bool(re.search(
        r"\d+\s*(%|percent|users|clients|revenue|saving|increase|decrease)", t)))
    feats["has_bullets"]  = int(bool(re.search(r"^[•\-\*]", text, re.MULTILINE)))
    feats["no_cliches"]   = int(not any(c in t for c in _ATS_CLICHES))
    feats["word_count"]   = wc
    feats["wc_optimal"]   = int(300 <= wc <= 800)
    feats["wc_too_short"] = int(wc < 200)
    feats["wc_too_long"]  = int(wc > 1200)
    feats["unique_ratio"] = len(set(words)) / max(wc, 1)
    feats["avg_word_len"] = float(np.mean([len(w) for w in words])) if words else 0.0
    return feats


def _extract_explicit_features(resume: str, jd: str) -> np.ndarray:
    r_lower = resume.lower()
    j_lower = jd.lower()
    r_words = set(r_lower.split())
    j_words = set(j_lower.split())
    overlap       = len(r_words & j_words) / max(len(j_words), 1)
    len_ratio     = min(len(resume), len(jd)) / max(max(len(resume), len(jd)), 1)
    has_email     = int(bool(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", resume)))
    has_phone     = int(bool(re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", resume)))
    has_dates     = int(bool(re.search(r"\b(19|20)\d{2}\b", resume)))
    has_skills_kw = int(any(k in r_lower for k in ["skills", "expertise", "proficient"]))
    has_exp_kw    = int(any(k in r_lower for k in ["experience", "worked", "developed"]))
    has_edu_kw    = int(any(k in r_lower for k in ["education", "degree", "university"]))
    has_metrics   = int(bool(re.search(
        r"\d+\s*(%|percent|users|clients|revenue|saving|increase|decrease)", r_lower)))
    r_word_count  = min(len(resume.split()) / 1000, 1.0)
    return np.array([overlap, len_ratio, has_email, has_phone, has_dates,
                     has_skills_kw, has_exp_kw, has_edu_kw, has_metrics, r_word_count])


def _build_pair_features(emb_a: np.ndarray, emb_b: np.ndarray) -> np.ndarray:
    return np.hstack([[np.dot(emb_a, emb_b)], np.abs(emb_a - emb_b), emb_a * emb_b])


def predict_match(cv_text: str, profile_text: str) -> Dict[str, Any]:
    """Fit / No Fit classification, via match_scorer_xgb.pkl."""
    _load_xgb_models()
    encoder  = _get_match_encoder()
    emb_cv   = encoder.encode(f"search_document: {cv_text}", normalize_embeddings=True)
    emb_job  = encoder.encode(f"search_query: {profile_text}", normalize_embeddings=True)
    explicit = _extract_explicit_features(cv_text, profile_text)
    raw      = _build_pair_features(emb_cv, emb_job)
    feat     = np.hstack([_pca_match.transform(raw.reshape(1, -1))[0], explicit]).reshape(1, -1)

    proba = _match_model.predict_proba(feat)[0]
    pred  = int(proba.argmax())
    if proba.max() < _MATCH_CONFIDENCE_THRESHOLD:
        pred = 0
    return {
        "match_label":      "Fit" if pred == 1 else "No Fit",
        "match_confidence": round(float(proba.max()), 4),
        "match_score":      round(float(np.dot(emb_cv, emb_job)), 4),
    }


def predict_ats(cv_text: str) -> Dict[str, Any]:
    """Low / Medium / High ATS tier classification, via ats_scorer_xgb.pkl."""
    _load_xgb_models()
    feats = _extract_ats_features(cv_text)
    x     = np.array([[feats[name] for name in _ats_feature_names]])
    pred  = int(_ats_model.predict(x)[0])
    proba = _ats_model.predict_proba(x)[0]
    return {
        "ats_label":      _ats_label_map[pred],
        "ats_confidence": round(float(proba.max()), 4),
    }


def predict_sections(cv_text: str, profile_text: str) -> Dict[str, float]:
    """Experience / Skills / Education / Overall 0-100 scores, via section_scorer_xgb.pkl."""
    _load_xgb_models()
    encoder  = _get_match_encoder()
    emb_cv   = encoder.encode(f"search_document: {cv_text}", normalize_embeddings=True)
    emb_job  = encoder.encode(f"search_query: {profile_text}", normalize_embeddings=True)
    explicit = _extract_explicit_features(cv_text, profile_text)
    raw      = _build_pair_features(emb_cv, emb_job)
    feat     = np.hstack([_pca_section.transform(raw.reshape(1, -1))[0], explicit]).reshape(1, -1)

    preds = _section_model.predict(feat)[0]
    return {
        col.replace("score_", ""): round(float(np.clip(p, 0, 1)) * 100, 1)
        for col, p in zip(_section_target_cols, preds)
    }


def ats_label_to_score(ats_label: str) -> int:
    """Map the ATS classifier's categorical tier to a 0-100 number, for API display."""
    return _ATS_TIER_SCORE.get(ats_label, 20)


def compute_overall_score(section_overall_pct: float, ats_label: str) -> int:
    """Overall score = 75% section-model overall score + 25% ATS-tier score."""
    ats_tier_score = ats_label_to_score(ats_label)
    result = int(round(section_overall_pct * _WEIGHT_SECTION_OVERALL + ats_tier_score * _WEIGHT_ATS))
    logger(
        "CV_ANALYSIS",
        f"compute_overall_score(section_overall={section_overall_pct}, ats_label={ats_label}) = {result}",
        level="DEBUG",
    )
    return result


def _call_groq(system_prompt: str, user_prompt: str, json_mode: bool = False, max_tokens: int = 1500) -> Any:
    """Call GROQ LLM and return the response. Retries once on a JSON-schema
    failure — gpt-oss-20b occasionally produces malformed nested JSON, and a
    resample on the same prompt usually succeeds since generation isn't
    deterministic (temperature=0.3)."""
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

    attempts = 2 if json_mode else 1
    for attempt in range(attempts):
        try:
            completion = client.chat.completions.create(**kwargs)
            content = completion.choices[0].message.content.strip()
            return json.loads(content) if json_mode else content
        except Exception as e:
            if attempt < attempts - 1:
                logger("CV_ANALYSIS", f"GROQ call failed (attempt {attempt + 1}/{attempts}), retrying: {e}", level="WARNING")
                continue
            raise


async def analyze_cv_with_llm(
    cv_text: str,
    profile_text: str,
    similarity: float,
    skill_coverage: Optional[float],
    matched_skills: List[str],
    missing_skills: List[str],
    ats_result: Dict[str, Any],
    model_scores: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Narrative CV analysis via GROQ. All numeric scoring is already final by the
    time this runs (predict_match / predict_ats / predict_sections) — the LLM
    only writes assessment text and recommendations grounded in those numbers,
    it never invents its own score.
    """
    coverage_str = f"{skill_coverage:.0%}" if skill_coverage is not None else "N/A"
    matched_str = ", ".join(matched_skills[:10]) if matched_skills else "none"
    missing_str = ", ".join(missing_skills[:10]) if missing_skills else "none"
    ats_flags_str = "\n".join(f"- {f}" for f in ats_result.get("ats_flags", [])) or "None"
    section_scores = model_scores.get("section_scores", {})

    system = (
        "You are an expert CV reviewer and career coach. "
        "Write an analysis grounded in the scores a trained model has already computed — "
        "you never invent or restate a numeric score yourself. Return valid JSON only. "
        "Do not include markdown fences, explanations, or any text outside the JSON."
    )

    schema_example = {
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
        f"=== MODEL-COMPUTED SCORES (already final — do not recompute or contradict) ===\n"
        f"Match: {model_scores.get('match_label')} (confidence {model_scores.get('match_confidence', 0):.0%})\n"
        f"ATS Tier: {model_scores.get('ats_label')} (confidence {model_scores.get('ats_confidence', 0):.0%})\n"
        f"Section Scores — Experience: {section_scores.get('experience', 0):.0f}/100, "
        f"Skills: {section_scores.get('skills', 0):.0f}/100, "
        f"Education: {section_scores.get('education', 0):.0f}/100, "
        f"Overall: {section_scores.get('overall', 0):.0f}/100\n\n"
        f"=== ADDITIONAL METRICS ===\n"
        f"Profile-CV Similarity: {similarity:.2f} ({similarity * 100:.1f}%)\n"
        f"Skill Coverage: {coverage_str}\n"
        f"Skills Matched: {matched_str}\n"
        f"Skills Missing from CV: {missing_str}\n"
        f"ATS Issues Found:\n{ats_flags_str}\n\n"
        f"=== FREELANCER PROFILE ===\n{profile_text[:2500]}\n\n"
        f"=== CV CONTENT ===\n{cv_text[:2500]}\n\n"
        "Write an analysis and return exactly one JSON object matching this structure:\n"
        f"{json.dumps(schema_example, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- Do NOT output resume_score or any other numeric score — scoring is already handled\n"
        "- All text must reference specific details from THIS CV and THIS profile, never generic advice\n"
        "- All text must stay consistent with the model-computed scores above "
        "(e.g. never call it a strong match if Match is 'No Fit')\n"
        "- sections must contain exactly these four titles in order: "
        "'Skills Analysis', 'Work Experience', 'Education', 'ATS Optimization'\n"
        "- Each section must have 2-4 specific, actionable recommendations\n"
        "- Return only the JSON object, nothing else"
    )

    try:
        result = await asyncio.to_thread(_call_groq, system, user, json_mode=True, max_tokens=2500)
        if not isinstance(result, dict):
            raise ValueError(f"LLM returned {type(result).__name__} instead of dict")
        sections = result.get("sections", [])
        if not isinstance(sections, list):
            sections = []
        return {
            "overall_assessment": str(result.get("overall_assessment", "")),
            "profile_match_analysis": str(result.get("profile_match_analysis", "")),
            "sections": sections,
        }
    except Exception as e:
        logger("CV_ANALYSIS", f"LLM structured analysis failed: {e}", level="ERROR")
        return {
            "overall_assessment": "",
            "profile_match_analysis": "",
            "sections": [],
        }


async def parse_cv_for_profile(cv_text: str) -> Dict[str, Any]:
    """
    Hybrid CV parser: RoBERTa NER extracts bio/name/contact, GROQ extracts
    structured fields (skills, work_experience, education, languages).
    """
    # Step 1: RoBERTa for bio and contact entities
    suggested_bio = ""
    try:
        from routes.cv_upload.cv_parser_roberta import parse_cv_with_roberta
        roberta_result = await asyncio.to_thread(parse_cv_with_roberta, cv_text)
        suggested_bio = roberta_result.get("suggested_bio", "")
        logger(
            "CV_ANALYSIS",
            f"RoBERTa extracted bio={'yes' if suggested_bio else 'no'} "
            f"| roberta_skills={len(roberta_result.get('skills', []))} "
            f"| roberta_exp={len(roberta_result.get('work_experience', []))}",
            level="INFO",
        )
    except Exception as e:
        logger("CV_ANALYSIS", f"RoBERTa extraction failed: {e}", level="WARNING")

    # Step 2: GROQ for structured fields
    schema_example = {
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

    system = (
        "You are an expert at parsing CVs into structured data. "
        "Extract the requested fields and return valid JSON only. "
        "Do not include markdown fences, explanations, or any text outside the JSON."
    )

    user = (
        f"=== CV TEXT ===\n{cv_text[:5000]}\n\n"
        "Extract the following fields from this CV and return exactly one JSON object:\n"
        f"{json.dumps(schema_example, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- skills: all technical and relevant soft skills mentioned\n"
        "- languages proficiency must be one of: basic, conversational, fluent, native\n"
        "- dates: use YYYY-MM format when month is known, YYYY when only year is known\n"
        "- is_current: true only if the role or education is ongoing\n"
        "- Do not invent information not present in the CV\n"
        "- Use empty string or empty list when a field cannot be found\n"
        "- Return only the JSON object, nothing else"
    )

    groq_result: Dict[str, Any] = {}
    try:
        groq_result = await asyncio.to_thread(_call_groq, system, user, json_mode=True, max_tokens=3000)
        logger(
            "CV_ANALYSIS",
            f"GROQ extracted skills={len(groq_result.get('skills', []))} "
            f"| exp={len(groq_result.get('work_experience', []))} "
            f"| edu={len(groq_result.get('education', []))}",
            level="INFO",
        )
    except Exception as e:
        logger("CV_ANALYSIS", f"GROQ structured extraction failed: {e}", level="ERROR")

    # Merge: bio from RoBERTa (fallback to GROQ if empty), rest from GROQ
    bio = suggested_bio or groq_result.get("suggested_bio", "")

    return {
        "suggested_bio": bio,
        "skills": groq_result.get("skills", []),
        "languages": groq_result.get("languages", []),
        "work_experience": groq_result.get("work_experience", []),
        "education": groq_result.get("education", []),
    }


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
    match_result = await asyncio.to_thread(predict_match, cv_text, profile_text)
    ats_ml_result = await asyncio.to_thread(predict_ats, cv_text)
    section_scores = await asyncio.to_thread(predict_sections, cv_text, profile_text)
    model_scores = {
        **match_result,
        **ats_ml_result,
        "section_scores": section_scores,
    }
    analysis = await analyze_cv_with_llm(
        cv_text, profile_text, similarity, skill_coverage,
        matched_skills, missing_skills, ats_result, model_scores,
    )
    recs: List[str] = []
    for section in analysis.get("sections", []):
        recs.extend(section.get("recommendations", []))
    return recs
