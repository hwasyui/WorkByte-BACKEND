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


class ClientTrustScoreFunctions:
    """Handle all client trust score-related database operations"""

    @staticmethod
    def get_all_client_trust_scores(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all client trust scores"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client_trust_score",
                columns=["client_id", "trust_score", "rating_consistency_score", "extreme_rating_ratio",
                        "project_completion_rate", "average_budget_gap", "total_ratings_given", "last_calculated_at"],
                order_by="last_calculated_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Fetched {len(rows)} client trust scores", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Error fetching client trust scores: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_trust_score_by_id(client_id: str) -> Optional[Dict]:
        """Fetch client trust score by ID"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="client_trust_score",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Client trust score for {client_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Error fetching client trust score: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_client_trust_score(client_id: str, trust_score: float = 0.0) -> Dict:
        """Create a new client trust score"""
        try:
            db = get_db()
            
            trust_score_data = {
                "client_id": client_id,
                "trust_score": trust_score,
                "rating_consistency_score": 0.0,
                "extreme_rating_ratio": 0.0,
                "project_completion_rate": 0.0,
                "average_budget_gap": 0.0,
                "total_ratings_given": 0
            }
            
            db.insert_data(table_name="client_trust_score", data=trust_score_data)
            
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Client trust score created for {client_id}", level="INFO")
            return convert_uuids_to_str(trust_score_data)
        
        except Exception as e:
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Error creating client trust score: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_client_trust_score(client_id: str, update_data: Dict) -> Optional[Dict]:
        """Update client trust score information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("CLIENT_TRUST_SCORE_FUNCTIONS", "No data to update", level="WARNING")
                return ClientTrustScoreFunctions.get_client_trust_score_by_id(client_id)
            
            conditions = [("client_id", "=", client_id)]
            db.update_data(table_name="client_trust_score", data=update_data, conditions=conditions)
            
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Client trust score for {client_id} updated", level="INFO")
            return ClientTrustScoreFunctions.get_client_trust_score_by_id(client_id)
        
        except Exception as e:
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Error updating client trust score: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_client_trust_score(client_id: str) -> bool:
        """Delete a client trust score"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            db.delete_data(table_name="client_trust_score", conditions=conditions)
            
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Client trust score for {client_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("CLIENT_TRUST_SCORE_FUNCTIONS", f"Error deleting client trust score: {str(e)}", level="ERROR")
            raise
