import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import json
import uuid

import httpx
from google.oauth2 import service_account
import google.auth.transport.requests

FCM_PROJECT_ID = os.getenv("FCM_PROJECT_ID")
FCM_SERVICE_ACCOUNT_FILE = os.getenv("FCM_SERVICE_ACCOUNT_FILE", "service-account.json")


def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    return {
        k: str(v) if hasattr(v, '__class__') and 'UUID' in v.__class__.__name__ else v
        for k, v in data.items()
    }


class NotificationFunctions:

    @staticmethod
    def _get_fcm_access_token() -> str:
        credentials = service_account.Credentials.from_service_account_file(
            FCM_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token

    @staticmethod
    async def send_fcm(token: str, title: str, body: str, data: dict):
        try:
            access_token = NotificationFunctions._get_fcm_access_token()
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"https://fcm.googleapis.com/v1/projects/{FCM_PROJECT_ID}/messages:send",
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "message": {
                            "token": token,
                            "notification": {"title": title, "body": body},
                            "data": {k: str(v) for k, v in data.items()},
                        }
                    },
                    timeout=5.0,
                )
            logger("NOTIFICATION_FUNCTIONS", f"FCM sent to token ...{token[-6:]}", level="INFO")
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"FCM send failed (non-fatal): {str(e)}", level="WARNING")

    @staticmethod
    async def notify(
        recipient_user_id: str,
        notif_type: str,
        title: str,
        body: str,
        data: dict = {},
    ):
        """Persist notification to DB + best-effort FCM push."""
        try:
            db = get_db()
            notification_id = str(uuid.uuid4())
            payload = {**data, "type": notif_type}

            db.insert_data(
                table_name="notifications",
                data={
                    "id": notification_id,
                    "recipient_id": recipient_user_id,
                    "type": notif_type,
                    "title": title,
                    "body": body,
                    "data": json.dumps(payload),
                    "is_read": False,
                },
            )
            logger("NOTIFICATION_FUNCTIONS", f"Notification saved for user {recipient_user_id}: {notif_type}", level="INFO")

            # Best-effort FCM; never block the main flow
            row = db.fetch_data(
                table_name="users",
                columns=["fcm_token"],
                conditions=[("user_id", "=", recipient_user_id)],
                limit=1,
            )
            if row and row[0].get("fcm_token"):
                await NotificationFunctions.send_fcm(row[0]["fcm_token"], title, body, payload)

        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"notify() failed (non-fatal): {str(e)}", level="WARNING")

    @staticmethod
    def get_notifications_by_user(
        user_id: str, limit: int = 20, offset: int = 0
    ) -> list:
        try:
            db = get_db()
            rows = db.execute_query(
                """
                SELECT id, recipient_id, type, title, body, data, is_read, created_at
                FROM notifications
                WHERE recipient_id = :uid
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """,
                {"uid": user_id, "limit": limit, "offset": offset},
            )
            logger("NOTIFICATION_FUNCTIONS", f"Fetched {len(rows)} notifications for user {user_id}", level="INFO")
            return rows or []
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"Error fetching notifications: {str(e)}", level="ERROR")
            raise
        
    @staticmethod
    def get_unread_count(user_id: str) -> int:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="notifications",
                columns=["id"],
                conditions=[("recipient_id", "=", user_id), ("is_read", "=", False)],
            )
            return len(rows)
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"Error fetching unread count: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def mark_as_read(notification_id: str, user_id: str) -> bool:
        try:
            db = get_db()
            db.update_data(
                table_name="notifications",
                data={"is_read": True},
                conditions=[("id", "=", notification_id), ("recipient_id", "=", user_id)],
            )
            logger("NOTIFICATION_FUNCTIONS", f"Notification {notification_id} marked as read", level="INFO")
            return True
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"Error marking notification as read: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def mark_all_read(user_id: str) -> bool:
        try:
            db = get_db()
            db.update_data(
                table_name="notifications",
                data={"is_read": True},
                conditions=[("recipient_id", "=", user_id), ("is_read", "=", False)],
            )
            logger("NOTIFICATION_FUNCTIONS", f"All notifications marked as read for user {user_id}", level="INFO")
            return True
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"Error marking all as read: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def save_fcm_token(user_id: str, token: str) -> bool:
        try:
            db = get_db()
            db.update_data(
                table_name="users",
                data={"fcm_token": token},
                conditions=[("user_id", "=", user_id)],
            )
            logger("NOTIFICATION_FUNCTIONS", f"FCM token saved for user {user_id}", level="INFO")
            return True
        except Exception as e:
            logger("NOTIFICATION_FUNCTIONS", f"Error saving FCM token: {str(e)}", level="ERROR")
            raise