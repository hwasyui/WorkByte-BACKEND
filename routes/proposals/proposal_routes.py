import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional
import uuid
from functions.schema_model import ProposalCreate, ProposalUpdate, ProposalResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.proposals.proposal_functions import ProposalFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions

proposal_router = APIRouter(prefix="/proposals", tags=["Proposals"])


@proposal_router.get("", response_model=List[ProposalResponse])
async def get_all_proposals(
    limit: Optional[int] = None,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        proposals = ProposalFunctions.get_all_proposals(limit=limit)
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals", "GET /proposals", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/me", response_model=List[ProposalResponse])
async def get_my_proposals(current_user: UserInDB = Depends(get_current_user)):
    """Freelancer views their own proposals"""
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found", 404)

        proposals = ProposalFunctions.get_proposals_by_freelancer_id(
            freelancer["freelancer_id"]
        )
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for user {current_user.user_id}", "GET /proposals/me", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch my proposals: {str(e)}", "GET /proposals/me", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/job-post/{job_post_id}")
async def get_proposals_by_job_post(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Client views all proposals for their job post — includes freelancer info"""
    try:
        proposals = ProposalFunctions.get_proposals_by_job_post_id_enriched(job_post_id)
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for job post {job_post_id}", "GET /proposals/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/freelancer/{freelancer_id}", response_model=List[ProposalResponse])
async def get_proposals_by_freelancer(
    freelancer_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        proposals = ProposalFunctions.get_proposals_by_freelancer_id(freelancer_id)
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for freelancer {freelancer_id}", "GET /proposals/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(
    proposal_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not proposal:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)
        logger("PROPOSAL", f"Retrieved proposal {proposal_id}", "GET /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success(proposal, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposal: {str(e)}", "GET /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposal: {str(e)}", 500)


@proposal_router.post("", response_model=ProposalResponse, status_code=201)
async def create_proposal(
    proposal: ProposalCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer submits a proposal — freelancer_id is derived from token"""
    try:
        # Derive freelancer_id from the logged-in user
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found for this account", 404)

        freelancer_id = freelancer["freelancer_id"]

        # Duplicate check
        existing = ProposalFunctions.get_proposals_by_freelancer_id(freelancer_id)
        for p in existing:
            if proposal.job_role_id and str(p.get("job_role_id")) == str(proposal.job_role_id):
                return ResponseSchema.error("You have already submitted a proposal for this role", 409)
            if not proposal.job_role_id and str(p.get("job_post_id")) == str(proposal.job_post_id):
                return ResponseSchema.error("You have already submitted a proposal for this job", 409)

        new_proposal = ProposalFunctions.create_proposal(
            job_post_id=proposal.job_post_id,
            freelancer_id=freelancer_id,
            cover_letter=proposal.cover_letter,
            proposed_budget=proposal.proposed_budget,
            job_role_id=proposal.job_role_id,
            proposed_duration=proposal.proposed_duration,
            status=proposal.status if proposal.status else "pending",
            is_ai_generated=proposal.is_ai_generated,
        )

        logger("PROPOSAL", f"Proposal created by freelancer {freelancer_id}", "POST /proposals", "INFO")
        return ResponseSchema.success(new_proposal, 201)
    except Exception as e:
        logger("PROPOSAL", f"Failed to create proposal: {str(e)}", "POST /proposals", "ERROR")
        return ResponseSchema.error(f"Failed to create proposal: {str(e)}", 500)


@proposal_router.patch("/{proposal_id}/status")
async def update_proposal_status(
    proposal_id: str,
    status: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Update proposal status.
    - Client: can set 'accepted' or 'rejected'
    - Freelancer: can only set 'withdrawn' (on their own proposal)
    """
    try:
        proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not proposal:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        user_type = current_user.type  # 'client' or 'freelancer'

        if user_type == "freelancer":
            # Freelancer can only withdraw their own proposal
            freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
            if not freelancer or freelancer["freelancer_id"] != proposal["freelancer_id"]:
                return ResponseSchema.error("You can only withdraw your own proposals", 403)
            if status != "withdrawn":
                return ResponseSchema.error("Freelancers can only set status to 'withdrawn'", 403)

        elif user_type == "client":
            if status not in ("accepted", "rejected"):
                return ResponseSchema.error("Clients can only set status to 'accepted' or 'rejected'", 403)
        else:
            return ResponseSchema.error("Unauthorized", 403)

        updated = ProposalFunctions.update_proposal(proposal_id, {"status": status})
        logger("PROPOSAL", f"Proposal {proposal_id} status → {status}", "PATCH /proposals/{proposal_id}/status", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to update status: {str(e)}", "PATCH /proposals/{proposal_id}/status", "ERROR")
        return ResponseSchema.error(f"Failed to update status: {str(e)}", 500)


@proposal_router.put("/{proposal_id}", response_model=ProposalResponse)
async def update_proposal(
    proposal_id: str,
    proposal_update: ProposalUpdate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer edits their own pending proposal"""
    try:
        existing = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not existing:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        # Only the owning freelancer can edit
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer or freelancer["freelancer_id"] != existing["freelancer_id"]:
            return ResponseSchema.error("You can only edit your own proposals", 403)

        if existing["status"] != "pending":
            return ResponseSchema.error("Only pending proposals can be edited", 400)

        update_data = proposal_update.model_dump(exclude_unset=True)
        updated = ProposalFunctions.update_proposal(proposal_id, update_data)
        logger("PROPOSAL", f"Proposal {proposal_id} updated", "PUT /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to update proposal: {str(e)}", "PUT /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(f"Failed to update proposal: {str(e)}", 500)


@proposal_router.delete("/{proposal_id}", status_code=200)
async def delete_proposal(
    proposal_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        existing = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not existing:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        ProposalFunctions.delete_proposal(proposal_id)
        logger("PROPOSAL", f"Proposal {proposal_id} deleted", "DELETE /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to delete proposal: {str(e)}", "DELETE /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(f"Failed to delete proposal: {str(e)}", 500)