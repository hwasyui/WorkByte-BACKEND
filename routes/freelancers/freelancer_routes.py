import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query, UploadFile, File
from typing import List, Optional, Dict
import uuid
from functions.schema_model import FreelancerCreate, FreelancerUpdate, FreelancerResponse, FreelancerProfileComplete
from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_freelancer_user
from functions.access_control import assert_freelancer_owns, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.freelancers.freelancer_functions import FreelancerFunctions, get_comprehensive_freelancer_profile
from ai_related.job_matching.embedding_manager import upsert_freelancer_embedding, mark_freelancer_dirty
from functions.supabase_client import upload_freelancer_profile_picture, delete_file, BUCKET_USER_ASSETS
from mimetypes import guess_type as guess_mime
from ai_related.cv_analysis.cv_analysis import parse_cv_for_profile
from routes.cv_upload.cv_upload_functions import (
    _extract_text_from_pdf,
    _extract_text_from_docx,
    _extract_text_from_image,
)

_DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
}

freelancer_router = APIRouter(prefix="/freelancers", tags=["Freelancers"])


@freelancer_router.get("", response_model=List[FreelancerResponse])
async def get_all_freelancers(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        logger("FREELANCER", f"Retrieved freelancer profile for user {current_user.user_id}", "GET /freelancers", "INFO")
        return ResponseSchema.success([freelancer], 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancers: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.post("", response_model=FreelancerResponse, status_code=201)
async def create_freelancer(
    freelancer: FreelancerCreate = Depends(),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    try:
        freelancer_id = freelancer.freelancer_id or str(uuid.uuid4())
        current_freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if current_freelancer:
            return ResponseSchema.error(f"Freelancer profile already exists for user {current_user.user_id}", 400)
        if freelancer.user_id and str(freelancer.user_id) != str(current_user.user_id):
            return ResponseSchema.error("Cannot create a freelancer profile for another user", 403)

        profile_picture_url = None
        if freelancer.profile_picture is not None:
            contents = await freelancer.profile_picture.read()
            if not contents:
                return ResponseSchema.error("Profile picture file must not be empty", 400)
            mime_type = freelancer.profile_picture.content_type or guess_mime(freelancer.profile_picture.filename or "avatar.jpg")[0]
            if not mime_type.startswith("image/"):
                return ResponseSchema.error("Only image files are allowed for profile pictures", 400)
            logger("FREELANCER", f"Uploading freelancer avatar for user {current_user.user_id}: filename={freelancer.profile_picture.filename}, size={len(contents)} bytes, mime={mime_type}", level="DEBUG")
            profile_picture_url = upload_freelancer_profile_picture(
                freelancer_id=current_user.user_id,
                file_name=freelancer.profile_picture.filename or "avatar.jpg",
                file_bytes=contents,
                content_type=mime_type,
            )
            logger("FREELANCER", f"Freelancer avatar uploaded: {profile_picture_url}", level="DEBUG")

        new_freelancer = FreelancerFunctions.create_freelancer(
            freelancer_id=freelancer_id,
            user_id=current_user.user_id,
            full_name=freelancer.full_name,
            bio=freelancer.bio,
            cv_file_url=None,
            profile_picture_url=profile_picture_url,
            estimated_rate=freelancer.estimated_rate,
            rate_time=freelancer.rate_time,
            rate_currency=freelancer.rate_currency,
            create_embedding=True
        )
        asyncio.create_task(upsert_freelancer_embedding(str(new_freelancer["freelancer_id"])))
        logger("FREELANCER", f"Created freelancer {freelancer_id}", "POST /freelancers", "INFO")
        return ResponseSchema.success(new_freelancer, 201)
    except Exception as e:
        error_msg = f"Failed to create freelancer: {str(e)}"
        logger("FREELANCER", error_msg, "POST /freelancers", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.post("/parse-cv")
async def parse_cv_for_autofill(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Parse a CV and return structured profile data for frontend autofill.
    Does not apply any changes — returns parsed data for the user to review and
    populate form fields before submitting via POST/PUT /freelancers.
    """
    try:
        contents = await file.read()
        if not contents:
            return ResponseSchema.error("CV file must not be empty", 400)

        original_name = file.filename or "cv"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
        mime = file.content_type or ""

        if ext == "docx" or mime in _DOCX_MIMES:
            raw_text = _extract_text_from_docx(contents)
        elif ext == "pdf" or mime == "application/pdf":
            raw_text = _extract_text_from_pdf(contents)
        elif mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "bmp", "tiff"}:
            raw_text = _extract_text_from_image(contents)
        else:
            return ResponseSchema.error("Unsupported file type. Please upload a PDF, DOCX, or image.", 400)

        if not raw_text:
            return ResponseSchema.error("Unable to extract text from the uploaded CV.", 422)

        parsed_profile = await parse_cv_for_profile(raw_text)

        logger(
            "FREELANCER",
            f"CV parsed for autofill | user={current_user.user_id} | "
            f"skills={len(parsed_profile.get('skills', []))} | "
            f"experience={len(parsed_profile.get('work_experience', []))}",
            "POST /freelancers/parse-cv",
            "INFO",
        )
        return ResponseSchema.success(parsed_profile, 200)

    except Exception as e:
        error_msg = f"Failed to parse CV: {str(e)}"
        logger("FREELANCER", error_msg, "POST /freelancers/parse-cv", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.put("/{identifier}", response_model=FreelancerResponse)
async def update_freelancer(
    identifier: str,
    freelancer_update: FreelancerUpdate = Depends(FreelancerUpdate.as_form),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        freelancer_id = existing["freelancer_id"]
        assert_freelancer_owns(current_user, freelancer_id)

        update_data = freelancer_update.model_dump(
            exclude={"profile_picture"},
            exclude_unset=True,
            exclude_none=True,
        )

        if freelancer_update.profile_picture is not None:
            contents = await freelancer_update.profile_picture.read()
            if not contents:
                return ResponseSchema.error("Profile picture file must not be empty", 400)
            mime_type = freelancer_update.profile_picture.content_type or guess_mime(freelancer_update.profile_picture.filename or "avatar.jpg")[0]
            if not mime_type.startswith("image/"):
                return ResponseSchema.error("Only image files are allowed for profile pictures", 400)
            logger("FREELANCER", f"Uploading freelancer avatar for user {current_user.user_id}: filename={freelancer_update.profile_picture.filename}, size={len(contents)} bytes, mime={mime_type}", level="DEBUG")
            update_data["profile_picture_url"] = upload_freelancer_profile_picture(
                freelancer_id=current_user.user_id,
                file_name=freelancer_update.profile_picture.filename or "avatar.jpg",
                file_bytes=contents,
                content_type=mime_type,
            )
            logger("FREELANCER", f"Freelancer avatar uploaded: {update_data['profile_picture_url']}", level="DEBUG")

        updated_freelancer = FreelancerFunctions.update_freelancer(freelancer_id=freelancer_id, update_data=update_data)
        mark_freelancer_dirty(freelancer_id)
        logger("FREELANCER", f"Updated freelancer {freelancer_id}", "PUT /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(updated_freelancer, 200)
    except Exception as e:
        error_msg = f"Failed to update freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "PUT /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.delete("/{identifier}", status_code=200)
async def delete_freelancer(identifier: str, current_user: UserInDB = Depends(get_freelancer_user)):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not existing:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        freelancer_id = existing["freelancer_id"]
        assert_freelancer_owns(current_user, freelancer_id)
        FreelancerFunctions.delete_freelancer(freelancer_id, delete_embedding=True)
        logger("FREELANCER", f"Freelancer {freelancer_id} deleted", "DELETE /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(f"Freelancer {freelancer_id} deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "DELETE /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/search", response_model=Dict)
async def search_freelancers(
    name: str = Query(..., description="Freelancer name to search for"),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        results = FreelancerFunctions.search_freelancers_by_name(name)
        logger("FREELANCER", f"Found {len(results)} results for '{name}'", "GET /freelancers/search", "INFO")
        return ResponseSchema.success({"results": results, "count": len(results)}, 200)
    except Exception as e:
        error_msg = f"Failed to search freelancers: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/search", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/{freelancer_id}/skills", response_model=Dict)
async def get_freelancer_skills(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        skills = FreelancerFunctions.get_freelancer_skills_with_names(freelancer_id)
        logger("FREELANCER", f"Retrieved skills for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/skills", "INFO")
        return ResponseSchema.success(skills, 200)
    except Exception as e:
        error_msg = f"Failed to fetch skills for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/skills", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.get("/{freelancer_id}/embedding", response_model=Dict)
async def get_freelancer_embedding(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        embedding = FreelancerFunctions.get_freelancer_embedding(freelancer_id)
        if not embedding:
            return ResponseSchema.error(f"Embedding not found for freelancer {freelancer_id}", 404)
        result = {
            "embedding_id": embedding.get("embedding_id"),
            "freelancer_id": embedding.get("freelancer_id"),
            "source_text": embedding.get("source_text"),
            "created_at": embedding.get("created_at"),
            "updated_at": embedding.get("updated_at")
        }
        logger("FREELANCER", f"Retrieved embedding for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/embedding", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        error_msg = f"Failed to fetch embedding for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/embedding", "ERROR")
        return ResponseSchema.error(error_msg, 500)

_VALID_FREELANCER_ORDER_BY = {"created_at", "updated_at", "full_name", "estimated_rate", "total_jobs"}

@freelancer_router.get("/browse/all", response_model=List[FreelancerResponse])
async def browse_all_freelancers(
    order_by: str = Query(
        default="created_at",
        description="Sort field. One of: created_at (default), updated_at, full_name, estimated_rate, total_jobs",
    ),
    order_dir: str = Query(default="desc", description="asc or desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_current_user),
):
    """Browse all freelancers with pagination and sorting - Authenticated users only"""
    try:
        if order_by not in _VALID_FREELANCER_ORDER_BY:
            return ResponseSchema.error(
                f"Invalid order_by '{order_by}'. Valid values: {', '.join(sorted(_VALID_FREELANCER_ORDER_BY))}", 400
            )
        result = FreelancerFunctions.browse_freelancers(
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            page_size=page_size,
        )
        logger("FREELANCER", f"Browsed freelancers: page={page}", "GET /freelancers/browse/all", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancers for browse: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/browse/all", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@freelancer_router.get("/{freelancer_id}/profile", response_model=FreelancerProfileComplete)
async def get_comprehensive_freelancer_profile_endpoint(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        profile = get_comprehensive_freelancer_profile(freelancer_id)
        if not profile:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        logger("FREELANCER", f"Retrieved comprehensive profile for freelancer {freelancer_id}", "GET /freelancers/{freelancer_id}/profile", "INFO")
        return ResponseSchema.success(profile, 200)
    except Exception as e:
        error_msg = f"Failed to fetch comprehensive profile {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{freelancer_id}/profile", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.post("/{freelancer_id}/profile-picture", response_model=FreelancerResponse)
async def upload_freelancer_profile_picture_endpoint(
    freelancer_id: str,
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(freelancer_id)
        if not existing:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        assert_freelancer_owns(current_user, existing["freelancer_id"])

        contents = await file.read()
        if not contents:
            return ResponseSchema.error("Profile picture file must not be empty", 400)
        mime_type = file.content_type or guess_mime(file.filename or "avatar.jpg")[0]
        if not mime_type or not mime_type.startswith("image/"):
            return ResponseSchema.error("Only image files are allowed for profile pictures", 400)

        profile_picture_url = upload_freelancer_profile_picture(
            freelancer_id=current_user.user_id,
            file_name=file.filename or "avatar.jpg",
            file_bytes=contents,
            content_type=mime_type,
        )
        updated = FreelancerFunctions.update_freelancer(
            freelancer_id=existing["freelancer_id"],
            update_data={"profile_picture_url": profile_picture_url},
        )
        logger("FREELANCER", f"Profile picture updated for freelancer {freelancer_id}", f"POST /freelancers/{freelancer_id}/profile-picture", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        error_msg = f"Failed to upload profile picture for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, f"POST /freelancers/{freelancer_id}/profile-picture", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@freelancer_router.delete("/{freelancer_id}/profile-picture", status_code=200)
async def delete_freelancer_profile_picture(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    try:
        existing = FreelancerFunctions.get_freelancer_by_id_or_user_id(freelancer_id)
        if not existing:
            return ResponseSchema.error(f"Freelancer {freelancer_id} not found", 404)
        assert_freelancer_owns(current_user, existing["freelancer_id"])

        profile_picture_url = existing.get("profile_picture_url")
        if not profile_picture_url:
            return ResponseSchema.error("No profile picture to delete", 400)

        # Extract path from URL or assume path
        # Since URL is public URL, path is after bucket
        # e.g., https://xxx.supabase.co/storage/v1/object/public/user-assets/avatars/123.jpg
        # path = avatars/123.jpg
        if "user-assets/" in profile_picture_url:
            path = profile_picture_url.split("user-assets/")[-1]
        else:
            # Fallback, assume path from upload function
            path = f"avatars/{current_user.user_id}.jpg"  # or whatever ext, but since delete, maybe try common exts

        try:
            delete_file(BUCKET_USER_ASSETS, path)
        except Exception as e:
            logger("FREELANCER", f"Failed to delete file from storage: {str(e)}", level="WARNING")
            # Continue to update DB

        updated = FreelancerFunctions.update_freelancer(
            freelancer_id=existing["freelancer_id"],
            update_data={"profile_picture_url": None},
        )
        logger("FREELANCER", f"Profile picture deleted for freelancer {freelancer_id}", f"DELETE /freelancers/{freelancer_id}/profile-picture", "INFO")
        return ResponseSchema.success({"message": "Profile picture deleted successfully", "freelancer": updated}, 200)
    except Exception as e:
        error_msg = f"Failed to delete profile picture for freelancer {freelancer_id}: {str(e)}"
        logger("FREELANCER", error_msg, f"DELETE /freelancers/{freelancer_id}/profile-picture", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ✅ Wildcard last — must come after all /{freelancer_id}/xxx routes
@freelancer_router.get("/{identifier}", response_model=FreelancerResponse)
async def get_freelancer(identifier: str, current_user: UserInDB = Depends(get_current_user)):
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_id_or_user_id(identifier)
        if not freelancer:
            return ResponseSchema.error(f"Freelancer {identifier} not found", 404)
        logger("FREELANCER", f"Retrieved freelancer {identifier}", "GET /freelancers/{identifier}", "INFO")
        return ResponseSchema.success(freelancer, 200)
    except Exception as e:
        error_msg = f"Failed to fetch freelancer {identifier}: {str(e)}"
        logger("FREELANCER", error_msg, "GET /freelancers/{identifier}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
