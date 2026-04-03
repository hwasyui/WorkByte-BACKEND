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


class ProposalFileFunctions:
    """Handle all proposal file-related database operations"""

    @staticmethod
    def get_all_proposal_files(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all proposal files"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="proposal_file",
                columns=["proposal_file_id", "proposal_id", "file_url", "file_type", "file_name", "file_size", "created_at"],
                order_by="created_at DESC",
                limit=limit,
                offset=offset
            )
            
            logger("PROPOSAL_FILE_FUNCTIONS", f"Fetched {len(rows)} proposal files", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error fetching proposal files: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_file_by_id(proposal_file_id: str) -> Optional[Dict]:
        """Fetch a proposal file by ID"""
        try:
            db = get_db()
            conditions = [("proposal_file_id", "=", proposal_file_id)]
            rows = db.fetch_data(
                table_name="proposal_file",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("PROPOSAL_FILE_FUNCTIONS", f"Proposal file {proposal_file_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error fetching proposal file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proposal_files_by_proposal_id(proposal_id: str) -> List[Dict]:
        """Fetch all files for a proposal"""
        try:
            db = get_db()
            conditions = [("proposal_id", "=", proposal_id)]
            rows = db.fetch_data(
                table_name="proposal_file",
                conditions=conditions,
                order_by="created_at DESC"
            )
            
            logger("PROPOSAL_FILE_FUNCTIONS", f"Fetched {len(rows)} files for proposal {proposal_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error fetching proposal files: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_proposal_file(proposal_id: str, file_url: str, file_type: str,
                             file_name: str, file_size: Optional[int] = None) -> Dict:
        """Create a new proposal file"""
        try:
            db = get_db()
            proposal_file_id = str(uuid.uuid4())
            
            proposal_file_data = {
                "proposal_file_id": proposal_file_id,
                "proposal_id": proposal_id,
                "file_url": file_url,
                "file_type": file_type,
                "file_name": file_name,
                "file_size": file_size
            }
            
            db.insert_data(table_name="proposal_file", data=proposal_file_data)
            
            logger("PROPOSAL_FILE_FUNCTIONS", f"Proposal file {proposal_file_id} created", level="INFO")
            return convert_uuids_to_str(proposal_file_data)
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error creating proposal file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_proposal_file(proposal_file_id: str, update_data: Dict) -> Optional[Dict]:
        """Update proposal file information"""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("PROPOSAL_FILE_FUNCTIONS", "No data to update", level="WARNING")
                return ProposalFileFunctions.get_proposal_file_by_id(proposal_file_id)
            
            conditions = [("proposal_file_id", "=", proposal_file_id)]
            db.update_data(table_name="proposal_file", data=update_data, conditions=conditions)
            
            logger("PROPOSAL_FILE_FUNCTIONS", f"Proposal file {proposal_file_id} updated", level="INFO")
            return ProposalFileFunctions.get_proposal_file_by_id(proposal_file_id)
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error updating proposal file: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_proposal_file(proposal_file_id: str) -> bool:
        """Delete a proposal file"""
        try:
            db = get_db()
            conditions = [("proposal_file_id", "=", proposal_file_id)]
            db.delete_data(table_name="proposal_file", conditions=conditions)
            
            logger("PROPOSAL_FILE_FUNCTIONS", f"Proposal file {proposal_file_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("PROPOSAL_FILE_FUNCTIONS", f"Error deleting proposal file: {str(e)}", level="ERROR")
            raise
