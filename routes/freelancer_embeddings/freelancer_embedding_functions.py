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


class FreelancerEmbeddingFunctions:
    """Handle all freelancer embedding-related database operations"""

    @staticmethod
    def get_all_freelancer_embeddings(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all freelancer embeddings"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_embedding",
                columns=["embedding_id", "freelancer_id", "embedding_vector", "source_text", "embedding_metadata", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Fetched {len(rows)} freelancer embeddings", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error fetching freelancer embeddings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_embedding_by_id(embedding_id: str) -> Optional[Dict]:
        """Fetch a freelancer embedding by ID"""
        try:
            db = get_db()
            conditions = [("embedding_id", "=", embedding_id)]
            rows = db.fetch_data(
                table_name="freelancer_embedding",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Freelancer embedding {embedding_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error fetching freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_embedding_by_freelancer_id(freelancer_id: str) -> Optional[Dict]:
        """Fetch embedding for a specific freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer_embedding",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Freelancer embedding for {freelancer_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error fetching freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer_embedding(freelancer_id: str, embedding_vector: List[float],
                                   source_text: Optional[str] = None, embedding_metadata: Optional[Dict] = None) -> Dict:
        """Create a new freelancer embedding"""
        try:
            db = get_db()
            embedding_id = str(uuid.uuid4())
            
            embedding_data = {
                "embedding_id": embedding_id,
                "freelancer_id": freelancer_id,
                "embedding_vector": embedding_vector,
                "source_text": source_text,
                "embedding_metadata": embedding_metadata
            }
            
            db.insert_data(table_name="freelancer_embedding", data=embedding_data)
            
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Freelancer embedding {embedding_id} created", level="INFO")
            return convert_uuids_to_str(embedding_data)
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error creating freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer_embedding(embedding_id: str, update_data: Dict) -> Optional[Dict]:
        """Update freelancer embedding information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("FREELANCER_EMBEDDING_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerEmbeddingFunctions.get_freelancer_embedding_by_id(embedding_id)
            
            conditions = [("embedding_id", "=", embedding_id)]
            db.update_data(table_name="freelancer_embedding", data=update_data, conditions=conditions)
            
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Freelancer embedding {embedding_id} updated", level="INFO")
            return FreelancerEmbeddingFunctions.get_freelancer_embedding_by_id(embedding_id)
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error updating freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_embedding(embedding_id: str) -> bool:
        """Delete a freelancer embedding"""
        try:
            db = get_db()
            conditions = [("embedding_id", "=", embedding_id)]
            db.delete_data(table_name="freelancer_embedding", conditions=conditions)
            
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Freelancer embedding {embedding_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_EMBEDDING_FUNCTIONS", f"Error deleting freelancer embedding: {str(e)}", level="ERROR")
            raise
