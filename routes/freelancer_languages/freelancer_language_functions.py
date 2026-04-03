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


class FreelancerLanguageFunctions:
    """Handle all freelancer language-related database operations"""

    @staticmethod
    def get_all_freelancer_languages(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all freelancer languages"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_language",
                columns=["freelancer_language_id", "freelancer_id", "language_id", "proficiency_level", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Fetched {len(rows)} freelancer languages", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error fetching freelancer languages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_language_by_id(freelancer_language_id: str) -> Optional[Dict]:
        """Fetch a freelancer language by ID"""
        try:
            db = get_db()
            conditions = [("freelancer_language_id", "=", freelancer_language_id)]
            rows = db.fetch_data(
                table_name="freelancer_language",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Freelancer language {freelancer_language_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error fetching freelancer language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_languages_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all languages for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer_language",
                conditions=conditions,
                order_by="proficiency_level DESC"
            )
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Fetched {len(rows)} languages for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error fetching freelancer languages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer_language(freelancer_id: str, language_id: str, 
                                   proficiency_level: str) -> Dict:
        """Create a new freelancer language"""
        try:
            db = get_db()
            freelancer_language_id = str(uuid.uuid4())
            
            freelancer_language_data = {
                "freelancer_language_id": freelancer_language_id,
                "freelancer_id": freelancer_id,
                "language_id": language_id,
                "proficiency_level": proficiency_level
            }
            
            db.insert_data(table_name="freelancer_language", data=freelancer_language_data)
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Freelancer language {freelancer_language_id} created", level="INFO")
            return convert_uuids_to_str(freelancer_language_data)
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error creating freelancer language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer_language(freelancer_language_id: str, update_data: Dict) -> Optional[Dict]:
        """Update freelancer language information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("FREELANCER_LANGUAGE_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerLanguageFunctions.get_freelancer_language_by_id(freelancer_language_id)
            
            conditions = [("freelancer_language_id", "=", freelancer_language_id)]
            db.update_data(table_name="freelancer_language", data=update_data, conditions=conditions)
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Freelancer language {freelancer_language_id} updated", level="INFO")
            return FreelancerLanguageFunctions.get_freelancer_language_by_id(freelancer_language_id)
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error updating freelancer language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_language(freelancer_language_id: str) -> bool:
        """Delete a freelancer language"""
        try:
            db = get_db()
            conditions = [("freelancer_language_id", "=", freelancer_language_id)]
            db.delete_data(table_name="freelancer_language", conditions=conditions)
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Freelancer language {freelancer_language_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error deleting freelancer language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_language_by_freelancer_and_language(freelancer_id: str, language_id: str) -> bool:
        """Delete a freelancer language by freelancer_id and language_id"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id), ("language_id", "=", language_id)]
            db.delete_data(table_name="freelancer_language", conditions=conditions)
            
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Deleted language {language_id} from freelancer {freelancer_id}", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_LANGUAGE_FUNCTIONS", f"Error deleting freelancer language: {str(e)}", level="ERROR")
            raise
