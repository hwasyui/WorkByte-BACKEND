import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from functions.schema_model import UserInDB
from routes.contracts.contract_functions import ContractFunctions
from routes.job_posts.job_post_functions import JobPostFunctions
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


class RatingFunctions:
    """Handle all rating-related database operations"""

    @staticmethod
    def get_all_ratings(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all ratings"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="rating",
                columns=["rating_id", "contract_id", "client_id", "freelancer_id", "communication_score",
                        "result_quality_score", "professionalism_score", "timeline_compliance_score",
                        "overall_rating", "review_text", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("RATING_FUNCTIONS", f"Fetched {len(rows)} ratings", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error fetching ratings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_rating_by_id(rating_id: str) -> Optional[Dict]:
        """Fetch a rating by ID"""
        try:
            db = get_db()
            conditions = [("rating_id", "=", rating_id)]
            rows = db.fetch_data(
                table_name="rating",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("RATING_FUNCTIONS", f"Rating {rating_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error fetching rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_ratings_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all ratings for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="rating",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("RATING_FUNCTIONS", f"Fetched {len(rows)} ratings for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error fetching ratings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_ratings_by_client_id(client_id: str) -> List[Dict]:
        """Fetch all ratings given by a client"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="rating",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("RATING_FUNCTIONS", f"Fetched {len(rows)} ratings from client {client_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error fetching ratings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_rating_by_contract_id(contract_id: str) -> Optional[Dict]:
        """Fetch a rating by contract ID"""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(table_name="rating", conditions=conditions, limit=1)
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error fetching rating by contract_id: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_rating(contract_id: str, client_id: str, freelancer_id: str,
                      communication_score: int, result_quality_score: int, professionalism_score: int,
                      timeline_compliance_score: int, overall_rating: float, review_text: Optional[str] = None) -> Dict:
        """Create a new rating"""
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise ValueError(f"Contract {contract_id} not found")

            # Only client who owns the contract can rate
            if str(contract["client_id"]) != str(client_id):
                raise ValueError("Only contract owner client can create rating")

            # Ensure freelancer matches contract
            if str(contract["freelancer_id"]) != str(freelancer_id):
                raise ValueError("Freelancer does not match contract")

            # Only complete/cancelled/disputed contracts can be rated
            if contract.get("status") not in ["completed", "cancelled", "disputed"]:
                raise ValueError("Can only rate a contract that is completed, cancelled, or disputed")

            # Ensure rating does not exist for the contract
            existing = RatingFunctions.get_rating_by_contract_id(contract_id)
            if existing:
                raise ValueError("Rating already exists for this contract")

            db = get_db()
            rating_id = str(uuid.uuid4())
            
            rating_data = {
                "rating_id": rating_id,
                "contract_id": contract_id,
                "client_id": client_id,
                "freelancer_id": freelancer_id,
                "communication_score": communication_score,
                "result_quality_score": result_quality_score,
                "professionalism_score": professionalism_score,
                "timeline_compliance_score": timeline_compliance_score,
                "overall_rating": overall_rating,
                "review_text": review_text,
                "update_count": 0
            }
            
            db.insert_data(table_name="rating", data=rating_data)
            
            logger("RATING_FUNCTIONS", f"Rating {rating_id} created", level="INFO")
            return convert_uuids_to_str(rating_data)
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error creating rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_rating(rating_id: str, update_data: Dict) -> Optional[Dict]:
        """Update rating information (only once)"""
        try:
            existing_rating = RatingFunctions.get_rating_by_id(rating_id)
            if not existing_rating:
                raise ValueError("Rating not found")

            current_updates = existing_rating.get("update_count", 0) or 0
            if current_updates >= 1:
                raise ValueError("Rating can only be updated once")

            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            if not update_data:
                logger("RATING_FUNCTIONS", "No data to update", level="WARNING")
                return existing_rating

            update_data["update_count"] = current_updates + 1
            from datetime import datetime
            update_data["updated_at"] = datetime.utcnow()

            conditions = [("rating_id", "=", rating_id)]
            db.update_data(table_name="rating", data=update_data, conditions=conditions)

            logger("RATING_FUNCTIONS", f"Rating {rating_id} updated", level="INFO")
            return RatingFunctions.get_rating_by_id(rating_id)

        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error updating rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_rating(rating_id: str) -> bool:
        """Delete a rating"""
        try:
            db = get_db()
            conditions = [("rating_id", "=", rating_id)]
            db.delete_data(table_name="rating", conditions=conditions)
            
            logger("RATING_FUNCTIONS", f"Rating {rating_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("RATING_FUNCTIONS", f"Error deleting rating: {str(e)}", level="ERROR")
            raise
