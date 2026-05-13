import os
import re
import sys

from pydantic import BaseModel
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from functions.authentication import get_freelancer_user
from functions.schema_model import CVUploadRequest, UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_cv_file, guess_mime
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.freelancer_skills.freelancer_skill_functions import FreelancerSkillFunctions
from routes.work_experience.work_experience_functions import WorkExperienceFunctions
from routes.education.education_functions import EducationFunctions
from routes.freelancer_languages.freelancer_language_functions import FreelancerLanguageFunctions
from routes.languages.language_functions import LanguageFunctions
from routes.skills.skill_functions import SkillFunctions
from ai_related.cv_analysis.cv_analysis import (
    build_freelancer_profile_text,
    get_profile_skill_names,
    extract_skills_from_text,
    get_cv_embedding,
    cosine_similarity,
    compute_resume_score,
    compute_overall_score,
    grade_overall_score,
    check_ats_compliance,
    analyze_cv_with_llm,
    parse_cv_for_profile,
)
from .cv_upload_functions import (
    _extract_text_from_pdf,
    _extract_text_from_docx,
    _extract_text_from_image,
)


cv_upload_router = APIRouter(prefix="/cv_upload", tags=["CV Upload"])

_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}


@cv_upload_router.post("")
async def upload_and_analyze_cv(
    request: CVUploadRequest = Depends(),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Upload a CV (PDF or DOCX) and either:
    1. (Initial) Parse CV to suggest profile data if profile is empty
    2. (Update) Compare against existing profile, run ATS check, and provide full analysis
    """
    logger("CV_UPLOAD", f"CV upload from user {current_user.user_id}", level="DEBUG")
    try:
        file = request.file
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="CV file must not be empty")

        logger("CV_UPLOAD", f"File: {file.filename}, size={len(contents)} bytes", level="DEBUG")

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found for current user")

        freelancer_id = freelancer["freelancer_id"]

        original_name = file.filename or "cv"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
        mime = file.content_type or guess_mime(original_name)

        # --- Step 1: Extract raw text ---
        if ext == "docx" or mime in _DOCX_MIMES:
            raw_text = _extract_text_from_docx(contents)
        elif ext == "pdf" or mime == "application/pdf":
            raw_text = _extract_text_from_pdf(contents)
        elif mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "bmp", "tiff"}:
            raw_text = _extract_text_from_image(contents)
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Please upload a PDF or DOCX.",
            )

        if not raw_text:
            raise HTTPException(status_code=422, detail="Unable to extract text from the uploaded CV.")

        logger("CV_UPLOAD", f"Extracted {len(raw_text)} chars from CV", level="DEBUG")

        # --- Step 2: Store file in Supabase ---
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._")
        storage_path = f"cvs/{freelancer_id}/{safe_name}"
        public_url = upload_cv_file(path=storage_path, file_bytes=contents, content_type=mime)
        FreelancerFunctions.update_freelancer(freelancer_id, {"cv_file_url": public_url})
        logger("CV_UPLOAD", f"CV stored: {public_url}", level="DEBUG")

        # --- Step 3: Check if profile is meaningfully empty ---
        has_bio = bool(freelancer.get("bio"))
        has_skills = bool(FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer_id))
        has_work_exp = bool(WorkExperienceFunctions.get_work_experiences_by_freelancer_id(freelancer_id))
        has_education = bool(EducationFunctions.get_educations_by_freelancer_id(freelancer_id))
        is_initial_upload = not (has_bio or has_skills or has_work_exp or has_education)

        # Still build profile_text for the full-analysis path (used for similarity)
        profile_text = build_freelancer_profile_text(freelancer_id) if not is_initial_upload else None

        if is_initial_upload:
            # INITIAL PROFILE CREATION: Profile is empty, parse CV for suggestions
            logger("CV_UPLOAD", f"Initial CV upload detected for freelancer {freelancer_id}. Parsing for profile suggestions.", level="INFO")
            
            # Parse CV for profile suggestions
            parsed_profile = await parse_cv_for_profile(raw_text)
            
            logger(
                "CV_UPLOAD",
                f"Initial CV parsed | freelancer={freelancer_id} | skills={len(parsed_profile.get('skills', []))} "
                f"| experience={len(parsed_profile.get('work_experience', []))} | education={len(parsed_profile.get('education', []))}",
                level="INFO",
            )

            return ResponseSchema.success(
                {
                    "file_url": public_url,
                    "file_name": f"{freelancer_id}.{ext}",
                    "file_type": mime,
                    "is_initial": True,
                    "status": "profile_suggestions_ready",
                    "message": "Profile is empty. CV has been parsed. Please review and confirm the suggested profile data.",
                    "suggested_profile": parsed_profile,
                },
                200,
            )

        # PROFILE UPDATE: Profile exists, run full analysis
        logger("CV_UPLOAD", f"CV update detected for freelancer {freelancer_id}. Running full analysis.", level="INFO")

        # --- Step 4: Embedding similarity ---
        cv_embedding = get_cv_embedding(raw_text)
        profile_embedding = get_cv_embedding(profile_text)
        similarity = cosine_similarity(cv_embedding, profile_embedding)

        # --- Step 5: Skill coverage ---
        profile_skills = get_profile_skill_names(freelancer_id)
        matched_skills = extract_skills_from_text(raw_text, profile_skills)
        missing_skills = [s for s in profile_skills if s not in matched_skills]
        skill_coverage = len(matched_skills) / len(profile_skills) if profile_skills else None

        # --- Step 6: ATS compliance ---
        ats_result = check_ats_compliance(raw_text)

        # --- Step 7: Scoring ---
        resume_score = compute_resume_score(similarity, skill_coverage)

        # --- Step 8: Structured LLM analysis (all content from GROQ) ---
        llm_analysis = await analyze_cv_with_llm(
            cv_text=raw_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            ats_result=ats_result,
        )

        # --- Step 9: Parse CV for profile suggestions ---
        parsed_profile = await parse_cv_for_profile(raw_text)

        final_resume_score = llm_analysis["resume_score"]
        ats_score = ats_result["ats_score"]
        logger("CV_UPLOAD", f"DEBUG: final_resume_score={final_resume_score} (type: {type(final_resume_score).__name__}), ats_score={ats_score} (type: {type(ats_score).__name__})", level="DEBUG")
        
        overall_score = compute_overall_score(final_resume_score, ats_score)
        overall_grade = grade_overall_score(overall_score)

        logger(
            "CV_UPLOAD",
            f"CV analysis complete | freelancer={freelancer_id} | overall={overall_score} ({overall_grade}) "
            f"| similarity={similarity:.3f} | ats={ats_result['ats_score']}",
            level="INFO",
        )

        return ResponseSchema.success(
            {
                "file_url": public_url,
                "file_name": f"{freelancer_id}.{ext}",
                "file_type": mime,
                "is_initial": False,
                "overall_score": overall_score,
                "overall_grade": overall_grade,
                "resume_score": final_resume_score,
                "ats_score": ats_result["ats_score"],
                "ats_flags": ats_result["ats_flags"],
                "similarity_score": round(similarity, 4),
                "skill_coverage": round(skill_coverage, 4) if skill_coverage is not None else None,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "overall_assessment": llm_analysis["overall_assessment"],
                "profile_match_analysis": llm_analysis["profile_match_analysis"],
                "sections": llm_analysis["sections"],
                "suggested_profile": parsed_profile,
            },
            200,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_UPLOAD", f"CV upload/analyze failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"CV upload/analyze failed: {str(e)}", 500)


# ── Request body models ───────────────────────────────────────────────────────

class _SuggestedWorkExp(BaseModel):
    job_title: str
    company_name: str
    location: Optional[str] = None
    start_date: str
    end_date: Optional[str] = None
    is_current: Optional[bool] = False
    description: Optional[str] = None


class _SuggestedEducation(BaseModel):
    institution_name: str
    degree: str
    field_of_study: Optional[str] = None
    start_date: str
    end_date: Optional[str] = None
    is_current: Optional[bool] = False
    grade: Optional[str] = None


class _SuggestedLanguage(BaseModel):
    name: str
    proficiency: str


class CVApplyRequest(BaseModel):
    apply_bio: bool = True
    apply_skills: bool = True
    apply_work_experience: bool = True
    apply_education: bool = True
    apply_languages: bool = True
    suggested_bio: Optional[str] = None
    skills: Optional[List[str]] = None
    work_experience: Optional[List[_SuggestedWorkExp]] = None
    education: Optional[List[_SuggestedEducation]] = None
    languages: Optional[List[_SuggestedLanguage]] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(date_str: Optional[str]):
    from datetime import date
    if not date_str:
        return None
    try:
        if len(date_str) == 4:
            return date(int(date_str), 1, 1)
        if len(date_str) == 7:
            y, m = date_str.split("-")
            return date(int(y), int(m), 1)
        return date.fromisoformat(date_str[:10])
    except Exception:
        return None


def _resolve_or_create_skill(skill_name: str) -> Optional[str]:
    existing = SkillFunctions.get_skill_by_name(skill_name)
    if existing:
        return existing["skill_id"]
    try:
        new_skill = SkillFunctions.create_skill(
            skill_name=skill_name, skill_category="hard_skill"
        )
        return new_skill["skill_id"]
    except ValueError:
        existing = SkillFunctions.get_skill_by_name(skill_name)
        return existing["skill_id"] if existing else None


def _resolve_or_create_language(language_name: str) -> Optional[str]:
    existing = LanguageFunctions.get_language_by_name(language_name)
    if existing:
        return existing["language_id"]
    try:
        new_lang = LanguageFunctions.create_language(language_name=language_name)
        return new_lang["language_id"]
    except Exception:
        existing = LanguageFunctions.get_language_by_name(language_name)
        return existing["language_id"] if existing else None


# ── Apply route ───────────────────────────────────────────────────────────────

@cv_upload_router.post("/apply")
async def apply_cv_profile(
    payload: CVApplyRequest,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """Apply confirmed CV suggestions to the freelancer's profile."""
    logger("CV_APPLY", f"Apply request from user {current_user.user_id}", level="DEBUG")
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found")

        freelancer_id = freelancer["freelancer_id"]
        summary = {
            "bio_updated": False,
            "skills_added": [],
            "skills_skipped": [],
            "work_experience_added": 0,
            "education_added": 0,
            "languages_added": [],
            "languages_skipped": [],
        }

        # 1. Bio
        if payload.apply_bio and payload.suggested_bio:
            FreelancerFunctions.update_freelancer(
                freelancer_id, {"bio": payload.suggested_bio}
            )
            summary["bio_updated"] = True

        # 2. Skills
        if payload.apply_skills and payload.skills:
            existing_ids = {
                s["skill_id"]
                for s in FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer_id)
            }
            for name in payload.skills:
                sid = _resolve_or_create_skill(name)
                if not sid or sid in existing_ids:
                    summary["skills_skipped"].append(name)
                    continue
                FreelancerSkillFunctions.create_freelancer_skill(
                    freelancer_id=freelancer_id,
                    skill_id=sid,
                    proficiency_level="intermediate",
                )
                summary["skills_added"].append(name)
                existing_ids.add(sid)

        # 3. Work Experience
        if payload.apply_work_experience and payload.work_experience:
            for exp in payload.work_experience:
                start = _parse_date(exp.start_date)
                if not start:
                    continue
                WorkExperienceFunctions.create_work_experience(
                    freelancer_id=freelancer_id,
                    job_title=exp.job_title,
                    company_name=exp.company_name,
                    location=exp.location,
                    start_date=start,
                    end_date=_parse_date(exp.end_date),
                    is_current=exp.is_current or False,
                    description=exp.description,
                )
                summary["work_experience_added"] += 1

        # 4. Education
        if payload.apply_education and payload.education:
            for edu in payload.education:
                start = _parse_date(edu.start_date)
                if not start:
                    continue
                EducationFunctions.create_education(
                    freelancer_id=freelancer_id,
                    institution_name=edu.institution_name,
                    degree=edu.degree,
                    field_of_study=edu.field_of_study,
                    start_date=start,
                    end_date=_parse_date(edu.end_date),
                    is_current=edu.is_current or False,
                    grade=edu.grade,
                )
                summary["education_added"] += 1

        # 5. Languages
        if payload.apply_languages and payload.languages:
            existing_lang_ids = {
                l["language_id"]
                for l in FreelancerLanguageFunctions.get_freelancer_languages_by_freelancer_id(freelancer_id)
            }
            valid = {"basic", "conversational", "fluent", "native"}
            for lang in payload.languages:
                lid = _resolve_or_create_language(lang.name)
                if not lid or lid in existing_lang_ids:
                    summary["languages_skipped"].append(lang.name)
                    continue
                FreelancerLanguageFunctions.create_freelancer_language(
                    freelancer_id=freelancer_id,
                    language_id=lid,
                    proficiency_level=lang.proficiency if lang.proficiency in valid else "conversational",
                )
                summary["languages_added"].append(lang.name)
                existing_lang_ids.add(lid)

        logger("CV_APPLY", f"Apply complete | freelancer={freelancer_id} | summary={summary}", level="INFO")
        return ResponseSchema.success(
            {"message": "Profile updated successfully from CV", "summary": summary}, 200
        )

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_APPLY", f"CV apply failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"CV apply failed: {str(e)}")