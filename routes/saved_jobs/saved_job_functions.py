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


class SavedJobFunctions:
    """Handle all saved job-related database operations"""

    @staticmethod
    def get_all_saved_jobs(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all saved jobs"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="saved_job",
                columns=["saved_job_id", "freelancer_id", "job_post_id", "saved_at", "notes"],
                order_by="saved_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("SAVED_JOB_FUNCTIONS", f"Fetched {len(rows)} saved jobs", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error fetching saved jobs: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_saved_job_by_id(saved_job_id: str) -> Optional[Dict]:
        """Fetch a saved job by ID"""
        try:
            db = get_db()
            conditions = [("saved_job_id", "=", saved_job_id)]
            rows = db.fetch_data(
                table_name="saved_job",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("SAVED_JOB_FUNCTIONS", f"Saved job {saved_job_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error fetching saved job: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_saved_jobs_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all saved jobs for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="saved_job",
                conditions=conditions,
                order_by="saved_at DESC"
            )
            
            logger("SAVED_JOB_FUNCTIONS", f"Fetched {len(rows)} saved jobs for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error fetching saved jobs: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_saved_job(freelancer_id: str, job_post_id: str, notes: Optional[str] = None) -> Dict:
        """Create a new saved job"""
        try:
            db = get_db()
            saved_job_id = str(uuid.uuid4())
            
            saved_job_data = {
                "saved_job_id": saved_job_id,
                "freelancer_id": freelancer_id,
                "job_post_id": job_post_id,
                "notes": notes
            }
            
            db.insert_data(table_name="saved_job", data=saved_job_data)
            
            logger("SAVED_JOB_FUNCTIONS", f"Saved job {saved_job_id} created", level="INFO")
            return convert_uuids_to_str(saved_job_data)
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error creating saved job: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_saved_job(saved_job_id: str, update_data: Dict) -> Optional[Dict]:
        """Update saved job information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("SAVED_JOB_FUNCTIONS", "No data to update", level="WARNING")
                return SavedJobFunctions.get_saved_job_by_id(saved_job_id)
            
            conditions = [("saved_job_id", "=", saved_job_id)]
            db.update_data(table_name="saved_job", data=update_data, conditions=conditions)
            
            logger("SAVED_JOB_FUNCTIONS", f"Saved job {saved_job_id} updated", level="INFO")
            return SavedJobFunctions.get_saved_job_by_id(saved_job_id)
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error updating saved job: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_saved_job(saved_job_id: str) -> bool:
        """Delete a saved job"""
        try:
            db = get_db()
            conditions = [("saved_job_id", "=", saved_job_id)]
            db.delete_data(table_name="saved_job", conditions=conditions)
            
            logger("SAVED_JOB_FUNCTIONS", f"Saved job {saved_job_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("SAVED_JOB_FUNCTIONS", f"Error deleting saved job: {str(e)}", level="ERROR")
            raise
