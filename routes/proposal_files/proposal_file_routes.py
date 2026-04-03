import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ProposalFileCreate, ProposalFileUpdate, ProposalFileResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.proposal_files.proposal_file_functions import ProposalFileFunctions

proposal_file_router = APIRouter(prefix="/proposal-files", tags=["Proposal Files"])


@proposal_file_router.get("", response_model=List[ProposalFileResponse])
async def get_all_proposal_files(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all proposal files - Authenticated users only - JSON response"""
    try:
        proposal_files = ProposalFileFunctions.get_all_proposal_files(limit=limit)
        success_msg = f"Retrieved {len(proposal_files)} proposal files" + (f" (limit: {limit})" if limit else "")
        logger("PROPOSAL_FILE", success_msg, "GET /proposal-files", "INFO")
        return ResponseSchema.success(proposal_files, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposal files: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "GET /proposal-files", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_file_router.get("/{proposal_file_id}", response_model=ProposalFileResponse)
async def get_proposal_file(proposal_file_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single proposal file by ID - Authenticated users only - JSON response"""
    try:
        proposal_file = ProposalFileFunctions.get_proposal_file_by_id(proposal_file_id)
        if not proposal_file:
            error_msg = f"Proposal file {proposal_file_id} not found"
            logger("PROPOSAL_FILE", error_msg, "GET /proposal-files/{proposal_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved proposal file {proposal_file_id}"
        logger("PROPOSAL_FILE", success_msg, "GET /proposal-files/{proposal_file_id}", "INFO")
        return ResponseSchema.success(proposal_file, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposal file {proposal_file_id}: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "GET /proposal-files/{proposal_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_file_router.get("/proposal/{proposal_id}", response_model=List[ProposalFileResponse])
async def get_proposal_files_by_proposal(proposal_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all files for a specific proposal - Authenticated users only - JSON response"""
    try:
        proposal_files = ProposalFileFunctions.get_proposal_files_by_proposal_id(proposal_id)
        success_msg = f"Retrieved {len(proposal_files)} files for proposal {proposal_id}"
        logger("PROPOSAL_FILE", success_msg, "GET /proposal-files/proposal/{proposal_id}", "INFO")
        return ResponseSchema.success(proposal_files, 200)
    except Exception as e:
        error_msg = f"Failed to fetch files for proposal {proposal_id}: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "GET /proposal-files/proposal/{proposal_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_file_router.post("", response_model=ProposalFileResponse, status_code=201)
async def create_proposal_file(proposal_file: ProposalFileCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new proposal file - Authenticated users only - JSON body accepted"""
    try:
        proposal_file_id = proposal_file.proposal_file_id or str(uuid.uuid4())
        
        new_proposal_file = ProposalFileFunctions.create_proposal_file(
            proposal_id=proposal_file.proposal_id,
            file_url=proposal_file.file_url,
            file_type=proposal_file.file_type,
            file_name=proposal_file.file_name,
            file_size=proposal_file.file_size
        )
        
        success_msg = f"Created proposal file {proposal_file_id} for proposal {proposal_file.proposal_id}"
        logger("PROPOSAL_FILE", success_msg, "POST /proposal-files", "INFO")
        return ResponseSchema.success(new_proposal_file, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "POST /proposal-files", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create proposal file: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "POST /proposal-files", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_file_router.put("/{proposal_file_id}", response_model=ProposalFileResponse)
async def update_proposal_file(proposal_file_id: str, proposal_file_update: ProposalFileUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update proposal file information - Authenticated users only"""
    try:
        existing_proposal_file = ProposalFileFunctions.get_proposal_file_by_id(proposal_file_id)
        if not existing_proposal_file:
            error_msg = f"Proposal file {proposal_file_id} not found"
            logger("PROPOSAL_FILE", error_msg, "PUT /proposal-files/{proposal_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = proposal_file_update.model_dump(exclude_unset=True)
        updated_proposal_file = ProposalFileFunctions.update_proposal_file(proposal_file_id, update_data)
        
        success_msg = f"Updated proposal file {proposal_file_id}"
        logger("PROPOSAL_FILE", success_msg, "PUT /proposal-files/{proposal_file_id}", "INFO")
        return ResponseSchema.success(updated_proposal_file, 200)
    except Exception as e:
        error_msg = f"Failed to update proposal file {proposal_file_id}: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "PUT /proposal-files/{proposal_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_file_router.delete("/{proposal_file_id}", status_code=200)
async def delete_proposal_file(proposal_file_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a proposal file - Authenticated users only"""
    try:
        existing_proposal_file = ProposalFileFunctions.get_proposal_file_by_id(proposal_file_id)
        if not existing_proposal_file:
            error_msg = f"Proposal file {proposal_file_id} not found"
            logger("PROPOSAL_FILE", error_msg, "DELETE /proposal-files/{proposal_file_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        ProposalFileFunctions.delete_proposal_file(proposal_file_id)
        
        success_msg = f"Deleted proposal file {proposal_file_id}"
        logger("PROPOSAL_FILE", success_msg, "DELETE /proposal-files/{proposal_file_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete proposal file {proposal_file_id}: {str(e)}"
        logger("PROPOSAL_FILE", error_msg, "DELETE /proposal-files/{proposal_file_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
