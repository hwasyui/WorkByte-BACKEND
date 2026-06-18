import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from io import BytesIO
from functions.authentication import get_current_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.minio_client import upload_file, download_file, BUCKET_MAP, BUCKET_JOB_FILES, guess_mime, PRIVATE_BUCKETS, resolve_file_url
import uuid


upload_router = APIRouter(prefix="/upload", tags=["Upload"])
files_router  = APIRouter(prefix="/files",  tags=["Files"])


@upload_router.post("")
async def upload_file_endpoint(
    file: UploadFile = File(...),
    bucket: str = Query(...),
    current_user: UserInDB = Depends(get_current_user),
):
    """Upload a file to MinIO.

    Args:
        bucket: Target storage bucket name (query param).

    Returns:
        Dict with file_url, file_name, file_type, and file_size.
    """
    try:
        contents = await file.read()
        file_size = len(contents)

        original_name = file.filename or "file"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        mime = file.content_type or guess_mime(original_name)
        storage_path = f"uploads/{current_user.user_id}/{uuid.uuid4()}.{ext}"

        selected_bucket = BUCKET_MAP.get(bucket, BUCKET_JOB_FILES)

        stored = upload_file(
            bucket=selected_bucket,
            path=storage_path,
            file_bytes=contents,
            content_type=mime,
        )
        file_url = resolve_file_url(selected_bucket, stored)

        logger("UPLOAD", f"File uploaded to [{selected_bucket}]: {storage_path} ({file_size} bytes)", level="INFO")

        return ResponseSchema.success({
            "file_url": file_url,
            "file_name": original_name,
            "file_type": ext,
            "file_size": file_size,
        }, 200)

    except HTTPException:
        raise
    except Exception as e:
        logger("UPLOAD", f"Upload failed: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"Upload failed: {str(e)}", 500)


@files_router.get("/{bucket}/{path:path}")
async def proxy_private_file(
    bucket: str,
    path: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Stream a private MinIO file. Requires JWT. Only private buckets are served here."""
    if bucket not in PRIVATE_BUCKETS:
        raise HTTPException(status_code=403, detail="Only private bucket files are served via this endpoint")
    try:
        file_bytes = download_file(bucket, path)
        filename = path.split("/")[-1]
        return StreamingResponse(
            BytesIO(file_bytes),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"inline; filename={filename}"},
        )
    except Exception as e:
        logger("FILES", f"Proxy download failed [{bucket}/{path}]: {str(e)}", level="ERROR")
        raise HTTPException(status_code=404, detail="File not found")