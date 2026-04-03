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


class ProposalFunctions:
    """Handle all proposal-related database operations"""

    @staticmethod
    def get_all_proposals(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all proposals"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="proposal",
                columns=["proposal_id", "job_post_id", "job_role_id", "freelancer_id", "cover_letter", 
                        "proposed_budget", "proposed_duration", "status", "is_ai_generated", "submitted_at"],
                order_by="submitted_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("PROPOSAL_FUNCTIONS", f"Fetched {len(rows)} proposals", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_by_id(proposal_id: str) -> Optional[Dict]:
        """Fetch a proposal by ID"""
        try:
            db = get_db()
            conditions = [("proposal_id", "=", proposal_id)]
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_job_post_id(job_post_id: str) -> List[Dict]:
        """Fetch all proposals for a job post"""
        try:
            db = get_db()
            conditions = [("job_post_id", "=", job_post_id)]
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                order_by="submitted_at DESC"
            )
            
            logger("PROPOSAL_FUNCTIONS", f"Fetched {len(rows)} proposals for job post {job_post_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposals_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all proposals from a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="proposal",
                conditions=conditions,
                order_by="submitted_at DESC"
            )
            
            logger("PROPOSAL_FUNCTIONS", f"Fetched {len(rows)} proposals from freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error fetching proposals: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_proposal(job_post_id: str, freelancer_id: str, cover_letter: str, 
                        proposed_budget: float, job_role_id: Optional[str] = None,
                        proposed_duration: Optional[str] = None, status: Optional[str] = "pending",
                        is_ai_generated: Optional[bool] = False) -> Dict:
        """Create a new proposal"""
        try:
            db = get_db()
            proposal_id = str(uuid.uuid4())
            
            proposal_data = {
                "proposal_id": proposal_id,
                "job_post_id": job_post_id,
                "job_role_id": job_role_id,
                "freelancer_id": freelancer_id,
                "cover_letter": cover_letter,
                "proposed_budget": proposed_budget,
                "proposed_duration": proposed_duration,
                "status": status,
                "is_ai_generated": is_ai_generated
            }
            
            db.insert_data(table_name="proposal", data=proposal_data)
            
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} created", level="INFO")
            return convert_uuids_to_str(proposal_data)
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error creating proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_proposal(proposal_id: str, update_data: Dict) -> Optional[Dict]:
        """Update proposal information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("PROPOSAL_FUNCTIONS", "No data to update", level="WARNING")
                return ProposalFunctions.get_proposal_by_id(proposal_id)
            
            conditions = [("proposal_id", "=", proposal_id)]
            db.update_data(table_name="proposal", data=update_data, conditions=conditions)
            
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} updated", level="INFO")
            return ProposalFunctions.get_proposal_by_id(proposal_id)
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error updating proposal: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_proposal(proposal_id: str) -> bool:
        """Delete a proposal"""
        try:
            db = get_db()
            conditions = [("proposal_id", "=", proposal_id)]
            db.delete_data(table_name="proposal", conditions=conditions)
            
            logger("PROPOSAL_FUNCTIONS", f"Proposal {proposal_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("PROPOSAL_FUNCTIONS", f"Error deleting proposal: {str(e)}", level="ERROR")
            raise
