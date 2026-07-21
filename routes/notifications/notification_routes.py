import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional
from functions.schema_model import UserInDB, FCMTokenUpdate, NotificationResponse
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.notifications.notification_functions import NotificationFunctions


notification_router = APIRouter(prefix="/notifications", tags=["Notifications"])


@notification_router.get("", response_model=None)
async def get_notifications(
    limit: Optional[int] = 20,
    offset: int = 0,
    current_user: UserInDB = Depends(get_current_user),
):
    """Get paginated notifications for the current user."""
    try:
        notifications = NotificationFunctions.get_notifications_by_user(
            user_id=str(current_user.user_id),
            limit=limit,
            offset=offset,
        )
        logger("NOTIFICATION", f"Fetched {len(notifications)} notifications for user {current_user.user_id}", "GET /notifications", "INFO")
        return ResponseSchema.success(notifications, 200)
    except Exception as e:
        error_msg = f"Failed to fetch notifications: {str(e)}"
        logger("NOTIFICATION", error_msg, "GET /notifications", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@notification_router.get("/unread-count")
async def get_unread_count(current_user: UserInDB = Depends(get_current_user)):
    """Get unread notification count for the bell badge."""
    try:
        count = NotificationFunctions.get_unread_count(str(current_user.user_id))
        logger("NOTIFICATION", f"Unread count for user {current_user.user_id}: {count}", "GET /notifications/unread-count", "INFO")
        return ResponseSchema.success({"count": count}, 200)
    except Exception as e:
        error_msg = f"Failed to fetch unread count: {str(e)}"
        logger("NOTIFICATION", error_msg, "GET /notifications/unread-count", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# NOTE: /read-all must be declared BEFORE /{notification_id}/read
# to prevent FastAPI from matching "read-all" as a notification_id
@notification_router.patch("/read-all")
async def mark_all_read(current_user: UserInDB = Depends(get_current_user)):
    """Mark all notifications as read."""
    try:
        NotificationFunctions.mark_all_read(str(current_user.user_id))
        logger("NOTIFICATION", f"All notifications marked as read for user {current_user.user_id}", "PATCH /notifications/read-all", "INFO")
        return ResponseSchema.success("All marked as read", 200)
    except Exception as e:
        error_msg = f"Failed to mark all as read: {str(e)}"
        logger("NOTIFICATION", error_msg, "PATCH /notifications/read-all", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@notification_router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Mark a single notification as read."""
    try:
        NotificationFunctions.mark_as_read(notification_id, str(current_user.user_id))
        logger("NOTIFICATION", f"Notification {notification_id} marked as read", "PATCH /notifications/{id}/read", "INFO")
        return ResponseSchema.success("Marked as read", 200)
    except Exception as e:
        error_msg = f"Failed to mark notification as read: {str(e)}"
        logger("NOTIFICATION", error_msg, "PATCH /notifications/{id}/read", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@notification_router.put("/fcm-token")
async def save_fcm_token(
    payload: FCMTokenUpdate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Save or update the FCM device token for push notifications."""
    try:
        NotificationFunctions.save_fcm_token(str(current_user.user_id), payload.token)
        logger("NOTIFICATION", f"FCM token saved for user {current_user.user_id}", "PUT /notifications/fcm-token", "INFO")
        return ResponseSchema.success("FCM token saved", 200)
    except Exception as e:
        error_msg = f"Failed to save FCM token: {str(e)}"
        logger("NOTIFICATION", error_msg, "PUT /notifications/fcm-token", "ERROR")
        return ResponseSchema.error(error_msg, 500)