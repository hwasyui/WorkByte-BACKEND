import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
import uuid
from functions.schema_model import ProposalCreate, ProposalUpdate, ProposalResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_freelancer_profile_complete
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.db_manager import get_db
from routes.proposals.proposal_functions import ProposalFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from routes.notifications.notification_functions import NotificationFunctions


proposal_router = APIRouter(prefix="/proposals", tags=["Proposals"])

_VALID_PROPOSAL_ORDER_BY = {"submitted_at", "proposed_budget", "total_jobs"}


@proposal_router.get("", response_model=List[ProposalResponse])
async def get_all_proposals(
    limit: Optional[int] = None,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        proposals = ProposalFunctions.get_all_proposals(limit=limit, visible_only=True)
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals", "GET /proposals", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch proposals: {str(e)}", "GET /proposals", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/me", response_model=List[ProposalResponse])
async def get_my_proposals(current_user: UserInDB = Depends(get_current_user)):
    """Freelancer views their own proposals."""
    try:
        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found", 404)

        proposals = ProposalFunctions.get_proposals_by_freelancer_id(
            freelancer["freelancer_id"], visible_only=False
        )
        logger("PROPOSAL", f"Retrieved {len(proposals)} proposals for user {current_user.user_id}", "GET /proposals/me", "INFO")
        return ResponseSchema.success(proposals, 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to fetch my proposals: {str(e)}", "GET /proposals/me", "ERROR")
        return ResponseSchema.error(f"Failed to fetch proposals: {str(e)}", 500)


@proposal_router.get("/job-post/{job_post_id}")
async def get_proposals_by_job_post(
    job_post_id: str,
    order_by: str = Query(default="submitted_at", description="submitted_at (default), proposed_budget, total_jobs"),
    order_dir: str = Query(default="desc", description="asc or desc", pattern="^(asc|desc)$"),
    current_user: UserInDB = Depends(get_current_user),
):
    """Client views all proposals for their own job post, includes freelancer info."""
    try:
        if order_by not in _VALID_PROPOSAL_ORDER_BY:
            return ResponseSchema.error(f"Invalid order_by '{order_by}'. Valid values: {', '.join(sorted(_VALID_PROPOSAL_ORDER_BY))}", 400)

        job_post_row = get_db().execute_query(
            "SELECT client_id FROM job_post WHERE job_post_id = :jpid",
            {"jpid": job_post_id}
        )
        if not job_post_row:
            return ResponseSchema.error(f"Job post {job_post_id} not found", 404)

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client or str(client["client_id"]) != str(job_post_row[0]["client_id"]):
            return ResponseSchema.error("You can only view proposals for your own job post", 403)

        proposals = ProposalFunctions.get_proposals_by_job_post_id_enriched(
            job_post_id, visible_only=True, order_by=order_by, order_dir=order_dir
        )
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
        proposals = ProposalFunctions.get_proposals_by_freelancer_id(freelancer_id, visible_only=True)
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

        if proposal.get("moderation_status") != "visible":
            freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
            is_owner = freelancer and str(freelancer["freelancer_id"]) == str(proposal["freelancer_id"])
            if not is_owner:
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
    """Freelancer submits a proposal; freelancer_id is derived from token."""
    try:
        if current_user.is_report_banned:
            return ResponseSchema.error("Your account is restricted and cannot submit new proposals", 403)

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found for this account", 404)
        assert_freelancer_profile_complete(freelancer)

        freelancer_id = freelancer["freelancer_id"]

        job_post_row = get_db().execute_query(
            "SELECT client_id, status FROM job_post WHERE job_post_id = :jpid",
            {"jpid": str(proposal.job_post_id)}
        )
        if not job_post_row:
            return ResponseSchema.error("Job post not found", 404)
        if job_post_row[0]["status"] != "active":
            return ResponseSchema.error("This job post is no longer accepting proposals", 400)

        if current_user.client_id:
            client_row = get_db().execute_query(
                "SELECT user_id FROM client WHERE client_id = :cid",
                {"cid": str(job_post_row[0]["client_id"])}
            )
            if client_row and str(client_row[0]["user_id"]) == str(current_user.user_id):
                return ResponseSchema.error("You cannot apply to your own job post", 403)

        if proposal.job_role_id:
            existing = ProposalFunctions.get_proposal_for_freelancer_role(
                freelancer_id=freelancer_id,
                job_post_id=str(proposal.job_post_id),
                job_role_id=str(proposal.job_role_id),
            )
            duplicate_message = "You have already submitted a proposal for this role"
        else:
            existing = ProposalFunctions.get_proposal_for_freelancer_job(
                freelancer_id=freelancer_id,
                job_post_id=str(proposal.job_post_id),
            )
            duplicate_message = "You have already submitted a proposal for this job"

        if existing:
            return ResponseSchema.error(duplicate_message, 409)
        try:
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
        except IntegrityError:
            return ResponseSchema.error(duplicate_message, 409)
        try:
            await NotificationFunctions.notify(
                recipient_user_id=str(current_user.user_id),
                notif_type="proposal_submitted",
                title="Proposal Submitted",
                body="Your proposal has been submitted and is being processed.",
                data={"proposal_id": new_proposal["proposal_id"]},
            )
        except Exception as notif_err:
            logger("PROPOSAL", f"Proposal-submitted notification failed (non-fatal): {notif_err}", "POST /proposals", "WARNING")

        asyncio.create_task(ProposalFunctions.run_proposal_scan(
            new_proposal["proposal_id"], proposal.cover_letter, str(current_user.user_id)
        ))

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
    except HTTPException as e:
        logger("PROPOSAL", f"HTTP {e.status_code}: {e.detail}", "POST /proposals", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("PROPOSAL", f"Failed to create proposal: {str(e)}", "POST /proposals", "ERROR")
        return ResponseSchema.error(f"Failed to create proposal: {str(e)}", 500)


@proposal_router.patch("/{proposal_id}/status")
async def update_proposal_status(
    proposal_id: str,
    status: str,
    current_user: UserInDB = Depends(get_current_user),
):
    """Update proposal status.

    Clients can set 'accepted' or 'rejected'; freelancers can only set 'withdrawn' on their own proposal.
    """
    try:
        proposal = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not proposal:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        is_proposal_freelancer = False
        is_proposal_client = False

        if current_user.freelancer_id:
            freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
            if freelancer and str(freelancer["freelancer_id"]) == str(proposal["freelancer_id"]):
                is_proposal_freelancer = True

        job_row = get_db().execute_query(
            "SELECT client_id, status FROM job_post WHERE job_post_id = :jpid",
            {"jpid": str(proposal["job_post_id"])}
        )

        if current_user.client_id and job_row:
            client = ClientFunctions.get_client_by_user_id(current_user.user_id)
            if client and str(client["client_id"]) == str(job_row[0]["client_id"]):
                is_proposal_client = True

        if not (is_proposal_freelancer or is_proposal_client):
            return ResponseSchema.error("Unauthorized", 403)

        # Once a proposal has moved off 'pending' (accepted/rejected/withdrawn), its
        # status is final - neither side may flip it again. This matters most for
        # 'accepted': a contract may already reference it (see POST /contracts),
        # so silently reopening it here would desync the two records.
        if proposal["status"] != "pending":
            return ResponseSchema.error(
                f"This proposal has already been decided (current status: {proposal['status']}) and can no longer be changed", 400
            )

        if is_proposal_freelancer:
            if status != "withdrawn":
                return ResponseSchema.error("Freelancers can only set status to 'withdrawn'", 403)
        elif is_proposal_client:
            if status not in ("accepted", "rejected"):
                return ResponseSchema.error("Clients can only set status to 'accepted' or 'rejected'", 403)
            # Rejecting is fine even while restricted (it's not a new commitment, and
            # it frees the freelancer to look elsewhere); only block the action that
            # starts new paid work.
            if status == "accepted" and current_user.is_report_banned:
                return ResponseSchema.error("Your account is restricted and cannot accept new proposals", 403)
            if proposal.get("moderation_status") != "visible":
                return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)
            if job_row and job_row[0]["status"] != "active":
                return ResponseSchema.error("This job post is no longer active", 400)
            if status == "accepted" and proposal.get("job_role_id"):
                role_row = get_db().execute_query(
                    "SELECT positions_filled, positions_available FROM job_role WHERE job_role_id = :jrid",
                    {"jrid": str(proposal["job_role_id"])},
                )
                if role_row and role_row[0]["positions_filled"] >= role_row[0]["positions_available"]:
                    return ResponseSchema.error("This role has already been fully staffed", 409)

        # Atomic status-flip guarded by WHERE status='pending' - the check above
        # (line ~275) is a plain read, so a client's accept and the freelancer's
        # withdraw can still both pass it if they land close together. This is
        # the real guard: only one of the two competing requests can match a
        # still-pending row.
        updated = ProposalFunctions.update_status_if_pending(proposal_id, status)
        if not updated:
            return ResponseSchema.error(
                "This proposal was just decided by the other party and can no longer be changed", 409
            )

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


@proposal_router.put("/{proposal_id}", response_model=ProposalResponse)
async def update_proposal(
    proposal_id: str,
    proposal_update: ProposalUpdate,
    current_user: UserInDB = Depends(get_current_user),
):
    """Freelancer edits their own pending proposal."""
    try:
        existing = ProposalFunctions.get_proposal_by_id(proposal_id)
        if not existing:
            return ResponseSchema.error(f"Proposal {proposal_id} not found", 404)

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer or freelancer["freelancer_id"] != existing["freelancer_id"]:
            return ResponseSchema.error("You can only edit your own proposals", 403)

        if existing["status"] != "pending":
            return ResponseSchema.error("Only pending proposals can be edited", 400)

        if existing["moderation_status"] == "scanning":
            return ResponseSchema.error("Proposal is still being reviewed, please wait before editing", 409)

        update_data = proposal_update.model_dump(exclude_unset=True)
        updated = ProposalFunctions.update_proposal(proposal_id, update_data)

        # re-scan on every edit, not just cover_letter changes, since the row is edited in place
        new_cover_letter = update_data.get("cover_letter", existing["cover_letter"])
        asyncio.create_task(ProposalFunctions.run_proposal_scan(
            proposal_id, new_cover_letter, str(current_user.user_id)
        ))

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

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer or str(freelancer["freelancer_id"]) != str(existing["freelancer_id"]):
            return ResponseSchema.error("You can only delete your own proposals", 403)

        # Same invariant as PATCH /proposals/{id}/status: once a proposal has been
        # decided (accepted/rejected/withdrawn), it's final - deleting an 'accepted'
        # one here would let it vanish out from under a client who already accepted
        # it (and could still race a contract that references it).
        if existing["status"] != "pending":
            return ResponseSchema.error(
                f"Only a pending proposal can be deleted (current status: {existing['status']})", 400
            )

        ProposalFunctions.delete_proposal(proposal_id)
        logger("PROPOSAL", f"Proposal {proposal_id} deleted", "DELETE /proposals/{proposal_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        logger("PROPOSAL", f"Failed to delete proposal: {str(e)}", "DELETE /proposals/{proposal_id}", "ERROR")
        return ResponseSchema.error(f"Failed to delete proposal: {str(e)}", 500)