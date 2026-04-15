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


class ContractFunctions:
    """Handle all contract-related database operations"""

    @staticmethod
    def get_all_contracts(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all contracts"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract",
                columns=["contract_id", "job_post_id", "job_role_id", "proposal_id", "freelancer_id", "client_id",
                        "contract_title", "role_title", "agreed_budget", "budget_currency", "payment_structure",
                        "agreed_duration", "status", "start_date", "end_date", "actual_completion_date",
                        "total_hours_worked", "total_paid", "contract_pdf_url", "contract_pdf_generated_at",
                        "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contract_by_id(contract_id: str) -> Optional[Dict]:
        """Fetch a contract by ID"""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            rows = db.fetch_data(
                table_name="contract",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contracts_by_freelancer_id(freelancer_id: str) -> List[Dict]:
        """Fetch all contracts for a freelancer"""
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            rows = db.fetch_data(
                table_name="contract",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts for freelancer {freelancer_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_contracts_by_client_id(client_id: str) -> List[Dict]:
        """Fetch all contracts for a client"""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="contract",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("CONTRACT_FUNCTIONS", f"Fetched {len(rows)} contracts for client {client_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error fetching contracts: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_contract(job_post_id: str, job_role_id: str, proposal_id: str, freelancer_id: str,
                        client_id: str, contract_title: str, agreed_budget: float, payment_structure: str,
                        start_date, contract_id: Optional[str] = None, role_title: Optional[str] = None,
                        budget_currency: Optional[str] = "USD", agreed_duration: Optional[str] = None,
                        status: Optional[str] = "active", end_date=None, actual_completion_date=None,
                        total_hours_worked: Optional[float] = None, total_paid: Optional[float] = 0) -> Dict:
        """Create a new contract"""
        try:
            db = get_db()
            contract_id = contract_id or str(uuid.uuid4())
            
            contract_data = {
                "contract_id": contract_id,
                "job_post_id": job_post_id,
                "job_role_id": job_role_id,
                "proposal_id": proposal_id,
                "freelancer_id": freelancer_id,
                "client_id": client_id,
                "contract_title": contract_title,
                "role_title": role_title,
                "agreed_budget": agreed_budget,
                "budget_currency": budget_currency,
                "payment_structure": payment_structure,
                "agreed_duration": agreed_duration,
                "status": status,
                "start_date": start_date,
                "end_date": end_date,
                "actual_completion_date": actual_completion_date,
                "total_hours_worked": total_hours_worked,
                "total_paid": total_paid
            }
            
            db.insert_data(table_name="contract", data=contract_data)
            
            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} created", level="INFO")
            return convert_uuids_to_str(contract_data)
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error creating contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_contract(contract_id: str, update_data: Dict) -> Optional[Dict]:
        """Update contract information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("CONTRACT_FUNCTIONS", "No data to update", level="WARNING")
                return ContractFunctions.get_contract_by_id(contract_id)
            
            conditions = [("contract_id", "=", contract_id)]
            db.update_data(table_name="contract", data=update_data, conditions=conditions)
            
            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} updated", level="INFO")
            return ContractFunctions.get_contract_by_id(contract_id)
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error updating contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_contract(contract_id: str) -> bool:
        """Delete a contract"""
        try:
            db = get_db()
            conditions = [("contract_id", "=", contract_id)]
            db.delete_data(table_name="contract", conditions=conditions)
            
            logger("CONTRACT_FUNCTIONS", f"Contract {contract_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("CONTRACT_FUNCTIONS", f"Error deleting contract: {str(e)}", level="ERROR")
            raise
