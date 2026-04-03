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


class ContractMilestoneFunctions:
    """Handle all contract milestone-related database operations"""

    @staticmethod
    def get_all_contract_milestones(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all contract milestones"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_milestone",
                columns=["milestone_id", "contract_id", "milestone_title", "milestone_description", 
                        "milestone_percentage", "milestone_amount", "milestone_order", "due_date", "status",
                        "completed_at", "paid_at", "created_at", "updated_at"],
                order_by="milestone_order ASC",
                limit=limit,
                offset=offset
            )
            
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Fetched {len(rows)} contract milestones", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error fetching contract milestones: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contract_milestone_by_id(milestone_id: str) -> Optional[Dict]:
        """Fetch a contract milestone by ID"""
        try:
            db = get_db()
            conditions = [("milestone_id", "=", milestone_id)]
            rows = db.fetch_data(
                table_name="contract_milestone",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("CONTRACT_MILESTONE_FUNCTIONS", f"Contract milestone {milestone_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error fetching contract milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contract_milestones_by_contract_id(contract_id: str) -> List[Dict]:
        """Fetch all milestones for a contract"""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(
                table_name="contract_milestone",
                conditions=conditions,
                order_by="milestone_order ASC"
            )
            
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Fetched {len(rows)} milestones for contract {contract_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error fetching contract milestones: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_contract_milestone(contract_id: str, milestone_title: str, milestone_percentage: float,
                                  milestone_amount: float, milestone_order: int, description: Optional[str] = None,
                                  due_date=None, status: Optional[str] = "pending") -> Dict:
        """Create a new contract milestone"""
        try:
            db = get_db()
            milestone_id = str(uuid.uuid4())
            
            contract_milestone_data = {
                "milestone_id": milestone_id,
                "contract_id": contract_id,
                "milestone_title": milestone_title,
                "milestone_description": description,
                "milestone_percentage": milestone_percentage,
                "milestone_amount": milestone_amount,
                "milestone_order": milestone_order,
                "due_date": due_date,
                "status": status,
                "client_approved": False,
                "freelancer_confirmed_paid": False,
                "payment_requested": False
            }
            
            db.insert_data(table_name="contract_milestone", data=contract_milestone_data)
            
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Contract milestone {milestone_id} created", level="INFO")
            return convert_uuids_to_str(contract_milestone_data)
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error creating contract milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_contract_milestone(milestone_id: str, update_data: Dict) -> Optional[Dict]:
        """Update contract milestone information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("CONTRACT_MILESTONE_FUNCTIONS", "No data to update", level="WARNING")
                return ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
            
            conditions = [("milestone_id", "=", milestone_id)]
            db.update_data(table_name="contract_milestone", data=update_data, conditions=conditions)
            
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Contract milestone {milestone_id} updated", level="INFO")
            return ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error updating contract milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_contract_milestone(milestone_id: str) -> bool:
        """Delete a contract milestone"""
        try:
            db = get_db()
            conditions = [("milestone_id", "=", milestone_id)]
            db.delete_data(table_name="contract_milestone", conditions=conditions)
            
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Contract milestone {milestone_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("CONTRACT_MILESTONE_FUNCTIONS", f"Error deleting contract milestone: {str(e)}", level="ERROR")
            raise
