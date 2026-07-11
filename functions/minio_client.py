import os
import json
import mimetypes
from io import BytesIO
from fastapi import HTTPException
from minio import Minio
from minio.error import S3Error
from dotenv import load_dotenv

load_dotenv()

MAX_UPLOAD_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB - fallback for the generic upload route, which has no specific use case


def validate_file_size(contents: bytes, file_name: str = "file") -> None:
    if len(contents) > MAX_UPLOAD_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {file_name}. Max size is 100 MB.",
        )


_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "application/zip",
    "image/jpeg",
    "image/png",
}
_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}

# Per-use-case allowlist + size cap. Use cases with no entry here fall back to
# MAX_UPLOAD_FILE_SIZE_BYTES and no MIME restriction (e.g. the generic upload route).
ALLOWED_MIME_TYPES = {
    "cv":             {"application/pdf", "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "image/jpeg", "image/png", "image/bmp", "image/tiff"},
    "job_file":       _DOCUMENT_MIME_TYPES,
    "proposal_file":  _DOCUMENT_MIME_TYPES,
    "avatar":         _IMAGE_MIME_TYPES,
    "appeal_proof":   {"application/pdf", "image/jpeg", "image/png"},
}

MAX_UPLOAD_FILE_SIZE_BYTES_BY_USE_CASE = {
    "cv":             10 * 1024 * 1024,
    "job_file":       25 * 1024 * 1024,
    "proposal_file":  25 * 1024 * 1024,
    "avatar":         5 * 1024 * 1024,
    "appeal_proof":   20 * 1024 * 1024,
}


def validate_upload(use_case: str, contents: bytes, mime_type: str, file_name: str = "file") -> None:
    """Enforce a per-use-case size cap and MIME allowlist. Falls back to the flat
    100MB cap and no MIME restriction for use cases not in the maps above."""
    max_size = MAX_UPLOAD_FILE_SIZE_BYTES_BY_USE_CASE.get(use_case, MAX_UPLOAD_FILE_SIZE_BYTES)
    if len(contents) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large: {file_name}. Max size for this upload type is {max_size // (1024 * 1024)} MB.",
        )
    allowed = ALLOWED_MIME_TYPES.get(use_case)
    if allowed and mime_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{mime_type}' for {file_name}. Allowed types: {', '.join(sorted(allowed))}.",
        )


MINIO_ENDPOINT        = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY      = os.getenv("MINIO_ACCESS_KEY", "capstone")
MINIO_SECRET_KEY      = os.getenv("MINIO_SECRET_KEY", "capstone")
MINIO_SECURE          = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_PUBLIC_BASE     = os.getenv("MINIO_PUBLIC_BASE_URL", "http://localhost:9000").rstrip("/")
BACKEND_PUBLIC_URL    = os.getenv("BACKEND_PUBLIC_URL", "http://localhost:8000").rstrip("/")

_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)

PRIVATE_BUCKETS = {"contract-assets", "contract-submissions", "message-attachments", "proposal-files", "cv-files", "appeal-proofs"}

BUCKET_JOB_FILES            = "job-files"
BUCKET_PROPOSAL_FILES       = "proposal-files"
BUCKET_USER_ASSETS          = "user-assets"
BUCKET_CONTRACT_SUBMISSIONS = "contract-submissions"
BUCKET_MESSAGE_ATTACHMENTS  = "message-attachments"
BUCKET_CV_FILES             = "cv-files"
BUCKET_APPEAL_PROOFS        = "appeal-proofs"

BUCKET_MAP = {
    "job-files":            BUCKET_JOB_FILES,
    "proposal-files":       BUCKET_PROPOSAL_FILES,
    "user-assets":          BUCKET_USER_ASSETS,
    "contract-submissions": BUCKET_CONTRACT_SUBMISSIONS,
    "message-attachments":  BUCKET_MESSAGE_ATTACHMENTS,
    "cv-files":             BUCKET_CV_FILES,
    "appeal-proofs":        BUCKET_APPEAL_PROOFS,
}


_ALL_BUCKETS = [
    "user-assets",
    "job-files",
    "proposal-files",
    "contract-submissions",
    "message-attachments",
    "contract-assets",
    "cv-files",
    "appeal-proofs",
]
_PUBLIC_READ_BUCKETS = {"user-assets", "job-files"}


def ensure_buckets() -> None:
    for bucket in _ALL_BUCKETS:
        if not _client.bucket_exists(bucket):
            _client.make_bucket(bucket)
        if bucket in _PUBLIC_READ_BUCKETS:
            policy = json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }],
            })
            _client.set_bucket_policy(bucket, policy)


def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(filename)[0] or fallback


def upload_file(
    bucket: str,
    path: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> str:
    """Upload bytes to a bucket. Returns public URL for public buckets, storage path for private buckets."""
    try:
        _client.put_object(
            bucket, path, BytesIO(file_bytes), len(file_bytes),
            content_type=content_type,
        )
    except S3Error as e:
        raise RuntimeError(f"MinIO upload failed [{bucket}/{path}]: {e}")

    if bucket in PRIVATE_BUCKETS:
        return path
    return f"{MINIO_PUBLIC_BASE}/{bucket}/{path}"


def delete_file(bucket: str, path: str) -> None:
    """Delete a file. Accepts either a full URL or a raw storage path."""
    if path.startswith("http"):
        marker = f"/{bucket}/"
        if marker in path:
            path = path.split(marker, 1)[1]
    try:
        _client.remove_object(bucket, path)
    except S3Error as e:
        raise RuntimeError(f"MinIO delete failed [{bucket}/{path}]: {e}")


def download_file(bucket: str, path: str) -> bytes:
    """Download and return file bytes from any bucket."""
    response = None
    try:
        response = _client.get_object(bucket, path)
        return response.read()
    except S3Error as e:
        raise RuntimeError(f"MinIO download failed [{bucket}/{path}]: {e}")
    finally:
        if response:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def get_file_proxy_url(bucket: str, path: str) -> str:
    """Return the backend-proxied URL for a private file. Flutter must send JWT to access it."""
    return f"{BACKEND_PUBLIC_URL}/files/{bucket}/{path}"


def create_signed_url(bucket: str, path: str, expires_in: int = 3600) -> str:
    """Compatibility shim used by contract_generation_functions. Returns a backend proxy URL."""
    return get_file_proxy_url(bucket, path)


def resolve_file_url(bucket: str, stored_value: str) -> str:
    """Convert a stored DB value to a usable URL for Flutter.
    - Already a URL: return as-is
    - Raw path in a private bucket: return backend proxy URL
    - Raw path in a public bucket: return public MinIO URL
    """
    if not stored_value or stored_value.startswith("http"):
        return stored_value
    if bucket in PRIVATE_BUCKETS:
        return get_file_proxy_url(bucket, stored_value)
    return f"{MINIO_PUBLIC_BASE}/{bucket}/{stored_value}"

def upload_proposal_file(proposal_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_PROPOSAL_FILES,
        path=f"{proposal_id}/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )


def upload_job_file(job_post_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_JOB_FILES,
        path=f"{job_post_id}/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )


def upload_cv_file(path: str, file_bytes: bytes, content_type: str = None) -> str:
    """Resolves to a full URL here (unlike other private buckets, which return a
    raw path) so cv_file_url stays a drop-in value for existing callers."""
    stored = upload_file(
        bucket=BUCKET_CV_FILES,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(path),
    )
    return resolve_file_url(BUCKET_CV_FILES, stored)

def upload_freelancer_profile_picture(freelancer_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    ext = file_name.split(".")[-1] if "." in file_name else "jpg"
    path = f"avatars/{freelancer_id}.{ext}"
    return upload_file(
        bucket=BUCKET_USER_ASSETS,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )

def upload_client_profile_picture(client_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    ext = file_name.split(".")[-1] if "." in file_name else "jpg"
    path = f"avatars/{client_id}.{ext}"
    return upload_file(
        bucket=BUCKET_USER_ASSETS,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )

def upload_contract_submission_file(contract_id: str, submission_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_CONTRACT_SUBMISSIONS,
        path=f"{contract_id}/{submission_id}/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )

def upload_thread_attachment(thread_id: str, message_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_MESSAGE_ATTACHMENTS,
        path=f"{thread_id}/{message_id}/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )

def upload_appeal_proof_file(appeal_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    """Resolves to a full URL here (like upload_cv_file) so proof_file_url stays
    a drop-in value readable straight off the appeals row, no extra resolve step
    needed in every appeal-listing endpoint."""
    stored = upload_file(
        bucket=BUCKET_APPEAL_PROOFS,
        path=f"{appeal_id}/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )
    return resolve_file_url(BUCKET_APPEAL_PROOFS, stored)
