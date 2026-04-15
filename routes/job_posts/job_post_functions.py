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


class JobPostFunctions:
    """Handle all job post-related database operations"""

    @staticmethod
    def get_all_job_posts(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all job posts"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_post",
                columns=["job_post_id", "client_id", "job_title", "job_description", "project_type", "project_scope",
                        "estimated_duration", "working_days", "deadline", "experience_level", "status", "is_ai_generated",
                        "view_count", "proposal_count", "created_at", "updated_at", "posted_at", "closed_at"],
                order_by="created_at DESC",
                limit=limit,
            )
            
            logger("JOB_POST_FUNCTIONS", f"Fetched {len(rows)} job posts", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job posts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_post_by_id(job_post_id: str) -> Optional[Dict]:
        """Fetch a job post by ID"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="job_post",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_posts_by_client_id(client_id: str) -> List[Dict]:
        """Fetch all job posts for a client"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="job_post",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("JOB_POST_FUNCTIONS", f"Fetched {len(rows)} job posts for client {client_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error fetching job posts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_post(client_id: str, job_title: str, job_description: str, 
                        project_type: str, project_scope: str, estimated_duration: Optional[str] = None,
                        working_days: Optional[int] = None, deadline=None, experience_level: Optional[str] = None,
                        status: Optional[str] = "draft", is_ai_generated: Optional[bool] = False) -> Dict:
        """Create a new job post"""
        try:
            db = get_db()
            job_post_id = str(uuid.uuid4())
            
            job_post_data = {
                "job_post_id": job_post_id,
                "client_id": client_id,
                "job_title": job_title,
                "job_description": job_description,
                "project_type": project_type,
                "project_scope": project_scope,
                "estimated_duration": estimated_duration,
                "working_days": working_days,
                "deadline": deadline,
                "experience_level": experience_level,
                "status": status,
                "is_ai_generated": is_ai_generated
            }
            
            db.insert_data(table_name="job_post", data=job_post_data)
            
            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} created", level="INFO")
            return convert_uuids_to_str(job_post_data)
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error creating job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_post(job_post_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job post information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("JOB_POST_FUNCTIONS", "No data to update", level="WARNING")
                return JobPostFunctions.get_job_post_by_id(job_post_id)
            
            conditions = [("job_post_id", "=", job_post_id)]
            db.update_data(table_name="job_post", data=update_data, conditions=conditions)
            
            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} updated", level="INFO")
            return JobPostFunctions.get_job_post_by_id(job_post_id)
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error updating job post: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_post(job_post_id: str) -> bool:
        """Delete a job post"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            db.delete_data(table_name="job_post", conditions=conditions)
            
            logger("JOB_POST_FUNCTIONS", f"Job post {job_post_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("JOB_POST_FUNCTIONS", f"Error deleting job post: {str(e)}", level="ERROR")
            raise
