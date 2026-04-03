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


class FreelancerSkillFunctions:
    """Handle all freelancer skill-related database operations"""

    @staticmethod
    def get_all_freelancer_skills(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all freelancer skills"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_skill",
                columns=["freelancer_skill_id", "freelancer_id", "skill_id", "proficiency_level", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Fetched {len(rows)} freelancer skills", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error fetching freelancer skills: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_skill_by_id(freelancer_skill_id: str) -> Optional[Dict]:
        """Fetch a freelancer skill by ID"""
        try:
            db = get_db()
            conditions = [("freelancer_skill_id", "=", freelancer_skill_id)]
            rows = db.fetch_data(
                table_name="freelancer_skill",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_SKILL_FUNCTIONS", f"Freelancer skill {freelancer_skill_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error fetching freelancer skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_skills_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all skills for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer_skill",
                conditions=conditions,
                order_by="proficiency_level DESC"
            )
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Fetched {len(rows)} skills for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error fetching freelancer skills: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer_skill(freelancer_id: str, skill_id: str, 
                               proficiency_level: Optional[str] = None) -> Dict:
        """Create a new freelancer skill"""
        try:
            db = get_db()
            freelancer_skill_id = str(uuid.uuid4())
            
            freelancer_skill_data = {
                "freelancer_skill_id": freelancer_skill_id,
                "freelancer_id": freelancer_id,
                "skill_id": skill_id,
                "proficiency_level": proficiency_level
            }
            
            db.insert_data(table_name="freelancer_skill", data=freelancer_skill_data)
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Freelancer skill {freelancer_skill_id} created", level="INFO")
            return convert_uuids_to_str(freelancer_skill_data)
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error creating freelancer skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer_skill(freelancer_skill_id: str, update_data: Dict) -> Optional[Dict]:
        """Update freelancer skill information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("FREELANCER_SKILL_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerSkillFunctions.get_freelancer_skill_by_id(freelancer_skill_id)
            
            conditions = [("freelancer_skill_id", "=", freelancer_skill_id)]
            db.update_data(table_name="freelancer_skill", data=update_data, conditions=conditions)
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Freelancer skill {freelancer_skill_id} updated", level="INFO")
            return FreelancerSkillFunctions.get_freelancer_skill_by_id(freelancer_skill_id)
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error updating freelancer skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_skill(freelancer_skill_id: str) -> bool:
        """Delete a freelancer skill"""
        try:
            db = get_db()
            conditions = [("freelancer_skill_id", "=", freelancer_skill_id)]
            db.delete_data(table_name="freelancer_skill", conditions=conditions)
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Freelancer skill {freelancer_skill_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error deleting freelancer skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_skill_by_freelancer_and_skill(freelancer_id: str, skill_id: str) -> bool:
        """Delete a freelancer skill by freelancer_id and skill_id"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id), ("skill_id", "=", skill_id)]
            db.delete_data(table_name="freelancer_skill", conditions=conditions)
            
            logger("FREELANCER_SKILL_FUNCTIONS", f"Freelancer skill deleted for freelancer {freelancer_id} and skill {skill_id}", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_SKILL_FUNCTIONS", f"Error deleting freelancer skill: {str(e)}", level="ERROR")
            raise
