import os
import mimetypes
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")

try:
    from supabase import create_client
except ImportError as exc:
    raise ImportError(
        "The supabase package is required. Install it with: pip install supabase"
    ) from exc

# ── Single shared client ──────────────────────────────────────────────────────
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Bucket names ──────────────────────────────────────────────────────────────
BUCKET_JOB_FILES       = os.getenv("SUPABASE_STORAGE_BUCKET", "job-files")
BUCKET_PROPOSAL_FILES  = "proposal-files"
BUCKET_USER_ASSETS     = "user-assets"

BUCKET_MAP = {
    "job-files":       BUCKET_JOB_FILES,
    "proposal-files":  BUCKET_PROPOSAL_FILES,
    "user-assets":     BUCKET_USER_ASSETS,
}


# ── Core helpers ──────────────────────────────────────────────────────────────

def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    """Guess MIME type from filename."""
    return mimetypes.guess_type(filename)[0] or fallback


def upload_file(
    bucket: str,
    path: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
    upsert: bool = True,
) -> str:
    """Upload bytes to any bucket — returns public URL."""
    storage = supabase.storage.from_(bucket)

    if upsert:
        try:
            storage.remove([path])
        except Exception:
            pass  # file didn't exist yet, that's fine

    response = storage.upload(path, file_bytes, file_options={"content-type": content_type})
    if getattr(response, "error", None):
        raise RuntimeError(f"Supabase upload failed [{bucket}/{path}]: {response.error}")

    return supabase.storage.from_(bucket).get_public_url(path)


def delete_file(bucket: str, path: str) -> None:
    """Delete a file from any bucket."""
    supabase.storage.from_(bucket).remove([path])


def create_signed_url(bucket: str, path: str, expires_in: int = 3600) -> str:
    """Create a signed temporary URL for a private storage object."""
    storage = supabase.storage.from_(bucket)
    response = storage.create_signed_url(path, expires_in)
    if getattr(response, "error", None):
        raise RuntimeError(f"Supabase signed URL failed: {response.error}")
    signed_url = response.get("signedURL") or response.get("signedUrl")
    if not signed_url:
        raise RuntimeError("Signed URL response did not contain a URL")
    return signed_url


# ── Per-bucket upload helpers ─────────────────────────────────────────────────

def upload_proposal_file(proposal_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_PROPOSAL_FILES,
        path=f"{proposal_id}/files/{file_name}",
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )


def upload_job_file(job_post_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    return upload_file(
        bucket=BUCKET_JOB_FILES,
        path=f"{job_post_id}/files/{file_name}",
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
    ext = file_name.split('.')[-1] if '.' in file_name else 'jpg'
    path = f"avatars/{freelancer_id}.{ext}"
    return upload_file(
        bucket=BUCKET_USER_ASSETS,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )


def upload_client_profile_picture(client_id: str, file_name: str, file_bytes: bytes, content_type: str = None) -> str:
    ext = file_name.split('.')[-1] if '.' in file_name else 'jpg'
    path = f"avatars/{client_id}.{ext}"
    return upload_file(
        bucket=BUCKET_USER_ASSETS,
        path=path,
        file_bytes=file_bytes,
        content_type=content_type or guess_mime(file_name),
    )
