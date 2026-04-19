import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException

from functions.authentication import get_freelancer_user
from functions.schema_model import CVParseRequest, UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_cv_file, guess_mime
from routes.freelancers.freelancer_functions import FreelancerFunctions
from .cv_upload_functions import (
    _extract_text_from_pdf,
    _extract_text_from_image,
    _parse_resume_text,
    _parse_resume_text_with_llm,
)


cv_upload_router = APIRouter(prefix="/cv_upload", tags=["CV Upload"])


@cv_upload_router.post("")
async def upload_and_parse_cv(
    request: CVParseRequest = Depends(),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Upload a freelancer CV and parse profile-relevant fields.
    The file is uploaded to Supabase user-assets bucket under cvs/{freelancer_id}/.
    Returns parsed skills, languages, education, work experience, and contact info.
    """
    logger("CV_UPLOAD", f"CV upload request from user {current_user.user_id}, use_llm={request.use_llm}", level="DEBUG")
    try:
        file = request.file
        use_llm = request.use_llm
        contents = await file.read()
        if not contents:
            logger("CV_UPLOAD", "CV file is empty", level="WARNING")
            raise HTTPException(status_code=400, detail="CV file must not be empty")

        logger("CV_UPLOAD", f"File received: {file.filename}, size={len(contents)} bytes, mime={file.content_type}", level="DEBUG")

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            logger("CV_UPLOAD", f"Freelancer profile not found for user {current_user.user_id}", level="WARNING")
            raise HTTPException(status_code=404, detail="Freelancer profile not found for current user")

        logger("CV_UPLOAD", f"Freelancer found: {freelancer['freelancer_id']}", level="DEBUG")

        original_name = file.filename or "cv"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "pdf"
        filename = f"{freelancer['freelancer_id']}.{ext}"
        mime = file.content_type or guess_mime(original_name)
        storage_path = f"cvs/{freelancer['freelancer_id']}.{ext}"

        logger("CV_UPLOAD", f"Uploading to Supabase: {storage_path}, mime={mime}", level="DEBUG")
        public_url = upload_cv_file(
            path=storage_path,
            file_bytes=contents,
            content_type=mime,
        )
        logger("CV_UPLOAD", f"Upload successful: {public_url}", level="DEBUG")

        FreelancerFunctions.update_freelancer(freelancer['freelancer_id'], {"cv_file_url": public_url})
        logger("CV_UPLOAD", f"Database updated with CV URL for freelancer {freelancer['freelancer_id']}", level="DEBUG")

        if mime == "application/pdf" or ext == "pdf":
            text = _extract_text_from_pdf(contents)
        elif mime.startswith("image/") or ext in {"png", "jpg", "jpeg", "bmp", "tiff"}:
            text = _extract_text_from_image(contents)
        else:
            logger("CV_UPLOAD", f"Unsupported file type: mime={mime}, ext={ext}", level="WARNING")
            raise HTTPException(status_code=400, detail="Unsupported CV file type. Use PDF or image.")

        if not text:
            logger("CV_UPLOAD", "No text extracted from CV", level="WARNING")
            raise HTTPException(status_code=422, detail="Unable to extract text from the uploaded CV.")

        logger("CV_UPLOAD", f"Text extracted: {len(text)} characters", level="DEBUG")

        parsed_profile = None
        if use_llm:
            logger("CV_UPLOAD", "Attempting LLM parsing", level="DEBUG")
            try:
                parsed_profile = await _parse_resume_text_with_llm(text)
                logger("CV_UPLOAD", "LLM parsing successful", level="DEBUG")
            except Exception as llm_err:
                logger("CV_UPLOAD", f"LLM resume parsing failed: {str(llm_err)}", level="WARNING")
        if parsed_profile is None:
            logger("CV_UPLOAD", "Using fallback parsing", level="DEBUG")
            parsed_profile = _parse_resume_text(text)

        logger(
            "CV_UPLOAD",
            f"CV parsed for freelancer {freelancer['freelancer_id']} | file={filename} | source={mime}",
            level="INFO",
        )

        logger("CV_UPLOAD", f"Returning parsed profile: name={parsed_profile.get('full_name')}, skills={len(parsed_profile.get('skills', []))}", level="DEBUG")
        return ResponseSchema.success({
            "file_url": public_url,
            "file_name": filename,
            "file_type": mime,
            "parsed_profile": parsed_profile,
        }, 200)

    except HTTPException:
        raise
    except Exception as e:
        logger("CV_UPLOAD", f"CV upload/parse failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"CV upload/parse failed: {str(e)}", 500)