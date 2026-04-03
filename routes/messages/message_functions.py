import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
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
    def get_all_messages(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all messages"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="message",
                columns=["message_id", "sender_id", "receiver_id", "contract_id", "message_text",
                        "is_read", "read_at", "sent_at"],
                order_by="sent_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("MESSAGE_FUNCTIONS", f"Fetched {len(rows)} messages", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching messages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_message_by_id(message_id: str) -> Optional[Dict]:
        """Fetch a message by ID"""
        try:
            db = get_db()
            conditions = [("message_id", "=", message_id)]
            rows = db.fetch_data(
                table_name="message",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("MESSAGE_FUNCTIONS", f"Message {message_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching message: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_messages_by_sender_id(sender_id: str) -> List[Dict]:
        """Fetch all messages sent by a user"""
        try:
            db = get_db()
            conditions = [("sender_id", "=", sender_id)]
            rows = db.fetch_data(
                table_name="message",
                conditions=conditions,
                order_by="sent_at DESC"
            )
            
            logger("MESSAGE_FUNCTIONS", f"Fetched {len(rows)} messages from sender {sender_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching messages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_messages_by_receiver_id(receiver_id: str) -> List[Dict]:
        """Fetch all messages received by a user"""
        try:
            db = get_db()
            conditions = [("receiver_id", "=", receiver_id)]
            rows = db.fetch_data(
                table_name="message",
                conditions=conditions,
                order_by="sent_at DESC"
            )
            
            logger("MESSAGE_FUNCTIONS", f"Fetched {len(rows)} messages for receiver {receiver_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching messages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_messages_by_contract_id(contract_id: str) -> List[Dict]:
        """Fetch all messages for a specific contract"""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(
                table_name="message",
                conditions=conditions,
                order_by="sent_at DESC"
            )
            
            logger("MESSAGE_FUNCTIONS", f"Fetched {len(rows)} messages for contract {contract_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error fetching messages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_message(sender_id: str, receiver_id: str, message_text: str,
                       contract_id: Optional[str] = None) -> Dict:
        """Create a new message"""
        try:
            db = get_db()
            message_id = str(uuid.uuid4())
            
            message_data = {
                "message_id": message_id,
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "contract_id": contract_id,
                "message_text": message_text,
                "is_read": False
            }
            
            db.insert_data(table_name="message", data=message_data)
            
            logger("MESSAGE_FUNCTIONS", f"Message {message_id} created", level="INFO")
            return convert_uuids_to_str(message_data)
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error creating message: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_message(message_id: str, update_data: Dict) -> Optional[Dict]:
        """Update message information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("MESSAGE_FUNCTIONS", "No data to update", level="WARNING")
                return MessageFunctions.get_message_by_id(message_id)
            
            conditions = [("message_id", "=", message_id)]
            db.update_data(table_name="message", data=update_data, conditions=conditions)
            
            logger("MESSAGE_FUNCTIONS", f"Message {message_id} updated", level="INFO")
            return MessageFunctions.get_message_by_id(message_id)
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error updating message: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_message(message_id: str) -> bool:
        """Delete a message"""
        try:
            db = get_db()
            conditions = [("message_id", "=", message_id)]
            db.delete_data(table_name="message", conditions=conditions)
            
            logger("MESSAGE_FUNCTIONS", f"Message {message_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("MESSAGE_FUNCTIONS", f"Error deleting message: {str(e)}", level="ERROR")
            raise
