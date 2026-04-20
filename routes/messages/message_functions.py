import os
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
from datetime import datetime
import uuid


def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings"""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


class MessageFunctions:
    """Handle all message-related database operations"""

    @staticmethod
    def get_message_by_id(message_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            conditions = [("message_id", "=", message_id)]
            rows = db.fetch_data(
                table_name="message",
                conditions=conditions,
                limit=1,
            )
            if rows:
                logger("MESSAGE_FUNCTIONS", f"Message {message_id} found", level="INFO")
                message = convert_uuids_to_str(dict(rows[0]))
                if isinstance(message.get("metadata"), str):
                    try:
                        message["metadata"] = json.loads(message["metadata"])
                    except Exception:
                        pass
                return message
            return None
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching message: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contract_by_id(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(
                table_name="contract",
                conditions=conditions,
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_messages_by_contract_id(contract_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="message",
                columns=[
                    "message_id",
                    "sender_id",
                    "receiver_id",
                    "contract_id",
                    "message_text",
                    "message_type",
                    "event_type",
                    "metadata",
                    "is_read",
                    "read_at",
                    "sent_at",
                ],
                conditions=[
                    ("contract_id", "=", contract_id),
                ],
                order_by="sent_at ASC",
            )
            logger("MESSAGE_FUNCTIONS", f"Fetched {len(rows)} messages for contract {contract_id}", level="INFO")
            messages = []
            for row in rows:
                item = convert_uuids_to_str(dict(row))
                if isinstance(item.get("metadata"), str):
                    try:
                        item["metadata"] = json.loads(item["metadata"])
                    except Exception:
                        pass
                messages.append(item)
            return messages
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching messages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_message(
        sender_id: str,
        receiver_id: str,
        contract_id: str,
        message_text: str,
        message_type: str = "user",
        event_type: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        try:
            db = get_db()
            message_id = str(uuid.uuid4())

            message_data = {
                "message_id": message_id,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "contract_id": contract_id,
                "message_text": message_text.strip(),
                "message_type": message_type,
                "event_type": event_type,
                "metadata": json.dumps(metadata) if metadata is not None else None,
                "is_read": False,
            }

            db.insert_data(table_name="message", data=message_data)
            logger("MESSAGE_FUNCTIONS", f"Message {message_id} created", level="INFO")

            return MessageFunctions.get_message_by_id(message_id)
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error creating message: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def mark_messages_as_read(contract_id: str, user_id: str) -> int:
        try:
            db = get_db()
            query = """
                UPDATE message
                SET is_read = TRUE,
                    read_at = NOW()
                WHERE contract_id = :contract_id
                  AND receiver_id = :user_id
                  AND is_read = FALSE
            """
            db.execute_query(query, {
                "contract_id": contract_id,
                "user_id": user_id,
            })
            logger("MESSAGE_FUNCTIONS", f"Marked messages as read for user {user_id} in contract {contract_id}", level="INFO")

            count_query = """
                SELECT COUNT(*) AS total
                FROM message
                WHERE contract_id = :contract_id
                  AND receiver_id = :user_id
                  AND is_read = TRUE
            """
            result = db.execute_query(count_query, {
                "contract_id": contract_id,
                "user_id": user_id,
            })

            if result and len(result) > 0:
                return int(result[0]["total"])
            return 0
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error marking messages as read: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_system_message(
        actor_user_id: str,
        contract_id: str,
        message_text: str,
        event_type: str,
        metadata: Optional[Dict] = None,
    ) -> Dict:
        try:
            contract = MessageFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise Exception("Contract not found")

            db = get_db()

            # Resolve actor → check if they're the client or freelancer by looking up both profiles
            client_rows = db.fetch_data("client", conditions=[("client_id", "=", str(contract["client_id"]))], limit=1)
            freelancer_rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", str(contract["freelancer_id"]))], limit=1)

            if not client_rows or not freelancer_rows:
                raise Exception("Could not resolve contract parties")

            client_user_id = str(client_rows[0]["user_id"])
            freelancer_user_id = str(freelancer_rows[0]["user_id"])

            if str(actor_user_id) == client_user_id:
                receiver_id = freelancer_user_id
            elif str(actor_user_id) == freelancer_user_id:
                receiver_id = client_user_id
            else:
                raise Exception("User is not part of this contract")

            return MessageFunctions.create_message(
                sender_id=actor_user_id,
                receiver_id=receiver_id,
                contract_id=contract_id,
                message_text=message_text,
                message_type="system",
                event_type=event_type,
                metadata=metadata,
            )
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error creating system message: {str(e)}", level="ERROR")
            raise