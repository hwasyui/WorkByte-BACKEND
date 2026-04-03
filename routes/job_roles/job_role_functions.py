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


class JobRoleFunctions:
    """Handle all job role-related database operations"""

    @staticmethod
    def get_all_job_roles(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all job roles"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_role",
                columns=["job_role_id", "job_post_id", "role_title", "role_budget", "budget_currency", 
                        "budget_type", "role_description", "positions_available", "positions_filled",
                        "is_required", "display_order", "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("JOB_ROLE_FUNCTIONS", f"Fetched {len(rows)} job roles", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error fetching job roles: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_role_by_id(job_role_id: str) -> Optional[Dict]:
        """Fetch a job role by ID"""
        try:
            db = get_db()
            conditions = [("job_role_id", "=", job_role_id)]
            rows = db.fetch_data(
                table_name="job_role",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_ROLE_FUNCTIONS", f"Job role {job_role_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error fetching job role: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_roles_by_job_post_id(job_post_id: str) -> List[Dict]:
        """Fetch all job roles for a job post"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="job_role",
                conditions=conditions,
                order_by="display_order ASC"
            )
            
            logger("JOB_ROLE_FUNCTIONS", f"Fetched {len(rows)} job roles for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error fetching job roles: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_role(job_post_id: str, role_title: str, budget_type: str,
                        role_budget: Optional[float] = None, budget_currency: Optional[str] = "USD",
                        role_description: Optional[str] = None, positions_available: Optional[int] = 1,
                        is_required: Optional[bool] = True, display_order: Optional[int] = 0) -> Dict:
        """Create a new job role"""
        try:
            db = get_db()
            job_role_id = str(uuid.uuid4())
            
            job_role_data = {
                "job_role_id": job_role_id,
                "job_post_id": job_post_id,
                "role_title": role_title,
                "role_budget": role_budget,
                "budget_currency": budget_currency,
                "budget_type": budget_type,
                "role_description": role_description,
                "positions_available": positions_available,
                "is_required": is_required,
                "display_order": display_order
            }
            
            db.insert_data(table_name="job_role", data=job_role_data)
            
            logger("JOB_ROLE_FUNCTIONS", f"Job role {job_role_id} created", level="INFO")
            return convert_uuids_to_str(job_role_data)
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error creating job role: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_role(job_role_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job role information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("JOB_ROLE_FUNCTIONS", "No data to update", level="WARNING")
                return JobRoleFunctions.get_job_role_by_id(job_role_id)
            
            conditions = [("job_role_id", "=", job_role_id)]
            db.update_data(table_name="job_role", data=update_data, conditions=conditions)
            
            logger("JOB_ROLE_FUNCTIONS", f"Job role {job_role_id} updated", level="INFO")
            return JobRoleFunctions.get_job_role_by_id(job_role_id)
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error updating job role: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_role(job_role_id: str) -> bool:
        """Delete a job role"""
        try:
            db = get_db()
            conditions = [("job_role_id", "=", job_role_id)]
            db.delete_data(table_name="job_role", conditions=conditions)
            
            logger("JOB_ROLE_FUNCTIONS", f"Job role {job_role_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("JOB_ROLE_FUNCTIONS", f"Error deleting job role: {str(e)}", level="ERROR")
            raise
