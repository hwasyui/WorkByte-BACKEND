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


class JobEmbeddingFunctions:
    """Handle all job embedding-related database operations"""

    @staticmethod
    def get_all_job_embeddings(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all job embeddings"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_embedding",
                columns=["embedding_id", "job_post_id", "embedding_vector", "source_text", "embedding_metadata", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("JOB_EMBEDDING_FUNCTIONS", f"Fetched {len(rows)} job embeddings", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error fetching job embeddings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_embedding_by_id(embedding_id: str) -> Optional[Dict]:
        """Fetch a job embedding by ID"""
        try:
            db = get_db()
            conditions = [("embedding_id", "=", embedding_id)]
            rows = db.fetch_data(
                table_name="job_embedding",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_EMBEDDING_FUNCTIONS", f"Job embedding {embedding_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error fetching job embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_embedding_by_job_post_id(job_post_id: str) -> Optional[Dict]:
        """Fetch embedding for a specific job post"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="job_embedding",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("JOB_EMBEDDING_FUNCTIONS", f"Job embedding for post {job_post_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error fetching job embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_embedding(job_post_id: str, embedding_vector: List[float],
                            source_text: Optional[str] = None, embedding_metadata: Optional[Dict] = None) -> Dict:
        """Create a new job embedding"""
        try:
            db = get_db()
            embedding_id = str(uuid.uuid4())
            
            embedding_data = {
                "embedding_id": embedding_id,
                "job_post_id": job_post_id,
                "embedding_vector": embedding_vector,
                "source_text": source_text,
                "embedding_metadata": embedding_metadata
            }
            
            db.insert_data(table_name="job_embedding", data=embedding_data)
            
            logger("JOB_EMBEDDING_FUNCTIONS", f"Job embedding {embedding_id} created", level="INFO")
            return convert_uuids_to_str(embedding_data)
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error creating job embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_embedding(embedding_id: str, update_data: Dict) -> Optional[Dict]:
        """Update job embedding information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("JOB_EMBEDDING_FUNCTIONS", "No data to update", level="WARNING")
                return JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
            
            conditions = [("embedding_id", "=", embedding_id)]
            db.update_data(table_name="job_embedding", data=update_data, conditions=conditions)
            
            logger("JOB_EMBEDDING_FUNCTIONS", f"Job embedding {embedding_id} updated", level="INFO")
            return JobEmbeddingFunctions.get_job_embedding_by_id(embedding_id)
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error updating job embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_embedding(embedding_id: str) -> bool:
        """Delete a job embedding"""
        try:
            db = get_db()
            conditions = [("embedding_id", "=", embedding_id)]
            db.delete_data(table_name="job_embedding", conditions=conditions)
            
            logger("JOB_EMBEDDING_FUNCTIONS", f"Job embedding {embedding_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("JOB_EMBEDDING_FUNCTIONS", f"Error deleting job embedding: {str(e)}", level="ERROR")
            raise
