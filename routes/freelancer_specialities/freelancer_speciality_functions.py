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


class FreelancerSpecialityFunctions:
    """Handle all freelancer speciality-related database operations"""

    @staticmethod
    def get_all_freelancer_specialities(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all freelancer specialities"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_speciality",
                columns=["freelancer_speciality_id", "freelancer_id", "speciality_id", "is_primary", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Fetched {len(rows)} freelancer specialities", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error fetching freelancer specialities: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_speciality_by_id(freelancer_speciality_id: str) -> Optional[Dict]:
        """Fetch a freelancer speciality by ID"""
        try:
            db = get_db()
            conditions = [("freelancer_speciality_id", "=", freelancer_speciality_id)]
            rows = db.fetch_data(
                table_name="freelancer_speciality",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Freelancer speciality {freelancer_speciality_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error fetching freelancer speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_specialities_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all specialities for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer_speciality",
                conditions=conditions,
                order_by="is_primary DESC"
            )
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Fetched {len(rows)} specialities for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error fetching freelancer specialities: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer_speciality(freelancer_id: str, speciality_id: str, 
                                     is_primary: Optional[bool] = False) -> Dict:
        """Create a new freelancer speciality"""
        try:
            db = get_db()
            freelancer_speciality_id = str(uuid.uuid4())
            
            freelancer_speciality_data = {
                "freelancer_speciality_id": freelancer_speciality_id,
                "freelancer_id": freelancer_id,
                "speciality_id": speciality_id,
                "is_primary": is_primary
            }
            
            db.insert_data(table_name="freelancer_speciality", data=freelancer_speciality_data)
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Freelancer speciality {freelancer_speciality_id} created", level="INFO")
            return convert_uuids_to_str(freelancer_speciality_data)
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error creating freelancer speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer_speciality(freelancer_speciality_id: str, update_data: Dict) -> Optional[Dict]:
        """Update freelancer speciality information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("FREELANCER_SPECIALITY_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerSpecialityFunctions.get_freelancer_speciality_by_id(freelancer_speciality_id)
            
            conditions = [("freelancer_speciality_id", "=", freelancer_speciality_id)]
            db.update_data(table_name="freelancer_speciality", data=update_data, conditions=conditions)
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Freelancer speciality {freelancer_speciality_id} updated", level="INFO")
            return FreelancerSpecialityFunctions.get_freelancer_speciality_by_id(freelancer_speciality_id)
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error updating freelancer speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_speciality(freelancer_speciality_id: str) -> bool:
        """Delete a freelancer speciality"""
        try:
            db = get_db()
            conditions = [("freelancer_speciality_id", "=", freelancer_speciality_id)]
            db.delete_data(table_name="freelancer_speciality", conditions=conditions)
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Freelancer speciality {freelancer_speciality_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error deleting freelancer speciality: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_speciality_by_freelancer_and_speciality(freelancer_id: str, speciality_id: str) -> bool:
        """Delete a freelancer speciality by freelancer_id and speciality_id"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id), ("speciality_id", "=", speciality_id)]
            db.delete_data(table_name="freelancer_speciality", conditions=conditions)
            
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Deleted speciality {speciality_id} from freelancer {freelancer_id}", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_SPECIALITY_FUNCTIONS", f"Error deleting freelancer speciality: {str(e)}", level="ERROR")
            raise
