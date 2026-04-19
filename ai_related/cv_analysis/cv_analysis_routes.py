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
    cosine_similarity,
    classify_cv_quality,
    build_cv_recommendations,
)
from ai_related.job_matching.embedding_service import get_embedding

cv_analysis_router = APIRouter(prefix="/cv_analysis", tags=["CV Analysis"])


@cv_analysis_router.post("/analyze", response_model=None)
async def analyze_cv(
    cv_file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_freelancer_user),
) -> Dict[str, Any]:
    """
    Analyze a freelancer's CV against their profile.
    Returns scoring (good/enough/bad) and recommendations for improvement.
    """
    logger("CV_ANALYSIS", f"CV analysis request from user {current_user.user_id}", level="DEBUG")
    try:
        # Extract CV text
        cv_text = await extract_cv_text(cv_file)
        if not cv_text:
            raise HTTPException(status_code=400, detail="Unable to extract text from CV")

        # Get freelancer profile
        from routes.freelancers.freelancer_functions import FreelancerFunctions
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            raise HTTPException(status_code=404, detail="Freelancer profile not found")

        freelancer_id = freelancer['freelancer_id']

        # Build profile text
        profile_text = build_freelancer_profile_text(freelancer_id)
        if not profile_text:
            raise HTTPException(status_code=400, detail="Unable to build freelancer profile text")

        # Get embeddings
        cv_embedding = await get_embedding(cv_text)
        profile_embedding = await get_embedding(profile_text)

        # Calculate similarity
        similarity = cosine_similarity(cv_embedding, profile_embedding)

        # Get skills and compare CV against the freelancer profile skills
        profile_skills = get_profile_skill_names(freelancer_id)
        matched_skills = extract_skills_from_text(cv_text, profile_skills)
        missing_skills = [skill for skill in profile_skills if skill not in matched_skills]

        # Calculate skill coverage
        skill_coverage = len(matched_skills) / len(profile_skills) if profile_skills else 0.0

        # Classify quality
        quality = classify_cv_quality(similarity, skill_coverage)

        # Build recommendations
        recommendations = await build_cv_recommendations(
            cv_text=cv_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
        )

        result = {
            "scoring": quality,
            "similarity_score": similarity,
            "skill_coverage": skill_coverage,
            "recommendations": recommendations,
        }

        logger("CV_ANALYSIS", f"CV analysis completed for freelancer {freelancer_id} | quality={quality} | similarity={similarity:.3f}", level="INFO")

        return ResponseSchema.success(result)

    except Exception as e:
        logger("CV_ANALYSIS", f"CV analysis failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"CV analysis failed: {str(e)}")