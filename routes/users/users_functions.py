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


class UserFunctions:
    """Handle all user-related database operations"""

    @staticmethod
    def get_all_users(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all users with optional pagination"""
        try:
            db = get_db()
            conditions = None
            order_by = "created_at DESC"
            
            rows = db.fetch_data(
                table_name="users",
                columns=["user_id", "email", "type", "created_at", "updated_at"],
                conditions=conditions,
                limit=limit,
                order_by=order_by
            )
            
            logger("USERS_FUNCTIONS", f"Fetched {len(rows)} users", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error fetching all users: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[Dict]:
        """Fetch a single user by ID"""
        try:
            db = get_db()
            conditions = [("user_id", "=", user_id)]
            rows = db.fetch_data(
                table_name="users",
                columns=["user_id", "email", "type", "created_at", "updated_at"],
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("USERS_FUNCTIONS", f"User {user_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            logger("USERS_FUNCTIONS", f"User {user_id} not found", level="WARNING")
            return None
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error fetching user {user_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_user_by_email(email: str) -> Optional[Dict]:
        """Fetch a single user by email"""
        try:
            db = get_db()
            conditions = [("email", "=", email)]
            rows = db.fetch_data(
                table_name="users",
                columns=["user_id", "email", "password", "type", "created_at", "updated_at"],
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("USERS_FUNCTIONS", f"User with email {email} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error fetching user by email: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_user(user_id: str, email: str, password: str, user_type: str = "freelancer") -> Dict:
        """Create a new user and auto-create freelancer/client profile"""
        try:
            db = get_db()
            
            user_data = {
                "user_id": user_id,
                "email": email,
                "password": password,
                "type": user_type
            }
            
            db.insert_data(table_name="users", data=user_data)
            
            # Auto-create freelancer or client profile
            if user_type == "freelancer":
                from routes.freelancers.freelancer_functions import FreelancerFunctions
                FreelancerFunctions.create_freelancer(
                    freelancer_id=str(uuid.uuid4()),
                    user_id=user_id,
                    full_name=email.split('@')[0],
                    bio=None,
                    create_embedding=False
                )
            elif user_type == "client":
                from routes.clients.client_functions import ClientFunctions
                ClientFunctions.create_client(
                    client_id=str(uuid.uuid4()),
                    user_id=user_id,
                    company_name=None
                )
            
            logger("USERS_FUNCTIONS", f"User {email} created with ID {user_id} (type: {user_type})", level="INFO")
            return user_data
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error creating user: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_user(user_id: str, update_data: Dict) -> Optional[Dict]:
        """Update user information"""
        try:
            db = get_db()
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("USERS_FUNCTIONS", "No data to update", level="WARNING")
                return UserFunctions.get_user_by_id(user_id)
            
            conditions = [("user_id", "=", user_id)]
            db.update_data(table_name="users", data=update_data, conditions=conditions)
            
            logger("USERS_FUNCTIONS", f"User {user_id} updated", level="INFO")
            return UserFunctions.get_user_by_id(user_id)
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error updating user {user_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_user(user_id: str) -> bool:
        """Delete a user (cascades to freelancer/client profiles)"""
        try:
            db = get_db()
            conditions = [("user_id", "=", user_id)]
            db.delete_data(table_name="users", conditions=conditions)
            
            logger("USERS_FUNCTIONS", f"User {user_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error deleting user {user_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_users(search_term: str) -> List[Dict]:
        """Search users by email"""
        try:
            db = get_db()
            query = f"SELECT user_id, email, type, created_at, updated_at FROM users WHERE email ILIKE '%' || :search_term || '%'"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("USERS_FUNCTIONS", f"Search found {len(rows)} users", level="INFO")
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger("USERS_FUNCTIONS", f"Error searching users: {str(e)}", level="ERROR")
            raise
