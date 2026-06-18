import os
import mimetypes
from io import BytesIO
from minio import Minio
from minio.error import S3Error
from dotenv import load_dotenv

load_dotenv()

MINIO_ENDPOINT        = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY      = os.getenv("MINIO_ACCESS_KEY", "capstone")
MINIO_SECRET_KEY      = os.getenv("MINIO_SECRET_KEY", "capstone")
MINIO_SECURE          = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_PUBLIC_BASE     = os.getenv("MINIO_PUBLIC_BASE_URL", "https://workbyte.angelica-whiharto.com/storage")
BACKEND_PUBLIC_URL    = os.getenv("BACKEND_PUBLIC_URL", "https://workbyte.angelica-whiharto.com").rstrip("/")

_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE,
)

PRIVATE_BUCKETS = {"contract-assets", "contract-submissions", "message-attachments", "proposal-files"}

BUCKET_JOB_FILES            = "job-files"
BUCKET_PROPOSAL_FILES       = "proposal-files"
BUCKET_USER_ASSETS          = "user-assets"
BUCKET_CONTRACT_SUBMISSIONS = "contract-submissions"
BUCKET_MESSAGE_ATTACHMENTS  = "message-attachments"

BUCKET_MAP = {
    "job-files":            BUCKET_JOB_FILES,
    "proposal-files":       BUCKET_PROPOSAL_FILES,
    "user-assets":          BUCKET_USER_ASSETS,
    "contract-submissions": BUCKET_CONTRACT_SUBMISSIONS,
    "message-attachments":  BUCKET_MESSAGE_ATTACHMENTS,
}


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
    return upload_file(
        bucket=BUCKET_USER_ASSETS,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(path),
    )

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
