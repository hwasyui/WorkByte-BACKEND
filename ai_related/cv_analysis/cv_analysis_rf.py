import re
from pathlib import Path
from typing import Any, Dict

import joblib
import numpy as np

from ai_related.job_engine.embedding_service import get_embedding, get_query_embedding

_RF_DIR = Path(__file__).parent / "cv_analysis_rf_models"
_PCA_DIR = _RF_DIR / "cv_embeddings_rf" / "cv_rf_details"

_MATCH_CONFIDENCE_THRESHOLD = 0.60
_ATS_LABEL_MAP = {0: "Low ATS", 1: "Medium ATS", 2: "High ATS"}
_ATS_SCORE_MAP = {"Low ATS": 20, "Medium ATS": 50, "High ATS": 100}
_MATCH_LABEL_MAP = {0: "No Fit", 1: "Fit"}

_CLICHES = [
    "team player", "hard-working", "go-getter", "self-starter",
    "detail-oriented", "results-oriented", "passionate about",
]
_SECTION_KEYWORDS = {
    "has_summary": ["summary", "profile", "about", "objective"],
    "has_skills": ["skills", "expertise", "competencies", "technical"],
    "has_experience": ["experience", "employment", "work history"],
    "has_education": ["education", "academic", "qualification", "degree"],
}

# Module-level singleton, mirrors the loading pattern in embedding_service.py.
_models: Dict[str, Any] = {}


def _load_models() -> Dict[str, Any]:
    if not _models:
        _models["ats_model"] = joblib.load(_RF_DIR / "ats_scorer_rf.pkl")
        _models["match_model"] = joblib.load(_RF_DIR / "match_scorer_rf.pkl")
        _models["section_model"] = joblib.load(_RF_DIR / "section_scorer_rf.pkl")
        _models["ats_feature_names"] = joblib.load(_RF_DIR / "ats_feature_names.pkl")
        _models["section_target_cols"] = joblib.load(_RF_DIR / "section_target_cols.pkl")
        _models["pca_match"] = joblib.load(_PCA_DIR / "pca_match.pkl")
        _models["pca_section"] = joblib.load(_PCA_DIR / "pca_section.pkl")
    return _models


def _extract_ats_features(text: str) -> Dict[str, float]:
    t = text.lower()
    words = text.split()
    wc = len(words)
    feats: Dict[str, float] = {}
    for key, kws in _SECTION_KEYWORDS.items():
        feats[key] = int(any(k in t for k in kws))
    feats["has_email"] = int(bool(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text)))
    feats["has_phone"] = int(bool(re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", text)))
    feats["has_dates"] = int(bool(re.search(r"\b(19|20)\d{2}\b", text)))
    feats["has_metrics"] = int(bool(re.search(
        r"\d+\s*(%|percent|users|clients|revenue|saving|increase|decrease)", t)))
    feats["has_bullets"] = int(bool(re.search(r"^[•\-\*]", text, re.MULTILINE)))
    feats["no_cliches"] = int(not any(c in t for c in _CLICHES))
    feats["word_count"] = wc
    feats["wc_optimal"] = int(300 <= wc <= 800)
    feats["wc_too_short"] = int(wc < 200)
    feats["wc_too_long"] = int(wc > 1200)
    feats["unique_ratio"] = len(set(words)) / max(wc, 1)
    feats["avg_word_len"] = float(np.mean([len(w) for w in words])) if words else 0.0
    return feats


def _extract_explicit_features(resume: str, jd: str) -> np.ndarray:
    r_lower = resume.lower()
    j_lower = jd.lower()
    r_words = set(r_lower.split())
    j_words = set(j_lower.split())
    overlap = len(r_words & j_words) / max(len(j_words), 1)
    len_ratio = min(len(resume), len(jd)) / max(max(len(resume), len(jd)), 1)
    has_email = int(bool(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", resume)))
    has_phone = int(bool(re.search(r"\+?\d[\d\s\-\.\(\)]{6,}\d", resume)))
    has_dates = int(bool(re.search(r"\b(19|20)\d{2}\b", resume)))
    has_skills_kw = int(any(k in r_lower for k in ["skills", "expertise", "proficient"]))
    has_exp_kw = int(any(k in r_lower for k in ["experience", "worked", "developed"]))
    has_edu_kw = int(any(k in r_lower for k in ["education", "degree", "university"]))
    has_metrics = int(bool(re.search(
        r"\d+\s*(%|percent|users|clients|revenue|saving|increase|decrease)", r_lower)))
    r_word_count = min(len(resume.split()) / 1000, 1.0)
    return np.array([overlap, len_ratio, has_email, has_phone, has_dates,
                      has_skills_kw, has_exp_kw, has_edu_kw, has_metrics, r_word_count])


def _build_pair_features(emb_a: np.ndarray, emb_b: np.ndarray) -> np.ndarray:
    cos = float(np.dot(emb_a, emb_b))
    diff = np.abs(emb_a - emb_b)
    prod = emb_a * emb_b
    return np.hstack([[cos], diff, prod])


async def analyze_cv_with_rf(cv_text: str, profile_text: str) -> Dict[str, Any]:
    models = _load_models()

    emb_cv = np.array(await get_embedding(cv_text))
    emb_profile = np.array(await get_query_embedding(profile_text))
    similarity = float(np.dot(emb_cv, emb_profile))

    explicit = _extract_explicit_features(cv_text, profile_text)
    raw_pair = _build_pair_features(emb_cv, emb_profile)

    match_feat = np.hstack(
        [models["pca_match"].transform(raw_pair.reshape(1, -1))[0], explicit]
    ).reshape(1, -1)
    match_proba = models["match_model"].predict_proba(match_feat)[0]
    match_pred = int(match_proba.argmax())
    if match_proba.max() < _MATCH_CONFIDENCE_THRESHOLD:
        match_pred = 0

    section_feat = np.hstack(
        [models["pca_section"].transform(raw_pair.reshape(1, -1))[0], explicit]
    ).reshape(1, -1)
    section_preds = models["section_model"].predict(section_feat)[0]
    section_scores = {
        col.replace("score_", ""): round(float(np.clip(p, 0, 1)) * 100, 1)
        for col, p in zip(models["section_target_cols"], section_preds)
    }

    ats_feats = _extract_ats_features(cv_text)
    ats_x = np.array([[ats_feats[name] for name in models["ats_feature_names"]]])
    ats_proba = models["ats_model"].predict_proba(ats_x)[0]
    ats_classes = models["ats_model"].classes_
    ats_pred = int(ats_classes[int(np.argmax(ats_proba))])
    ats_weighted_score = sum(
        _ATS_SCORE_MAP[_ATS_LABEL_MAP[int(cls)]] * proba
        for cls, proba in zip(ats_classes, ats_proba)
    )

    return {
        "similarity_score": round(similarity, 4),
        "match_label": _MATCH_LABEL_MAP[match_pred],
        "match_confidence": round(float(match_proba.max()), 4),
        "ats_label": _ATS_LABEL_MAP[ats_pred],
        "ats_confidence": round(float(np.max(ats_proba)), 4),
        "ats_score": int(round(ats_weighted_score)),
        "resume_score": int(round(section_scores.get("overall", 0.0))),
        "section_scores": section_scores,
    }
