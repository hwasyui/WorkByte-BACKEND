import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Optional


"""
Profile-id <-> user-id resolution for the review subsystem.

The review/trust tables key on freelancer.freelancer_id / client.client_id,
like the rest of the schema. A few things downstream need the users.user_id
behind that profile instead:

  * NotificationFunctions.notify(recipient_user_id=...)
  * dm_message.sender_id comparisons (responsiveness scoring)

These resolve one to the other without raising, so a background pipeline can
degrade gracefully rather than dying on a deleted profile. Use
functions/access_control.py for the request-scoped, HTTP-raising equivalents.
"""


def user_id_for_freelancer(freelancer_id: str) -> Optional[str]:
    """users.user_id behind a freelancer profile, or None if it no longer exists."""
    try:
        rows = get_db().execute_query(
            "SELECT user_id FROM freelancer WHERE freelancer_id = :fid",
            {"fid": freelancer_id},
        )
        return str(rows[0]["user_id"]) if rows else None
    except Exception as e:
        logger("PROFILE_IDS", f"Error resolving user_id for freelancer {freelancer_id}: {str(e)}", level="ERROR")
        return None


def user_id_for_client(client_id: str) -> Optional[str]:
    """users.user_id behind a client profile, or None if it no longer exists."""
    try:
        rows = get_db().execute_query(
            "SELECT user_id FROM client WHERE client_id = :cid",
            {"cid": client_id},
        )
        return str(rows[0]["user_id"]) if rows else None
    except Exception as e:
        logger("PROFILE_IDS", f"Error resolving user_id for client {client_id}: {str(e)}", level="ERROR")
        return None
