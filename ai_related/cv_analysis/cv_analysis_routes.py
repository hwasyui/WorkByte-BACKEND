import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import Dict, Any

from functions.authentication import get_freelancer_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.minio_client import validate_file_size
from ai_related.cv_analysis.cv_analysis import (
    extract_cv_text,
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
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.freelancer_skills.freelancer_skill_functions import FreelancerSkillFunctions
from routes.work_experience.work_experience_functions import WorkExperienceFunctions
from routes.education.education_functions import EducationFunctions

cv_analysis_router = APIRouter(prefix="/cv_analysis", tags=["CV Analysis"])


@cv_analysis_router.post("/analyze", response_model=None)
async def analyze_cv(
    cv_file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_freelancer_user),
) -> Dict[str, Any]:
    logger("CV_ANALYSIS", f"CV analysis request from user {current_user.user_id}", level="DEBUG")
    try:
        contents = await cv_file.read()
        validate_file_size(contents, cv_file.filename or "CV file")
        await cv_file.seek(0)

        cv_text = await extract_cv_text(cv_file)
        if not cv_text:
            raise HTTPException(
                status_code=422,
                detail="Unable to extract text from the uploaded CV. Ensure the file contains readable text.",
            )

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found")

        freelancer_id = freelancer["freelancer_id"]

        # profile text always falls back to full_name, so it's never falsy on its own
        has_bio = bool(freelancer.get("bio"))
        has_skills = bool(FreelancerSkillFunctions.get_freelancer_skills_by_freelancer_id(freelancer_id))
        has_work_exp = bool(WorkExperienceFunctions.get_work_experiences_by_freelancer_id(freelancer_id))
        has_education = bool(EducationFunctions.get_educations_by_freelancer_id(freelancer_id))
        if not (has_bio or has_skills or has_work_exp or has_education):
            raise HTTPException(status_code=400, detail="Freelancer profile is incomplete. Add skills, education, or experience first.")

        profile_text = build_freelancer_profile_text(freelancer_id)
        if not profile_text:
            raise HTTPException(status_code=400, detail="Freelancer profile is incomplete. Add skills, education, or experience first.")

        cv_embedding = await asyncio.to_thread(get_cv_embedding, cv_text)
        profile_embedding = await asyncio.to_thread(get_cv_embedding, profile_text)
        similarity = cosine_similarity(cv_embedding, profile_embedding)

        profile_skills = get_profile_skill_names(freelancer_id)
        matched_skills = extract_skills_from_text(cv_text, profile_skills)
        missing_skills = [s for s in profile_skills if s not in matched_skills]
        skill_coverage = len(matched_skills) / len(profile_skills) if profile_skills else None

        ats_result = check_ats_compliance(cv_text)

        # Scoring — all numeric scores come from the trained XGBoost models,
        # not the LLM (see cv_analysis_xgb_models/xgboost_cv_analysis_final.ipynb)
        match_result = await asyncio.to_thread(predict_match, cv_text, profile_text)
        ats_ml_result = await asyncio.to_thread(predict_ats, cv_text)
        section_scores = await asyncio.to_thread(predict_sections, cv_text, profile_text)

        model_scores = {
            **match_result,
            **ats_ml_result,
            "section_scores": section_scores,
        }

        llm_analysis = await analyze_cv_with_llm(
            cv_text=cv_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            ats_result=ats_result,
            model_scores=model_scores,
        )

        parsed_profile = await parse_cv_for_profile(cv_text)

        resume_score = int(round(section_scores["overall"]))
        overall_score = compute_overall_score(section_scores["overall"], ats_ml_result["ats_label"])
        overall_grade = grade_overall_score(overall_score)

        logger(
            "CV_ANALYSIS",
            f"CV analysis completed | freelancer={freelancer_id} | overall={overall_score} ({overall_grade}) "
            f"| match={match_result['match_label']} | ats={ats_ml_result['ats_label']} | similarity={similarity:.3f}",
            level="INFO",
        )

        return ResponseSchema.success({
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
        })

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger("CV_ANALYSIS", f"CV analysis failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"CV analysis failed: {str(e)}")
