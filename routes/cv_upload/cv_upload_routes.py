import asyncio
import os
import re
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional

from functions.authentication import get_freelancer_user
from functions.schema_model import CVUploadRequest, CVApplyRequest, UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.minio_client import upload_cv_file, guess_mime, validate_upload
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.freelancers.freelancer_routes import _scan_identity_fields_or_reject
from routes.admin.admin_moderation import scan_harmful_text
from routes.skills.skill_functions import SkillFunctions
from routes.freelancer_skills.freelancer_skill_functions import FreelancerSkillFunctions
from routes.work_experience.work_experience_functions import WorkExperienceFunctions
from routes.education.education_functions import EducationFunctions
from ai_related.cv_analysis.cv_analysis import (
    is_cv_text_too_sparse,
    CV_EXTRACTION_FAILED_MESSAGE,
    build_freelancer_profile_text,
    get_profile_skill_names,
    extract_skills_from_text,
    get_cv_embedding,
    cosine_similarity,
    compute_overall_score,
    grade_overall_score,
    check_ats_compliance,
    analyze_cv_with_llm,
    parse_cv_for_profile,
    predict_match,
    predict_ats,
    predict_sections,
    ats_label_to_score,
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

_SKILL_LABEL_NAMES = {
    "toxic": "toxicity",
    "toxicity": "toxicity",
    "obscene": "obscenity",
    "threat": "threats",
    "insult": "insults",
    "identity_hate": "identity-based hate speech",
}


@cv_upload_router.post("")
async def upload_and_analyze_cv(
    request: CVUploadRequest = Depends(),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Upload a CV (PDF or DOCX) and either:
    1. (Initial) Parse CV to suggest profile data if profile is empty
    2. (Update) Compare against existing profile, run ATS check, and provide full analysis.
    """
    logger("CV_UPLOAD", f"CV upload from user {current_user.user_id}", level="DEBUG")
    try:
        file = request.file
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="CV file must not be empty")

        original_name = file.filename or "cv"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
        mime = file.content_type or guess_mime(original_name)
        validate_upload("cv", contents, mime, original_name)

        logger("CV_UPLOAD", f"File: {file.filename}, size={len(contents)} bytes", level="DEBUG")

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found for current user")

        freelancer_id = freelancer["freelancer_id"]

        # Step 1: Extract raw text
        if ext == "docx" or mime in _DOCX_MIMES:
            raw_text = await asyncio.to_thread(_extract_text_from_docx, contents)
        elif ext == "pdf" or mime == "application/pdf":
            raw_text = await asyncio.to_thread(_extract_text_from_pdf, contents)
        elif mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "bmp", "tiff"}:
            raw_text = await asyncio.to_thread(_extract_text_from_image, contents)
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Please upload a PDF or DOCX.",
            )

        if is_cv_text_too_sparse(raw_text):
            raise HTTPException(status_code=422, detail=CV_EXTRACTION_FAILED_MESSAGE)

        logger("CV_UPLOAD", f"Extracted {len(raw_text)} chars from CV", level="DEBUG")

        # Step 2: Store file in MinIO (non-fatal — parsing continues if storage is unreachable)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._")
        storage_path = f"cvs/{freelancer_id}/{safe_name}"
        public_url = None
        try:
            public_url = upload_cv_file(path=storage_path, file_bytes=contents, content_type=mime)
            FreelancerFunctions.update_freelancer(freelancer_id, {"cv_file_url": public_url})
            logger("CV_UPLOAD", f"CV stored: {public_url}", level="DEBUG")
        except Exception as upload_err:
            logger("CV_UPLOAD", f"CV storage upload failed (continuing): {upload_err}", level="WARNING")

        # Step 3: Check if profile is meaningfully empty
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

        # Step 4: Embedding similarity
        cv_embedding = await asyncio.to_thread(get_cv_embedding, raw_text)
        profile_embedding = await asyncio.to_thread(get_cv_embedding, profile_text)
        similarity = cosine_similarity(cv_embedding, profile_embedding)

        # Step 5: Skill coverage
        profile_skills = get_profile_skill_names(freelancer_id)
        matched_skills = extract_skills_from_text(raw_text, profile_skills)
        missing_skills = [s for s in profile_skills if s not in matched_skills]
        skill_coverage = len(matched_skills) / len(profile_skills) if profile_skills else None

        # Step 6: ATS compliance (rule-based flags only — numeric score comes from the ATS model below)
        ats_result = check_ats_compliance(raw_text)

        # Step 7: Scoring — all numeric scores come from the trained XGBoost models,
        # not the LLM (see cv_analysis_xgb_models/xgboost_cv_analysis_final.ipynb)
        match_result = await asyncio.to_thread(predict_match, raw_text, profile_text)
        ats_ml_result = await asyncio.to_thread(predict_ats, raw_text)
        section_scores = await asyncio.to_thread(predict_sections, raw_text, profile_text)

        model_scores = {
            **match_result,
            **ats_ml_result,
            "section_scores": section_scores,
        }

        # Step 8: Structured LLM analysis (narrative text only — no scoring)
        llm_analysis = await analyze_cv_with_llm(
            cv_text=raw_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            ats_result=ats_result,
            model_scores=model_scores,
        )

        # Step 9: Parse CV for profile suggestions
        parsed_profile = await parse_cv_for_profile(raw_text)

        resume_score = int(round(section_scores["overall"]))
        overall_score = compute_overall_score(section_scores["overall"], ats_ml_result["ats_label"])
        overall_grade = grade_overall_score(overall_score)

        logger(
            "CV_UPLOAD",
            f"CV analysis complete | freelancer={freelancer_id} | overall={overall_score} ({overall_grade}) "
            f"| match={match_result['match_label']} | ats={ats_ml_result['ats_label']} | similarity={similarity:.3f}",
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
                "resume_score": resume_score,
                "ats_score": ats_label_to_score(ats_ml_result["ats_label"]),
                "ats_label": ats_ml_result["ats_label"],
                "ats_confidence": ats_ml_result["ats_confidence"],
                "ats_flags": ats_result["ats_flags"],
                "match_label": match_result["match_label"],
                "match_confidence": match_result["match_confidence"],
                "similarity_score": round(similarity, 4),
                "skill_coverage": round(skill_coverage, 4) if skill_coverage is not None else None,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "section_scores": section_scores,
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


@cv_upload_router.post("/apply")
async def apply_cv_profile(
    request: CVApplyRequest,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Apply confirmed CV suggestions to the current freelancer profile.
    Matches the Flutter payload from CvAnalysisService.applyProfile().
    """
    logger("CV_UPLOAD", f"Apply CV profile from user {current_user.user_id}", level="DEBUG")

    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found for current user")

        freelancer_id = freelancer["freelancer_id"]

        applied_bio = False
        applied_skills = 0
        applied_work_experience = 0
        applied_education = 0

        if request.apply_bio and request.suggested_bio and request.suggested_bio.strip():
            _bio_text = request.suggested_bio.strip()
            rejection = await _scan_identity_fields_or_reject("", _bio_text, "bio")
            if rejection:
                return ResponseSchema.error(rejection["message"], rejection["status"], extra={"detected_labels": rejection["detected_labels"]})
            FreelancerFunctions.update_freelancer(
                freelancer_id=freelancer_id,
                update_data={"bio": _bio_text},
            )
            applied_bio = True

        if request.apply_skills and request.skills:
            existing_freelancer_skills = FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer_id)
            existing_skill_ids = {
                str(item.get("skill_id"))
                for item in existing_freelancer_skills
                if item.get("skill_id") is not None
            }

            for raw_skill in request.skills:
                skill_name = format_skill_name(raw_skill)
                if not skill_name:
                    continue

                skill = SkillFunctions.get_skill_by_name(skill_name)
                if not skill:
                    # skill is a shared, global lookup table with no single owner
                    # (visible to every user via autocomplete/search) - same fail-closed
                    # scan as POST /skills. A flagged name is skipped rather than
                    # aborting the whole CV-apply request, since this is one field among
                    # several being applied in the same call.
                    try:
                        harm_result = scan_harmful_text(skill_name)
                    except Exception as e:
                        logger("CV_UPLOAD", f"Skill-name scan errored for {skill_name!r}, skipping (fail closed): {e}", level="WARNING")
                        continue
                    if harm_result["is_flagged"]:
                        detected_labels = harm_result.get("detected_labels", [])
                        labels = [_SKILL_LABEL_NAMES.get(l, l) for l in detected_labels]
                        logger("CV_UPLOAD", f"Skipped CV-suggested skill {skill_name!r}, labels={detected_labels}", level="WARNING")
                        continue
                    skill = SkillFunctions.create_skill(skill_name)

                skill_id = str(skill["skill_id"])
                if skill_id in existing_skill_ids:
                    continue

                FreelancerSkillFunctions.create_freelancer_skill(
                    freelancer_id,
                    skill_id,
                    "intermediate",
                )
                existing_skill_ids.add(skill_id)
                applied_skills += 1

        if request.apply_work_experience and request.work_experience:
            for exp in request.work_experience:
                new_experience = WorkExperienceFunctions.create_work_experience(
                    freelancer_id=freelancer_id,
                    company_name=exp.company_name,
                    job_title=exp.job_title,
                    location=exp.location,
                    start_date=normalize_partial_date(exp.start_date),
                    end_date=None if exp.is_current else normalize_partial_date(exp.end_date),
                    is_current=exp.is_current,
                    description=exp.description,
                )
                _exp_short_text = " ".join(filter(None, [
                    exp.job_title, exp.company_name, exp.location,
                ]))
                asyncio.create_task(WorkExperienceFunctions.run_work_experience_scan(
                    str(new_experience["work_experience_id"]), _exp_short_text, exp.description, str(current_user.user_id),
                ))
                applied_work_experience += 1

        if request.apply_education and request.education:
            for edu in request.education:
                new_education = EducationFunctions.create_education(
                    freelancer_id=freelancer_id,
                    institution_name=edu.institution_name,
                    degree=edu.degree,
                    field_of_study=edu.field_of_study,
                    start_date=normalize_partial_date(edu.start_date),
                    end_date=None if edu.is_current else normalize_partial_date(edu.end_date),
                    is_current=edu.is_current,
                    grade=edu.grade if edu.grade and str(edu.grade).strip() else None,
                )
                _edu_short_text = " ".join(filter(None, [
                    edu.institution_name, edu.degree, edu.field_of_study, edu.grade,
                ]))
                asyncio.create_task(EducationFunctions.run_education_scan(
                    str(new_education["education_id"]), _edu_short_text, None, str(current_user.user_id),
                ))
                applied_education += 1

        logger(
            "CV_UPLOAD",
            f"CV profile applied | freelancer={freelancer_id} | "
            f"bio={applied_bio} | skills={applied_skills} | "
            f"work_experience={applied_work_experience} | education={applied_education}",
            level="INFO",
        )

        return ResponseSchema.success(
            {
                "message": "Profile updated successfully from CV suggestions",
                "applied": {
                    "bio": applied_bio,
                    "skills": applied_skills,
                    "work_experience": applied_work_experience,
                    "education": applied_education,
                },
            },
            200,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_UPLOAD", f"Apply CV profile failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"Apply CV profile failed: {str(e)}", 500)


def normalize_partial_date(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()
    if not value:
        return None

    if re.fullmatch(r"\d{4}", value):
        return f"{value}-01-01"

    if re.fullmatch(r"\d{4}-\d{2}", value):
        return f"{value}-01"

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value

    return None

def format_skill_name(value: str) -> str:
    acronyms = {"UI", "UX", "API", "SQL", "HTML", "CSS", "PHP", "AI", "ML", "NLP"}
    words = []
    for word in value.strip().split():
        upper = word.upper()
        if upper in acronyms:
            words.append(upper)
        else:
            words.append(word.capitalize())
    return " ".join(words)