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


class JobRoleSkillFunctions:
    """Handle all job role skill-related database operations"""

    @staticmethod
    def get_all_job_role_skills(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all job role skills"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_role_skill",
                columns=["job_role_skill_id", "job_role_id", "skill_id", "is_required", "importance_level", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Fetched {len(rows)} job role skills", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error fetching job role skills: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_role_skill_by_id(job_role_skill_id: str) -> Optional[Dict]:
        """Fetch a job role skill by ID"""
        try:
            db = get_db()
            conditions = [("job_role_skill_id", "=", job_role_skill_id)]
            rows = db.fetch_data(
                table_name="job_role_skill",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_ROLE_SKILL_FUNCTIONS", f"Job role skill {job_role_skill_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error fetching job role skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_role_skills_by_job_role_id(job_role_id: str) -> List[Dict]:
        """Fetch all skills for a job role"""
        try:
            db = get_db()
            conditions = [("job_role_id", "=", job_role_id)]
            rows = db.fetch_data(
                table_name="job_role_skill",
                conditions=conditions,
                order_by="is_required DESC"
            )
            
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Fetched {len(rows)} skills for job role {job_role_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error fetching job role skills: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_role_skill(job_role_id: str, skill_id: str, 
                              is_required: Optional[bool] = True,
                              importance_level: Optional[str] = None) -> Dict:
        """Create a new job role skill"""
        try:
            db = get_db()
            job_role_skill_id = str(uuid.uuid4())
            
            job_role_skill_data = {
                "job_role_skill_id": job_role_skill_id,
                "job_role_id": job_role_id,
                "skill_id": skill_id,
                "is_required": is_required,
                "importance_level": importance_level
            }
            
            db.insert_data(table_name="job_role_skill", data=job_role_skill_data)
            
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Job role skill {job_role_skill_id} created", level="INFO")
            return convert_uuids_to_str(job_role_skill_data)
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error creating job role skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_role_skill(job_role_skill_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job role skill information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("JOB_ROLE_SKILL_FUNCTIONS", "No data to update", level="WARNING")
                return JobRoleSkillFunctions.get_job_role_skill_by_id(job_role_skill_id)
            
            conditions = [("job_role_skill_id", "=", job_role_skill_id)]
            db.update_data(table_name="job_role_skill", data=update_data, conditions=conditions)
            
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Job role skill {job_role_skill_id} updated", level="INFO")
            return JobRoleSkillFunctions.get_job_role_skill_by_id(job_role_skill_id)
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error updating job role skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_role_skill(job_role_skill_id: str) -> bool:
        """Delete a job role skill"""
        try:
            db = get_db()
            conditions = [("job_role_skill_id", "=", job_role_skill_id)]
            db.delete_data(table_name="job_role_skill", conditions=conditions)
            
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Job role skill {job_role_skill_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("JOB_ROLE_SKILL_FUNCTIONS", f"Error deleting job role skill: {str(e)}", level="ERROR")
            raise
