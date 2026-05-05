import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from typing import Optional, Dict


def _classify_file_type(mime_type: str, is_voice_note: bool = False) -> str:
    if is_voice_note:
        return "voice_note"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


def get_contract_by_id(contract_id: str) -> Optional[Dict]:
    db   = get_db()
    rows = db.fetch_data("contract", conditions=[("contract_id", "=", contract_id)], limit=1)
    if not rows:
        return None
    row = dict(rows[0])
    return {k: str(v) if hasattr(v, '__class__') and 'UUID' in type(v).__name__ else v
            for k, v in row.items()}
