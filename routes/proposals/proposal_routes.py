import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ProposalCreate, ProposalUpdate, ProposalResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.proposals.proposal_functions import ProposalFunctions

proposal_router = APIRouter(prefix="/proposals", tags=["Proposals"])


@proposal_router.get("", response_model=List[ProposalResponse])
async def get_all_proposals(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all proposals - Authenticated users only - JSON response"""
    try:
        proposals = ProposalFunctions.get_all_proposals(limit=limit)
        success_msg = f"Retrieved {len(proposals)} proposals" + (f" (limit: {limit})" if limit else "")
        logger("PROPOSAL", success_msg, "GET /proposals", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposals: {str(e)}"
        logger("PROPOSAL", error_msg, "GET /proposals", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.get("/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(proposal_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single proposal by ID - Authenticated users only - JSON response"""
    try:
        proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not proposal:
            error_msg = f"Proposal {proposal_id} not found"
            logger("PROPOSAL", error_msg, "GET /proposals/{proposal_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved proposal {proposal_id}"
        logger("PROPOSAL", success_msg, "GET /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success(proposal, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposal {proposal_id}: {str(e)}"
        logger("PROPOSAL", error_msg, "GET /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.get("/job-post/{job_post_id}", response_model=List[ProposalResponse])
async def get_proposals_by_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all proposals for a specific job post - Authenticated users only - JSON response"""
    try:
        proposals = ProposalFunctions.get_proposals_by_job_post_id(job_post_id)
        success_msg = f"Retrieved {len(proposals)} proposals for job post {job_post_id}"
        logger("PROPOSAL", success_msg, "GET /proposals/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposals for job post {job_post_id}: {str(e)}"
        logger("PROPOSAL", error_msg, "GET /proposals/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.get("/freelancer/{freelancer_id}", response_model=List[ProposalResponse])
async def get_proposals_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all proposals from a specific freelancer - Authenticated users only - JSON response"""
    try:
        proposals = ProposalFunctions.get_proposals_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(proposals)} proposals from freelancer {freelancer_id}"
        logger("PROPOSAL", success_msg, "GET /proposals/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        error_msg = f"Failed to fetch proposals from freelancer {freelancer_id}: {str(e)}"
        logger("PROPOSAL", error_msg, "GET /proposals/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.post("", response_model=ProposalResponse, status_code=201)
async def create_proposal(proposal: ProposalCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new proposal - Authenticated users only - JSON body accepted"""
    try:
        proposal_id = proposal.proposal_id or str(uuid.uuid4())
        
        new_proposal = ProposalFunctions.create_proposal(
            job_post_id=proposal.job_post_id,
            freelancer_id=proposal.freelancer_id,
            cover_letter=proposal.cover_letter,
            proposed_budget=proposal.proposed_budget,
            job_role_id=proposal.job_role_id,
            proposed_duration=proposal.proposed_duration,
            status=proposal.status,
            is_ai_generated=proposal.is_ai_generated
        )
        
        success_msg = f"Created proposal {proposal_id} from freelancer {proposal.freelancer_id}"
        logger("PROPOSAL", success_msg, "POST /proposals", "INFO")
        return ResponseSchema.success(new_proposal, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("PROPOSAL", error_msg, "POST /proposals", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create proposal: {str(e)}"
        logger("PROPOSAL", error_msg, "POST /proposals", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.put("/{proposal_id}", response_model=ProposalResponse)
async def update_proposal(proposal_id: str, proposal_update: ProposalUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update proposal information - Authenticated users only"""
    try:
        existing_proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not existing_proposal:
            error_msg = f"Proposal {proposal_id} not found"
            logger("PROPOSAL", error_msg, "PUT /proposals/{proposal_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        update_data = proposal_update.model_dump(exclude_unset=True)
        updated_proposal = ProposalFunctions.update_proposal(proposal_id, update_data)
        
        success_msg = f"Updated proposal {proposal_id}"
        logger("PROPOSAL", success_msg, "PUT /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success(updated_proposal, 200)
    except Exception as e:
        error_msg = f"Failed to update proposal {proposal_id}: {str(e)}"
        logger("PROPOSAL", error_msg, "PUT /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@proposal_router.delete("/{proposal_id}", status_code=200)
async def delete_proposal(proposal_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a proposal - Authenticated users only"""
    try:
        existing_proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not existing_proposal:
            error_msg = f"Proposal {proposal_id} not found"
            logger("PROPOSAL", error_msg, "DELETE /proposals/{proposal_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        
        ProposalFunctions.delete_proposal(proposal_id)
        
        success_msg = f"Deleted proposal {proposal_id}"
        logger("PROPOSAL", success_msg, "DELETE /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete proposal {proposal_id}: {str(e)}"
        logger("PROPOSAL", error_msg, "DELETE /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
