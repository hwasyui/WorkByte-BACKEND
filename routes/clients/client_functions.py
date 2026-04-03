import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid
from datetime import datetime

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


class ClientFunctions:
    """Handle all client-related database operations"""

    @staticmethod
    def get_all_clients(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all clients"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client",
                columns=["client_id", "user_id", "full_name", "bio", "website_url", "profile_picture_url", 
                        "total_jobs_posted", "total_projects_completed", 
                        "average_rating_given", "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit
            )
            
            logger("CLIENT_FUNCTIONS", f"Fetched {len(rows)} clients", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching clients: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_by_id(client_id: str) -> Optional[Dict]:
        """Fetch a single client by ID"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="client",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("CLIENT_FUNCTIONS", f"Client {client_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_by_user_id(user_id: str) -> Optional[Dict]:
        """Fetch a client by user ID"""
        try:
            db = get_db()
            conditions = [("user_id", "=", user_id)]
            rows = db.fetch_data(
                table_name="client",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("CLIENT_FUNCTIONS", f"Client for user {user_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client by user_id: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_by_id_or_user_id(identifier: str) -> Optional[Dict]:
        """Fetch a client by either client_id or user_id"""
        try:
            # Try client_id first
            result = ClientFunctions.get_client_by_id(identifier)
            if result:
                return result
            
            # Try user_id as fallback
            result = ClientFunctions.get_client_by_user_id(identifier)
            if result:
                return result
            
            return None
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_client(client_id: str, user_id: str, full_name: Optional[str] = None,
                     bio: Optional[str] = None, website_url: Optional[str] = None,
                     profile_picture_url: Optional[str] = None) -> Dict:
        """Create a new client profile"""
        try:
            db = get_db()
            client_id = str(uuid.uuid4())
            
            client_data = {
                "client_id": client_id,
                "user_id": user_id,
                "full_name": full_name,
                "bio": bio,
                "website_url": website_url,
                "profile_picture_url": profile_picture_url,
                "total_jobs_posted": 0,
                "total_projects_completed": 0
            }
            
            db.insert_data(table_name="client", data=client_data)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} created", level="INFO")
            return convert_uuids_to_str(client_data)
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error creating client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_client(client_id: str, update_data: Dict) -> Optional[Dict]:
        """Update client information"""
        try:
            db = get_db()
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("CLIENT_FUNCTIONS", "No data to update", level="WARNING")
                return ClientFunctions.get_client_by_id(client_id)
            
            conditions = [("client_id", "=", client_id)]
            db.update_data(table_name="client", data=update_data, conditions=conditions)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} updated", level="INFO")
            return ClientFunctions.get_client_by_id(client_id)
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error updating client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_client(client_id: str) -> bool:
        """Delete a client profile"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            db.delete_data(table_name="client", conditions=conditions)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error deleting client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_clients_by_full_name(search_term: str) -> List[Dict]:
        """Search clients by full name"""
        try:
            db = get_db()
            query = "SELECT * FROM client WHERE full_name ILIKE '%' || :search_term || '%' ORDER BY created_at DESC"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("CLIENT_FUNCTIONS", f"Found {len(rows)} clients matching '{search_term}'", level="INFO")
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error searching clients: {str(e)}", level="ERROR")
            raise
