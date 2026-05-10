import os
import re
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException

from functions.authentication import get_freelancer_user
from functions.schema_model import CVUploadRequest, UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_cv_file, guess_mime
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.freelancer_embeddings.freelancer_embedding_functions import FreelancerEmbeddingFunctions
from ai_related.cv_analysis.cv_analysis import (
    build_freelancer_profile_text,
    get_profile_skill_names,
    extract_skills_from_text,
    get_cv_embedding,
    cosine_similarity,
    classify_cv_quality,
    build_cv_recommendations,
    check_ats_compliance,
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


def _refresh_freelancer_embedding(freelancer_id: str) -> None:
    """Re-embed the freelancer profile and upsert into the DB."""
    from ai_related.job_matching.source_text_builder import build_freelancer_source_text
    source_text = build_freelancer_source_text(freelancer_id)
    if not source_text:
        return
    vector = get_cv_embedding(source_text)
    existing = FreelancerEmbeddingFunctions.get_freelancer_embedding_by_freelancer_id(freelancer_id)
    if existing:
        FreelancerEmbeddingFunctions.update_freelancer_embedding(
            existing["embedding_id"],
            {"embedding_vector": vector, "source_text": source_text},
        )
    else:
        FreelancerEmbeddingFunctions.create_freelancer_embedding(
            freelancer_id=freelancer_id,
            embedding_vector=vector,
            source_text=source_text,
        )


@cv_upload_router.post("")
async def upload_and_analyze_cv(
    request: CVUploadRequest = Depends(),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Upload a CV (PDF or DOCX), extract its text, compare it against the
    freelancer's existing profile, run an ATS compliance check, and return
    a full analysis: score, similarity, skill gaps, ATS flags, and LLM
    recommendations.
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
        original_name = file.filename or f"cv.{ext}"
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._")
        storage_path = f"cvs/{freelancer_id}/{safe_name}"
        
        public_url = upload_cv_file(
        path=storage_path,
        file_bytes=contents,
        content_type=mime,
        )
        FreelancerFunctions.update_freelancer(freelancer_id, {"cv_file_url": public_url})
        logger("CV_UPLOAD", f"CV stored: {public_url}", level="DEBUG")

        # --- Step 3: Build freelancer profile text from DB ---
        profile_text = build_freelancer_profile_text(freelancer_id)
        if not profile_text:
            raise HTTPException(
                status_code=400,
                detail="Freelancer profile is incomplete. Add skills, education, or experience before uploading a CV.",
            )

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

        # --- Step 7: Overall scoring ---
        scoring = classify_cv_quality(similarity, skill_coverage, ats_result["ats_score"])

        # --- Step 8: LLM recommendations ---
        recommendations = await build_cv_recommendations(
            cv_text=raw_text,
            profile_text=profile_text,
            similarity=similarity,
            skill_coverage=skill_coverage,
            matched_skills=matched_skills,
            missing_skills=missing_skills,
        )

        # --- Step 9: Refresh freelancer embedding ---
        try:
            _refresh_freelancer_embedding(freelancer_id)
            logger("CV_UPLOAD", f"Freelancer embedding refreshed for {freelancer_id}", level="DEBUG")
        except Exception as emb_err:
            logger("CV_UPLOAD", f"Embedding refresh failed (non-fatal): {emb_err}", level="WARNING")

        logger(
            "CV_UPLOAD",
            f"CV analysis complete | freelancer={freelancer_id} | scoring={scoring} "
            f"| similarity={similarity:.3f} | ats={ats_result['ats_score']}",
            level="INFO",
        )

        return ResponseSchema.success(
            {
                "file_url": public_url,
                "file_name": f"{freelancer_id}.{ext}",
                "file_type": mime,
                "scoring": scoring,
                "similarity_score": round(similarity, 4),
                "skill_coverage": round(skill_coverage, 4) if skill_coverage is not None else None,
                "matched_skills": matched_skills,
                "missing_skills": missing_skills,
                "ats_score": ats_result["ats_score"],
                "ats_flags": ats_result["ats_flags"],
                "recommendations": recommendations,
            },
            200,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_UPLOAD", f"CV upload/analyze failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"CV upload/analyze failed: {str(e)}", 500)
