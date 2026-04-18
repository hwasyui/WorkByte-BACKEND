import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from functions.authentication import get_current_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from supabase import create_client
import uuid
import mimetypes

upload_router = APIRouter(prefix="/upload", tags=["Upload"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "job-files")


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


@upload_router.post("")
async def upload_file(
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_current_user),
):
    """Upload a file to Supabase Storage — returns public URL, file_type, file_size"""
    try:
        contents = await file.read()
        file_size = len(contents)

        original_name = file.filename or "file"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        mime = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"

        storage_path = f"uploads/{current_user.user_id}/{uuid.uuid4()}.{ext}"

        supabase = get_supabase()
        supabase.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            path=storage_path,
            file=contents,
            file_options={"content-type": mime},
        )

        public_url = supabase.storage.from_(SUPABASE_STORAGE_BUCKET).get_public_url(storage_path)

        logger("UPLOAD", f"File uploaded: {storage_path} ({file_size} bytes)", level="INFO")

        return ResponseSchema.success({
            "file_url": public_url,
            "file_name": original_name,
            "file_type": ext,
            "file_size": file_size,
        }, 200)

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"Upload failed: {str(e)}"
        logger("UPLOAD", error_msg, level="ERROR")
        return ResponseSchema.error(error_msg, 500)