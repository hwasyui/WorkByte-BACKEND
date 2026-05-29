import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import Dict, Any

from functions.authentication import get_freelancer_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from ai_related.cv_analysis.cv_analysis import (
    extract_cv_text,
    build_freelancer_profile_text,
    get_profile_skill_names,
    extract_skills_from_text,
    get_cv_embedding,
    cosine_similarity,
    classify_cv_quality,
    compute_resume_score,
    compute_overall_score,
    grade_overall_score,
    check_ats_compliance,
    analyze_cv_with_llm,
    parse_cv_for_profile,
)
from routes.freelancers.freelancer_functions import FreelancerFunctions

cv_analysis_router = APIRouter(prefix="/cv_analysis", tags=["CV Analysis"])


@cv_analysis_router.post("/analyze", response_model=None)
async def analyze_cv(
    cv_file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_freelancer_user),
) -> Dict[str, Any]:
    logger("CV_ANALYSIS", f"CV analysis request from user {current_user.user_id}", level="DEBUG")
    try:
        cv_text = await extract_cv_text(cv_file)
        if not cv_text:
            raise HTTPException(status_code=400, detail="Unable to extract text from CV")

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found")

        freelancer_id = freelancer["freelancer_id"]

        profile_text = build_freelancer_profile_text(freelancer_id)
        if not profile_text:
            raise HTTPException(status_code=400, detail="Freelancer profile is incomplete. Add skills, education, or experience first.")

        cv_embedding = get_cv_embedding(cv_text)
        profile_embedding = get_cv_embedding(profile_text)
        similarity = cosine_similarity(cv_embedding, profile_embedding)

        profile_skills = get_profile_skill_names(freelancer_id)
        matched_skills = extract_skills_from_text(cv_text, profile_skills)
        missing_skills = [s for s in profile_skills if s not in matched_skills]
        skill_coverage = len(matched_skills) / len(profile_skills) if profile_skills else None

        ats_result = check_ats_compliance(cv_text)
        resume_score = compute_resume_score(similarity, skill_coverage)

        llm_analysis = await analyze_cv_with_llm(
            cv_text=cv_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
            ats_result=ats_result,
        )

        parsed_profile = await parse_cv_for_profile(cv_text)

        final_resume_score = llm_analysis["resume_score"]
        overall_score = compute_overall_score(final_resume_score, ats_result["ats_score"])
        overall_grade = grade_overall_score(overall_score)

        logger(
            "CV_ANALYSIS",
            f"CV analysis completed | freelancer={freelancer_id} | overall={overall_score} ({overall_grade}) | similarity={similarity:.3f}",
            level="INFO",
        )

        return ResponseSchema.success({
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
        })

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_ANALYSIS", f"CV analysis failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"CV analysis failed: {str(e)}")
