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


class PerformanceRatingFunctions:
    """Handle all performance rating-related database operations"""

    @staticmethod
    def get_all_performance_ratings(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all performance ratings"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="performance_rating",
                columns=["freelancer_id", "overall_performance_score", "confidence_score", "total_ratings_received",
                        "average_communication", "average_result_quality", "average_professionalism",
                        "average_timeline_compliance", "success_rate", "last_calculated_at"],
                order_by="last_calculated_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Fetched {len(rows)} performance ratings", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Error fetching performance ratings: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_performance_rating_by_freelancer_id(freelancer_id: str) -> Optional[Dict]:
        """Fetch performance rating for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="performance_rating",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("PERFORMANCE_RATING_FUNCTIONS", f"Performance rating for freelancer {freelancer_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Error fetching performance rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_performance_rating(freelancer_id: str, overall_performance_score: float,
                                  confidence_score: float, total_ratings_received: int = 0) -> Dict:
        """Create a new performance rating"""
        try:
            db = get_db()
            
            performance_rating_data = {
                "freelancer_id": freelancer_id,
                "overall_performance_score": overall_performance_score,
                "confidence_score": confidence_score,
                "total_ratings_received": total_ratings_received,
                "average_communication": 0.0,
                "average_result_quality": 0.0,
                "average_professionalism": 0.0,
                "average_timeline_compliance": 0.0,
                "success_rate": 0.0
            }
            
            db.insert_data(table_name="performance_rating", data=performance_rating_data)
            
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Performance rating created for freelancer {freelancer_id}", level="INFO")
            return convert_uuids_to_str(performance_rating_data)
        
        except Exception as e:
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Error creating performance rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_performance_rating(freelancer_id: str, update_data: Dict) -> Optional[Dict]:
        """Update performance rating information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("PERFORMANCE_RATING_FUNCTIONS", "No data to update", level="WARNING")
                return PerformanceRatingFunctions.get_performance_rating_by_freelancer_id(freelancer_id)
            
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.update_data(table_name="performance_rating", data=update_data, conditions=conditions)
            
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Performance rating for freelancer {freelancer_id} updated", level="INFO")
            return PerformanceRatingFunctions.get_performance_rating_by_freelancer_id(freelancer_id)
        
        except Exception as e:
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Error updating performance rating: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_performance_rating(freelancer_id: str) -> bool:
        """Delete a performance rating"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.delete_data(table_name="performance_rating", conditions=conditions)
            
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Performance rating for freelancer {freelancer_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("PERFORMANCE_RATING_FUNCTIONS", f"Error deleting performance rating: {str(e)}", level="ERROR")
            raise
