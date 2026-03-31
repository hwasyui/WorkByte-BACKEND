import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid
import json
from datetime import datetime

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


class EmbeddingFunctions:
    """Helper functions for managing embeddings with pgvector"""

    @staticmethod
    def get_embedding_vector(text: str) -> List[float]:
        """
        Generate embedding vector from text using OpenAI API or similar service.
        Replace with your actual embedding service.
        For now, returns a mock vector of 1536 dimensions (OpenAI Ada embedding size).
        """
        try:
            # TODO: Implement actual embedding generation
            # Example: using OpenAI API, Sentence Transformers, etc.
            # For now, returning a random vector
            import random
            mock_vector = [random.random() for _ in range(1536)]
            return mock_vector
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error generating embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer_embedding(freelancer_id: str, source_text: str) -> Dict:
        """Create or update freelancer embedding"""
        try:
            db = get_db()
            # Check if embedding already exists
            query = "SELECT embedding_id FROM freelancer_embedding WHERE freelancer_id = :freelancer_id"
            result = db.execute_query(query, {"freelancer_id": freelancer_id})
            
            embedding_vector = EmbeddingFunctions.get_embedding_vector(source_text)
            
            if result and len(result) > 0:
                # Update existing embedding
                embedding_id = result[0]['embedding_id']
                update_data = {
                    "embedding_vector": json.dumps(embedding_vector),
                    "source_text": source_text,
                    "embedding_metadata": json.dumps({"updated": True})
                }
                conditions = [("embedding_id", "=", embedding_id)]
                db.update_data(table_name="freelancer_embedding", data=update_data, conditions=conditions)
                logger("EMBEDDING_FUNCTIONS", f"Updated freelancer embedding: {embedding_id}", level="INFO")
                return {"embedding_id": embedding_id, "status": "updated"}
            else:
                # Create new embedding
                embedding_id = str(uuid.uuid4())
                embedding_data = {
                    "embedding_id": embedding_id,
                    "freelancer_id": freelancer_id,
                    "embedding_vector": json.dumps(embedding_vector),
                    "source_text": source_text,
                    "embedding_metadata": json.dumps({"created": True})
                }
                db.insert_data(table_name="freelancer_embedding", data=embedding_data)
                logger("EMBEDDING_FUNCTIONS", f"Created freelancer embedding: {embedding_id}", level="INFO")
                return {"embedding_id": embedding_id, "status": "created"}
        
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error managing freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer_embedding(freelancer_id: str) -> bool:
        """Delete freelancer embedding"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.delete_data(table_name="freelancer_embedding", conditions=conditions)
            logger("EMBEDDING_FUNCTIONS", f"Deleted freelancer embedding for {freelancer_id}", level="INFO")
            return True
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error deleting freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_job_embedding(job_post_id: str, source_text: str) -> Dict:
        """Create or update job embedding"""
        try:
            db = get_db()
            # Check if embedding already exists
            query = "SELECT embedding_id FROM job_embedding WHERE job_post_id = :job_post_id"
            result = db.execute_query(query, {"job_post_id": job_post_id})
            
            embedding_vector = EmbeddingFunctions.get_embedding_vector(source_text)
            
            if result and len(result) > 0:
                # Update existing embedding
                embedding_id = result[0]['embedding_id']
                update_data = {
                    "embedding_vector": json.dumps(embedding_vector),
                    "source_text": source_text,
                    "embedding_metadata": json.dumps({"updated": True})
                }
                conditions = [("embedding_id", "=", embedding_id)]
                db.update_data(table_name="job_embedding", data=update_data, conditions=conditions)
                logger("EMBEDDING_FUNCTIONS", f"Updated job embedding: {embedding_id}", level="INFO")
                return {"embedding_id": embedding_id, "status": "updated"}
            else:
                # Create new embedding
                embedding_id = str(uuid.uuid4())
                embedding_data = {
                    "embedding_id": embedding_id,
                    "job_post_id": job_post_id,
                    "embedding_vector": json.dumps(embedding_vector),
                    "source_text": source_text,
                    "embedding_metadata": json.dumps({"created": True})
                }
                db.insert_data(table_name="job_embedding", data=embedding_data)
                logger("EMBEDDING_FUNCTIONS", f"Created job embedding: {embedding_id}", level="INFO")
                return {"embedding_id": embedding_id, "status": "created"}
        
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error managing job embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_embedding(job_post_id: str) -> bool:
        """Delete job embedding"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            db.delete_data(table_name="job_embedding", conditions=conditions)
            logger("EMBEDDING_FUNCTIONS", f"Deleted job embedding for {job_post_id}", level="INFO")
            return True
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error deleting job embedding: {str(e)}", level="ERROR")
            raise


class FreelancerFunctions:
    """Handle all freelancer-related database operations"""

    @staticmethod
    def get_all_freelancers(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all freelancers"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer",
                columns=["freelancer_id", "user_id", "full_name", "bio", "cv_file_url", 
                        "profile_picture_url", "estimated_rate", "rate_time", "rate_currency", 
                        "total_projects", "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit
            )
            
            logger("FREELANCER_FUNCTIONS", f"Fetched {len(rows)} freelancers", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancers: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_by_id(freelancer_id: str) -> Optional[Dict]:
        """Fetch a single freelancer by ID"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_by_user_id(user_id: str) -> Optional[Dict]:
        """Fetch a freelancer by user ID"""
        try:
            db = get_db()
            conditions = [("user_id", "=", user_id)]
            rows = db.fetch_data(
                table_name="freelancer",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("FREELANCER_FUNCTIONS", f"Freelancer for user {user_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer by user_id: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_by_id_or_user_id(identifier: str) -> Optional[Dict]:
        """Fetch a freelancer by either freelancer_id or user_id"""
        try:
            # Try freelancer_id first
            result = FreelancerFunctions.get_freelancer_by_id(identifier)
            if result:
                return result
            
            # Try user_id as fallback
            result = FreelancerFunctions.get_freelancer_by_user_id(identifier)
            if result:
                return result
            
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer(freelancer_id: str, user_id: str, full_name: str, bio: Optional[str] = None,
                         cv_file_url: Optional[str] = None, profile_picture_url: Optional[str] = None,
                         estimated_rate: Optional[float] = None, rate_time: str = "hourly",
                         rate_currency: str = "USD", create_embedding: bool = True) -> Dict:
        """Create a new freelancer profile"""
        try:
            db = get_db()
            freelancer_id = str(uuid.uuid4())
            
            freelancer_data = {
                "freelancer_id": freelancer_id,
                "user_id": user_id,
                "full_name": full_name,
                "bio": bio,
                "cv_file_url": cv_file_url,
                "profile_picture_url": profile_picture_url,
                "estimated_rate": estimated_rate,
                "rate_time": rate_time,
                "rate_currency": rate_currency,
                "total_projects": 0
            }
            
            db.insert_data(table_name="freelancer", data=freelancer_data)
            
            # Create embedding if bio provided
            if create_embedding and bio:
                source_text = f"{full_name} - {bio}"
                EmbeddingFunctions.create_freelancer_embedding(freelancer_id, source_text)
            
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} created", level="INFO")
            return convert_uuids_to_str(freelancer_data)
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error creating freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer(freelancer_id: str, update_data: Dict, update_embedding: bool = True) -> Optional[Dict]:
        """Update freelancer information"""
        try:
            db = get_db()
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("FREELANCER_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerFunctions.get_freelancer_by_id(freelancer_id)
            
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.update_data(table_name="freelancer", data=update_data, conditions=conditions)
            
            # Update embedding if bio changed
            if update_embedding and "bio" in update_data:
                freelancer = FreelancerFunctions.get_freelancer_by_id(freelancer_id)
                if freelancer:
                    source_text = f"{freelancer['full_name']} - {update_data.get('bio', '')}"
                    EmbeddingFunctions.create_freelancer_embedding(freelancer_id, source_text)
            
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} updated", level="INFO")
            return FreelancerFunctions.get_freelancer_by_id(freelancer_id)
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error updating freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer(freelancer_id: str, delete_embedding: bool = True) -> bool:
        """Delete a freelancer profile"""
        try:
            db = get_db()
            # Delete embedding first
            if delete_embedding:
                EmbeddingFunctions.delete_freelancer_embedding(freelancer_id)
            
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.delete_data(table_name="freelancer", conditions=conditions)
            
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error deleting freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_freelancers_by_name(search_term: str) -> List[Dict]:
        """Search freelancers by name"""
        try:
            db = get_db()
            query = "SELECT * FROM freelancer WHERE full_name ILIKE '%' || :search_term || '%' ORDER BY created_at DESC"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("FREELANCER_FUNCTIONS", f"Found {len(rows)} freelancers matching '{search_term}'", level="INFO")
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error searching freelancers: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_embedding(freelancer_id: str) -> Optional[Dict]:
        """Get freelancer embedding"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="freelancer_embedding",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                return dict(rows[0])
            return None
        
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer embedding: {str(e)}", level="ERROR")
            raise
