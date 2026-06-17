import os
import re
import torch
from typing import Dict, List, Optional, Tuple

from transformers import AutoModelForTokenClassification, AutoTokenizer

from functions.logger import logger

MODEL_DIR = os.path.join(os.path.dirname(__file__), "cv_parser_roberta")

_model = None
_tokenizer = None
_device = None


def _load_model():
    global _model, _tokenizer, _device
    if _model is None:
        logger("CV_PARSER_ROBERTA", "Loading RoBERTa CV parser model", level="INFO")
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR, local_files_only=True)
        _model = AutoModelForTokenClassification.from_pretrained(
            MODEL_DIR, local_files_only=True
        ).to(_device)
        _model.eval()
        logger("CV_PARSER_ROBERTA", f"RoBERTa CV parser model loaded on {_device}", level="INFO")
    return _model, _tokenizer, _device


def _extract_spans(words: List[str], word_labels: List[str]) -> List[Tuple[str, str]]:
    """
    Convert per-word BIO labels into (entity_type, text) spans.
    B- always starts a new span. I- continues the current span if the type matches,
    otherwise starts a new one.
    """
    spans: List[Tuple[str, str]] = []
    current_type: Optional[str] = None
    current_words: List[str] = []

    for word, label in zip(words, word_labels):
        if label == "O":
            if current_type:
                spans.append((current_type, " ".join(current_words)))
                current_type = None
                current_words = []
        elif label.startswith("B-"):
            if current_type:
                spans.append((current_type, " ".join(current_words)))
            current_type = label[2:]
            current_words = [word]
        elif label.startswith("I-"):
            inner = label[2:]
            if current_type == inner:
                current_words.append(word)
            else:
                if current_type:
                    spans.append((current_type, " ".join(current_words)))
                current_type = inner
                current_words = [word]

    if current_type:
        spans.append((current_type, " ".join(current_words)))

    return spans


_MONTH_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}
_MONTH_PATTERN = "|".join(_MONTH_MAP)


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    """Normalize a raw date string extracted by the model to YYYY-MM or YYYY."""
    if not raw:
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:7]
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}", raw):
        return raw
    m = re.search(rf"({_MONTH_PATTERN})\.?\s+(\d{{4}})", raw.lower())
    if m:
        return f"{m.group(2)}-{_MONTH_MAP[m.group(1)]}"
    m = re.search(r"\b(\d{4})\b", raw)
    if m:
        return m.group(1)
    return None


def _is_present(raw: Optional[str]) -> bool:
    if not raw:
        return False
    return bool(re.search(r"\b(present|current|now|sekarang|ongoing)\b", raw.lower()))


_EXP_ANCHORS = {"EXP_JOB_TITLE", "EXP_COMPANY"}
_EDU_ANCHORS = {"EDU_SCHOOL", "EDU_DEGREE"}
_PROJ_ANCHORS = {"PROJ_TITLE"}

_EXP_FIELD_MAP = {
    "EXP_JOB_TITLE": "job_title",
    "EXP_COMPANY": "company_name",
    "EXP_LOCATION": "location",
    "EXP_START_DATE": "start_date",
    "EXP_END_DATE": "end_date",
    "EXP_DESCRIPTION": "description",
}
_EDU_FIELD_MAP = {
    "EDU_SCHOOL": "institution_name",
    "EDU_DEGREE": "degree",
    "EDU_FIELD": "field_of_study",
    "EDU_START_DATE": "start_date",
    "EDU_END_DATE": "end_date",
    "EDU_GRADE": "grade",
    "EDU_DESCRIPTION": "description",
}
_PROJ_FIELD_MAP = {
    "PROJ_TITLE": "title",
    "PROJ_DESCRIPTION": "description",
    "PROJ_ROLE": "role",
    "PROJ_URL": "url",
    "PROJ_DATE": "date",
}


def _group_spans(spans: List[Tuple[str, str]]) -> Dict:
    """Group flat entity spans into structured CV sections."""
    sections: Dict = {
        "about": [],
        "name": [],
        "contact": [],
        "skills": [],
        "work_experience_raw": [],
        "education_raw": [],
        "portfolio_raw": [],
    }

    current_exp: Dict = {}
    current_edu: Dict = {}
    current_proj: Dict = {}

    def _flush(container, current):
        if current:
            container.append(dict(current))
            current.clear()

    for entity_type, text in spans:
        if entity_type == "ABOUT":
            sections["about"].append(text)
        elif entity_type == "NAME":
            sections["name"].append(text)
        elif entity_type == "CONTACT":
            sections["contact"].append(text)
        elif entity_type == "SKILLS":
            sections["skills"].append(text)
        elif entity_type in _EXP_FIELD_MAP:
            if entity_type in _EXP_ANCHORS and any(k in current_exp for k in ("job_title", "company_name")):
                _flush(sections["work_experience_raw"], current_exp)
            current_exp[_EXP_FIELD_MAP[entity_type]] = text
        elif entity_type in _EDU_FIELD_MAP:
            if entity_type in _EDU_ANCHORS and any(k in current_edu for k in ("institution_name", "degree")):
                _flush(sections["education_raw"], current_edu)
            current_edu[_EDU_FIELD_MAP[entity_type]] = text
        elif entity_type in _PROJ_FIELD_MAP:
            if entity_type in _PROJ_ANCHORS and current_proj:
                _flush(sections["portfolio_raw"], current_proj)
            current_proj[_PROJ_FIELD_MAP[entity_type]] = text

    _flush(sections["work_experience_raw"], current_exp)
    _flush(sections["education_raw"], current_edu)
    _flush(sections["portfolio_raw"], current_proj)

    return sections


def _build_work_experience(raw: Dict) -> Dict:
    raw_end = raw.get("end_date", "")
    is_current = _is_present(raw_end) or not raw_end
    return {
        "job_title": raw.get("job_title", ""),
        "company_name": raw.get("company_name", ""),
        "location": raw.get("location") or None,
        "start_date": _normalize_date(raw.get("start_date")),
        "end_date": None if is_current else _normalize_date(raw_end),
        "is_current": is_current,
        "description": raw.get("description") or None,
    }


def _build_education(raw: Dict) -> Dict:
    raw_end = raw.get("end_date", "")
    is_current = _is_present(raw_end) or not raw_end
    return {
        "institution_name": raw.get("institution_name", ""),
        "degree": raw.get("degree", ""),
        "field_of_study": raw.get("field_of_study") or None,
        "start_date": _normalize_date(raw.get("start_date")),
        "end_date": None if is_current else _normalize_date(raw_end),
        "is_current": is_current,
        "grade": raw.get("grade") or None,
    }


def parse_cv_with_roberta(cv_text: str) -> Dict:
    """
    Parse CV text using the local RoBERTa NER model.
    Returns a dict with: suggested_bio, skills, languages, work_experience, education.
    """
    model, tokenizer, device = _load_model()
    confidence_threshold = getattr(model.config, "confidence_threshold", 0.6)

    words = cv_text.split()
    if not words:
        return {"suggested_bio": "", "skills": [], "languages": [], "work_experience": [], "education": []}

    CHUNK_WORDS = 256
    word_labels: List[str] = ["O"] * len(words)
    word_confs: List[float] = [0.0] * len(words)

    for chunk_start in range(0, len(words), CHUNK_WORDS):
        chunk_words = words[chunk_start: chunk_start + CHUNK_WORDS]

        batch = tokenizer(
            chunk_words,
            is_split_into_words=True,
            truncation=True,
            max_length=512,
            padding=False,
        )
        word_ids_list = batch.word_ids()
        inputs = {k: torch.tensor([v]).to(device) for k, v in batch.items()}

        with torch.no_grad():
            outputs = model(**inputs)

        probs = torch.softmax(outputs.logits[0], dim=-1)
        pred_ids = torch.argmax(probs, dim=-1).tolist()
        confs = torch.max(probs, dim=-1).values.tolist()

        seen: set = set()
        for token_idx, word_id in enumerate(word_ids_list):
            if word_id is None or word_id in seen:
                continue
            seen.add(word_id)
            global_idx = chunk_start + word_id
            if global_idx >= len(words):
                continue
            conf = confs[token_idx]
            if conf > word_confs[global_idx]:
                word_labels[global_idx] = model.config.id2label[pred_ids[token_idx]]
                word_confs[global_idx] = conf

    # Low-confidence predictions fall back to O
    for i in range(len(word_labels)):
        if word_labels[i] != "O" and word_confs[i] < confidence_threshold:
            word_labels[i] = "O"

    spans = _extract_spans(words, word_labels)
    logger("CV_PARSER_ROBERTA", f"Extracted {len(spans)} entity spans", level="DEBUG")

    sections = _group_spans(spans)

    # Skills: split raw skills text on common delimiters
    raw_skills_text = " , ".join(sections["skills"])
    skills = [
        s.strip() for s in re.split(r"[,\n\r•‣◦|/\\]+", raw_skills_text)
        if s.strip() and 1 < len(s.strip()) < 60
    ]

    # Bio: join all ABOUT spans
    suggested_bio = " ".join(sections["about"]).strip()

    # Work experience: filter out empty records
    work_experience = [
        _build_work_experience(e) for e in sections["work_experience_raw"]
        if e.get("job_title") or e.get("company_name")
    ]

    # Education: filter out empty records
    education = [
        _build_education(e) for e in sections["education_raw"]
        if e.get("institution_name") or e.get("degree")
    ]

    logger(
        "CV_PARSER_ROBERTA",
        f"Parsed CV | skills={len(skills)} | work_experience={len(work_experience)} | education={len(education)}",
        level="INFO",
    )

    return {
        "suggested_bio": suggested_bio,
        "skills": skills,
        "languages": [],
        "work_experience": work_experience,
        "education": education,
    }
