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


class SpecialityFunctions:
    """Handle all speciality-related database operations"""

    @staticmethod
    def get_all_specialities(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all specialities"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="speciality",
                columns=["speciality_id", "speciality_name", "description", "created_at"],
                order_by="speciality_name ASC",
                limit=limit
            )
            
            logger("SPECIALITY_FUNCTIONS", f"Fetched {len(rows)} specialities", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error fetching specialities: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_speciality_by_id(speciality_id: str) -> Optional[Dict]:
        """Fetch a single speciality by ID"""
        try:
            db = get_db()
            conditions = [("speciality_id", "=", speciality_id)]
            rows = db.fetch_data(
                table_name="speciality",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("SPECIALITY_FUNCTIONS", f"Speciality {speciality_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error fetching speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_speciality_by_name(speciality_name: str) -> Optional[Dict]:
        """Fetch a speciality by name"""
        try:
            db = get_db()
            conditions = [("speciality_name", "=", speciality_name)]
            rows = db.fetch_data(
                table_name="speciality",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error fetching speciality by name: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_speciality(speciality_name: str, description: Optional[str] = None) -> Dict:
        """Create a new speciality"""
        try:
            db = get_db()
            # Check if speciality already exists
            existing = SpecialityFunctions.get_speciality_by_name(speciality_name)
            if existing:
                logger("SPECIALITY_FUNCTIONS", f"Speciality {speciality_name} already exists", level="WARNING")
                raise ValueError(f"Speciality '{speciality_name}' already exists")
            
            speciality_id = str(uuid.uuid4())
            
            speciality_data = {
                "speciality_id": speciality_id,
                "speciality_name": speciality_name,
                "description": description
            }
            
            db.insert_data(table_name="speciality", data=speciality_data)
            
            logger("SPECIALITY_FUNCTIONS", f"Speciality {speciality_id} created: {speciality_name}", level="INFO")
            return convert_uuids_to_str(speciality_data)
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error creating speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_speciality(speciality_id: str, update_data: Dict) -> Optional[Dict]:
        """Update speciality information"""
        try:
            db = get_db()
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("SPECIALITY_FUNCTIONS", "No data to update", level="WARNING")
                return SpecialityFunctions.get_speciality_by_id(speciality_id)
            
            # If updating speciality_name, check for duplicates
            if "speciality_name" in update_data:
                existing = SpecialityFunctions.get_speciality_by_name(update_data["speciality_name"])
                if existing and existing["speciality_id"] != speciality_id:
                    raise ValueError(f"Speciality name '{update_data['speciality_name']}' already exists")
            
            conditions = [("speciality_id", "=", speciality_id)]
            db.update_data(table_name="speciality", data=update_data, conditions=conditions)
            
            logger("SPECIALITY_FUNCTIONS", f"Speciality {speciality_id} updated", level="INFO")
            return SpecialityFunctions.get_speciality_by_id(speciality_id)
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error updating speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_speciality(speciality_id: str) -> bool:
        """Delete a speciality"""
        try:
            db = get_db()
            conditions = [("speciality_id", "=", speciality_id)]
            db.delete_data(table_name="speciality", conditions=conditions)
            
            logger("SPECIALITY_FUNCTIONS", f"Speciality {speciality_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error deleting speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_specialities_by_name(search_term: str) -> List[Dict]:
        """Search specialities by name"""
        try:
            db = get_db()
            query = "SELECT * FROM speciality WHERE speciality_name ILIKE '%' || :search_term || '%' ORDER BY speciality_name ASC"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("SPECIALITY_FUNCTIONS", f"Found {len(rows)} specialities matching '{search_term}'", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("SPECIALITY_FUNCTIONS", f"Error searching specialities: {str(e)}", level="ERROR")
            raise
