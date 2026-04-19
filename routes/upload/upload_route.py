import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from functions.authentication import get_current_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_file, BUCKET_MAP, BUCKET_JOB_FILES, guess_mime
import uuid


upload_router = APIRouter(prefix="/upload", tags=["Upload"])


@upload_router.post("")
async def upload_file_endpoint(
    file: UploadFile = File(...),
    bucket: str = Query(...),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Upload a file to Supabase Storage.
    Optional ?bucket= query param to select target bucket.
    Returns: { file_url, file_name, file_type, file_size }
    """
    try:
        contents = await file.read()
        file_size = len(contents)

        original_name = file.filename or "file"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        mime = file.content_type or guess_mime(original_name)
        storage_path = f"uploads/{current_user.user_id}/{uuid.uuid4()}.{ext}"

        selected_bucket = BUCKET_MAP.get(bucket, BUCKET_JOB_FILES)

        public_url = upload_file(
            bucket=selected_bucket,
            path=storage_path,
            file_bytes=contents,
            content_type=mime,
        )

        logger("UPLOAD", f"File uploaded to [{selected_bucket}]: {storage_path} ({file_size} bytes)", level="INFO")

        return ResponseSchema.success({
            "file_url": public_url,
            "file_name": original_name,
            "file_type": ext,
            "file_size": file_size,
        }, 200)

    except HTTPException:
        raise
    except Exception as e:
        logger("UPLOAD", f"Upload failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"Upload failed: {str(e)}", 500)