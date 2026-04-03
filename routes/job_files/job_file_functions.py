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


class JobFileFunctions:
    """Handle all job file-related database operations"""

    @staticmethod
    def get_all_job_files(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all job files"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_file",
                columns=["job_file_id", "job_post_id", "file_url", "file_type", "file_name", "file_size", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("JOB_FILE_FUNCTIONS", f"Fetched {len(rows)} job files", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error fetching job files: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_file_by_id(job_file_id: str) -> Optional[Dict]:
        """Fetch a job file by ID"""
        try:
            db = get_db()
            conditions = [("job_file_id", "=", job_file_id)]
            rows = db.fetch_data(
                table_name="job_file",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_FILE_FUNCTIONS", f"Job file {job_file_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error fetching job file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_files_by_job_post_id(job_post_id: str) -> List[Dict]:
        """Fetch all files for a job post"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="job_file",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("JOB_FILE_FUNCTIONS", f"Fetched {len(rows)} files for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error fetching job files: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_file(job_post_id: str, file_url: str, file_type: str, 
                        file_name: str, file_size: Optional[int] = None) -> Dict:
        """Create a new job file"""
        try:
            db = get_db()
            job_file_id = str(uuid.uuid4())
            
            job_file_data = {
                "job_file_id": job_file_id,
                "job_post_id": job_post_id,
                "file_url": file_url,
                "file_type": file_type,
                "file_name": file_name,
                "file_size": file_size
            }
            
            db.insert_data(table_name="job_file", data=job_file_data)
            
            logger("JOB_FILE_FUNCTIONS", f"Job file {job_file_id} created", level="INFO")
            return convert_uuids_to_str(job_file_data)
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error creating job file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_file(job_file_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job file information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("JOB_FILE_FUNCTIONS", "No data to update", level="WARNING")
                return JobFileFunctions.get_job_file_by_id(job_file_id)
            
            conditions = [("job_file_id", "=", job_file_id)]
            db.update_data(table_name="job_file", data=update_data, conditions=conditions)
            
            logger("JOB_FILE_FUNCTIONS", f"Job file {job_file_id} updated", level="INFO")
            return JobFileFunctions.get_job_file_by_id(job_file_id)
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error updating job file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_file(job_file_id: str) -> bool:
        """Delete a job file"""
        try:
            db = get_db()
            conditions = [("job_file_id", "=", job_file_id)]
            db.delete_data(table_name="job_file", conditions=conditions)
            
            logger("JOB_FILE_FUNCTIONS", f"Job file {job_file_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("JOB_FILE_FUNCTIONS", f"Error deleting job file: {str(e)}", level="ERROR")
            raise
