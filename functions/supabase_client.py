import os
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
        "The supabase package is required to use Supabase storage. "
        "Install it with pip install supabase"
    ) from exc

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def upload_file(bucket: str, path: str, file_bytes: bytes, content_type: str = "application/pdf") -> dict:
    """Upload raw bytes to a Supabase storage bucket."""
    storage = supabase.storage.from_(bucket)
    try:
        # Upsert by deleting the old file first if it exists, then uploading fresh bytes.
        storage.remove([path])
    except Exception:
        pass

    response = storage.upload(path, file_bytes, file_options={"content-type": content_type})
    if getattr(response, "error", None):
        raise RuntimeError(f"Supabase upload failed: {response.error}")
    return response


def create_signed_url(bucket: str, path: str, expires_in: int = 3600) -> str:
    """Create a signed temporary URL for a private Supabase storage object."""
    storage = supabase.storage.from_(bucket)
    response = storage.create_signed_url(path, expires_in)
    if getattr(response, "error", None):
        raise RuntimeError(f"Supabase signed URL creation failed: {response.error}")
    signed_url = response.get("signedURL") or response.get("signedUrl")
    if not signed_url:
        raise RuntimeError("Supabase signed URL response did not contain a URL")
    return signed_url