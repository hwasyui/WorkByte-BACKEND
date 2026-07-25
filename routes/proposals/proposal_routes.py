import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional
import uuid
from functions.schema_model import ProposalCreate, ProposalUpdate, ProposalResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.db_manager import get_db
from functions.access_control import assert_client_owns
from routes.proposals.proposal_functions import ProposalFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from routes.notifications.notification_functions import NotificationFunctions
from routes.admin.admin_moderation import scan_harmful_text_with_ml_fallback


proposal_router = APIRouter(prefix="/proposals", tags=["Proposals"])

# Whitelists for the freelancer proposal-list filters/sort. Anything outside these
# is a 400 - keeps bad values out of the SQL and off the enum comparisons.
_VALID_PROPOSAL_STATUSES = {"pending", "accepted", "rejected"}
_VALID_JOB_POST_STATUSES = {"draft", "active", "closed", "filled"}
_VALID_SORT_BY = {"submitted_at", "proposed_budget"}
_VALID_SORT_ORDER = {"asc", "desc"}


def _validate_proposal_filters(status, job_post_status, sort_by, sort_order):
    """Return an error message if any filter/sort value is invalid, else None."""
    if status is not None and status not in _VALID_PROPOSAL_STATUSES:
        return f"Invalid status '{status}'. Allowed: {', '.join(sorted(_VALID_PROPOSAL_STATUSES))}"
    if job_post_status is not None and job_post_status not in _VALID_JOB_POST_STATUSES:
        return f"Invalid job_post_status '{job_post_status}'. Allowed: {', '.join(sorted(_VALID_JOB_POST_STATUSES))}"
    if sort_by not in _VALID_SORT_BY:
        return f"Invalid sort_by '{sort_by}'. Allowed: {', '.join(sorted(_VALID_SORT_BY))}"
    if sort_order not in _VALID_SORT_ORDER:
        return f"Invalid sort_order '{sort_order}'. Allowed: {', '.join(sorted(_VALID_SORT_ORDER))}"
    return None


@proposal_router.get("", response_model=None)
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


@proposal_router.get("/me", response_model=None)
async def get_my_proposals(
    status: Optional[str] = None,
    job_post_status: Optional[str] = None,
    sort_by: str = "submitted_at",
    sort_order: str = "desc",
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer views their own proposals. Each row carries the job post's current
    status/title so a still-'pending' proposal on a closed job reads correctly.
    Optional filters: status (proposal), job_post_status; sort_by + sort_order."""
    try:
        err = _validate_proposal_filters(status, job_post_status, sort_by, sort_order)
        if err:
            return ResponseSchema.error(err, 400)

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found", 404)

        proposals = ProposalFunctions.get_proposals_by_freelancer_id(
            freelancer["freelancer_id"],
            proposal_status=status,
            job_post_status=job_post_status,
            sort_by=sort_by,
            sort_order=sort_order,
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
    """Client views all proposals for their job post, includes freelancer info."""
    try:
        job_row = get_db().execute_query(
            "SELECT client_id FROM job_post WHERE job_post_id = :jpid",
            {"jpid": job_post_id},
        )
        if not job_row:
            return ResponseSchema.error(f"Job post {job_post_id} not found", 404)
        assert_client_owns(current_user, str(job_row[0]["client_id"]))

        proposals = ProposalFunctions.get_proposals_by_job_post_id_enriched(job_post_id)
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for job post {job_post_id}", "GET /proposals/job-post/{job_post_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except HTTPException:
        raise
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals/job-post/{job_post_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/freelancer/{freelancer_id}", response_model=None)
async def get_proposals_by_freelancer(
    freelancer_id: str,
    status: Optional[str] = None,
    job_post_status: Optional[str] = None,
    sort_by: str = "submitted_at",
    sort_order: str = "desc",
    current_user: UserInDB = Depends(get_current_user),
):
    """All proposals for one freelancer account, enriched with each job post's
    status/title. Same optional filters/sort as GET /proposals/me."""
    try:
        err = _validate_proposal_filters(status, job_post_status, sort_by, sort_order)
        if err:
            return ResponseSchema.error(err, 400)

        proposals = ProposalFunctions.get_proposals_by_freelancer_id(
            freelancer_id,
            proposal_status=status,
            job_post_status=job_post_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for freelancer {freelancer_id}", "GET /proposals/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/{proposal_id}", response_model=None)
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


@proposal_router.post("", response_model=None, status_code=201)
async def create_proposal(
    proposal: ProposalCreate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer submits a proposal; freelancer_id is derived from token."""
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found for this account", 404)

        freelancer_id = freelancer["freelancer_id"]

        if current_user.client_id:
            job_row = get_db().execute_query(
                "SELECT client_id FROM job_post WHERE job_post_id = :jpid",
                {"jpid": str(proposal.job_post_id)}
            )
            if job_row:
                client_row = get_db().execute_query(
                    "SELECT user_id FROM client WHERE client_id = :cid",
                    {"cid": str(job_row[0]["client_id"])}
                )
                if client_row and str(client_row[0]["user_id"]) == str(current_user.user_id):
                    return ResponseSchema.error("You cannot apply to your own job post", 403)

        # A proposal must target one specific role (matches the FE flow). The model
        # already requires job_role_id; this also rejects an empty string.
        if not proposal.job_role_id:
            return ResponseSchema.error("A specific role is required to apply for this job.", 400)

        # One application per freelancer per job post: once you've applied to any role
        # in this post, you can't apply to another role in the same post.
        existing = ProposalFunctions.get_proposal_for_freelancer_job(
            freelancer_id=freelancer_id,
            job_post_id=str(proposal.job_post_id),
        )
        if existing:
            return ResponseSchema.error("You have already applied to this job post", 409)

        if proposal.cover_letter and proposal.cover_letter.strip():
            harm_result = scan_harmful_text_with_ml_fallback(proposal.cover_letter)
            if harm_result["is_flagged"]:
                labels = harm_result.get("detected_labels", [])
                logger("PROPOSAL", f"Blocked toxic proposal from freelancer {freelancer_id}, labels={labels}", "POST /proposals", "WARNING")
                return ResponseSchema.error(
                    f"Your proposal was not submitted. The cover letter was detected as harmful ({', '.join(labels)}).",
                    400,
                )

        new_proposal = ProposalFunctions.create_proposal(
            job_post_id=proposal.job_post_id,
            freelancer_id=freelancer_id,
            cover_letter=proposal.cover_letter,
            proposed_budget=proposal.proposed_budget,
            job_role_id=proposal.job_role_id,
            proposed_duration=proposal.proposed_duration,
            status="pending",  # a new proposal is always pending; only the client can accept/reject
            is_ai_generated=proposal.is_ai_generated,
        )

        # Notify client of new proposal
        try:
            job_row = get_db().execute_query(
                "SELECT client_id FROM job_post WHERE job_post_id = :jpid",
                {"jpid": str(proposal.job_post_id)}
            )
            if job_row:
                client = ClientFunctions.get_client_by_id(str(job_row[0]["client_id"]))
                if client:
                    await NotificationFunctions.notify(
                        recipient_user_id=str(client["user_id"]),
                        notif_type="new_proposal",
                        title="New Proposal Received",
                        body=f"{freelancer.get('full_name')} applied to your job",
                        data={
                            "proposal_id": new_proposal["proposal_id"],
                            "job_post_id": str(proposal.job_post_id),
                        },
                    )
        except Exception as notif_err:
            logger("PROPOSAL", f"New proposal notification failed (non-fatal): {notif_err}", "POST /proposals", "WARNING")

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
    """Update proposal status. Only the job's client can accept or reject a proposal.
    There is no withdraw, and freelancers cannot change status."""
    try:
        proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not proposal:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        is_proposal_client = False
        if current_user.client_id:
            job_row = get_db().execute_query(
                "SELECT client_id FROM job_post WHERE job_post_id = :jpid",
                {"jpid": str(proposal["job_post_id"])}
            )
            if job_row:
                client = ClientFunctions.get_client_by_user_id(current_user.user_id)
                if client and str(client["client_id"]) == str(job_row[0]["client_id"]):
                    is_proposal_client = True

        if not is_proposal_client:
            return ResponseSchema.error("Only the job's client can change a proposal's status", 403)
        if status not in ("accepted", "rejected"):
            return ResponseSchema.error("Status can only be set to 'accepted' or 'rejected'", 403)

        # A proposal is decided once: only a still-pending proposal can be accepted or
        # rejected. Blocks re-deciding a proposal that was already accepted/rejected.
        current_status = proposal.get("status")
        if current_status != "pending":
            return ResponseSchema.error(
                f"This proposal is already '{current_status}' and can no longer be changed", 409
            )

        updated = ProposalFunctions.update_proposal(proposal_id, {"status": status})

        # Notify freelancer on accept/reject
        if status in ("accepted", "rejected") and is_proposal_client:
            try:
                fl = FreelancerFunctions.get_freelancer_by_id(str(proposal["freelancer_id"]))
                cl = ClientFunctions.get_client_by_user_id(current_user.user_id)
                if fl and cl:
                    if status == "accepted":
                        notif_title = "Proposal Accepted 🎉"
                        notif_body = f"{cl.get('full_name')} accepted your proposal"
                        notif_type = "proposal_accepted"
                    else:
                        notif_title = "Proposal Rejected"
                        notif_body = f"{cl.get('full_name')} has declined your proposal"
                        notif_type = "proposal_rejected"

                    await NotificationFunctions.notify(
                        recipient_user_id=str(fl["user_id"]),
                        notif_type=notif_type,
                        title=notif_title,
                        body=notif_body,
                        data={
                            "proposal_id": proposal_id,
                            "job_post_id": str(proposal["job_post_id"]),
                        },
                    )
            except Exception as notif_err:
                logger("PROPOSAL", f"Status notification failed (non-fatal): {notif_err}", "PATCH /proposals/{proposal_id}/status", "WARNING")

        logger("PROPOSAL", f"Proposal {proposal_id} status → {status}", "PATCH /proposals/{proposal_id}/status", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to update status: {str(e)}", "PATCH /proposals/{proposal_id}/status", "ERROR")
        return ResponseSchema.error(f"Failed to update status: {str(e)}", 500)


# A proposal is immutable once submitted: no edit route and no delete route.
# It can only be accepted or rejected by the job's client (see PATCH .../status),
# or auto-rejected when the role fills / the job post closes.