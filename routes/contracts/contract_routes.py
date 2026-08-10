import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dateutil.relativedelta import relativedelta
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from fastapi import APIRouter, Body, Depends, Response, BackgroundTasks, HTTPException
from sqlalchemy.exc import IntegrityError
from functions.minio_client import (
    download_file,
    upload_thread_attachment,
    resolve_file_url,
    BUCKET_MESSAGE_ATTACHMENTS,
)
from routes.reviews.review_routes import trigger_review_pipeline_on_completion
from routes.client_reviews.client_review_routes import trigger_client_review_pipeline_on_completion
from typing import Dict, List, Optional
import uuid
from functions.schema_model import CancelContractRequest, ContractCreate, ContractUpdate, ContractResponse, ContractSendRequest, RaiseDisputeRequest
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import (
    assert_current_user_is_contract_party,
    assert_client_owns,
    assert_freelancer_owns,
    get_client_profile_for_user,
    get_freelancer_profile_for_user,
)
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.db_manager import get_db
from routes.contracts.contract_functions import ContractFunctions, MAX_ACTIVE_CONTRACTS_PER_FREELANCER
from routes.contracts.contract_generation_functions import ContractGenerationFunctions, CONTRACT_BUCKET
from routes.contracts.milestone_functions import MilestoneFunctions
from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.proposals.proposal_functions import ProposalFunctions
from routes.job_roles.job_role_functions import JobRoleFunctions
from routes.dm.dm_functions import DMFunctions, _contract_accepted_default
from routes.notifications.notification_functions import NotificationFunctions
from routes.admin.admin_moderation import scan_harmful_text, scan_harmful_text_with_ml_fallback
from ai_related.job_engine.embedding_manager import mark_contract_dirty


def _reject_contract_short_text_if_harmful(*fields: Optional[str]) -> Optional[Dict]:
    """contract_title/role_title take the keyword scan, the same rule every other short
    field in the system follows. Synchronous since scan_harmful_text never awaits
    anything."""
    combined = " ".join(f for f in fields if f)
    if not combined.strip():
        return None
    harm_result = scan_harmful_text(combined)
    if not harm_result["is_flagged"]:
        return None
    detected_labels = harm_result.get("detected_labels", [])
    logger("CONTRACT", f"Blocked contract save, labels={detected_labels}", level="WARNING")
    return {
        "message": "This contract couldn't be saved. It was flagged by Harmful Text Detection.",
        "detected_labels": detected_labels,
    }


_MAX_REASON_LENGTH = 2000


def _reject_reason_if_invalid(reason: Optional[str], action: str) -> Optional[Dict]:
    """Cancellation and dispute reasons are free prose that gets persisted to the DM
    thread and surfaced in the admin arbitration queue, so they take the same ML-backed
    scan as every other prose field (DMs, cover letters) rather than the keyword-only
    path the short title fields use."""
    if not reason or not reason.strip():
        return None
    if len(reason) > _MAX_REASON_LENGTH:
        return {"message": f"Your {action} reason is too long. Keep it under {_MAX_REASON_LENGTH:,} characters."}
    harm_result = scan_harmful_text_with_ml_fallback(reason)
    if not harm_result["is_flagged"]:
        return None
    detected_labels = harm_result.get("detected_labels", [])
    logger("CONTRACT", f"Blocked {action} reason, labels={detected_labels}", level="WARNING")
    return {
        "message": f"Your {action} reason was not accepted. It was flagged by Harmful Text Detection.",
        "detected_labels": detected_labels,
    }


def _harmful_extra(rejection: Dict) -> Optional[Dict]:
    """Only a harmful-text rejection carries labels; a length rejection has none."""
    if "detected_labels" not in rejection:
        return None
    return {"blocked_by": "harmful_text", "detected_labels": rejection["detected_labels"]}


def _cancellation_was_by_a_party(contract: Dict) -> bool:
    """cancelled_by is a bare user_id with no role attached. An admin resolving a dispute
    with outcome='cancel' writes their own id into it, and checking the id against the two
    parties is the only way to tell that apart from a party cancelling on their own."""
    cancelled_by = str(contract.get("cancelled_by") or "")
    if not cancelled_by:
        return False
    try:
        cl = ClientFunctions.get_client_by_id(str(contract["client_id"]))
        fl = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
    except Exception as e:
        # Can't resolve the parties - let the dispute through rather than trap someone
        # behind a lookup failure. An admin still reviews it.
        logger("CONTRACT", f"Could not resolve contract parties for cancellation check: {e}", level="WARNING")
        return True
    if not cl or not fl:
        # A missing profile would shrink the set below and make a party's own cancellation
        # look like an admin's, permanently blocking the other party's dispute. Fail open.
        logger("CONTRACT", f"Contract {contract.get('contract_id')} has an unresolvable party; allowing dispute", level="WARNING")
        return True
    return cancelled_by in {str(cl["user_id"]), str(fl["user_id"])}


# A contract in front of an admin. Field edits and deletes are refused in this state so
# the record can't move under an arbitration in progress.
_ARBITRATION_LOCKED_STATUSES = {"disputed"}


def _cancellation_dispute_window_open(contract_id: str, contract: Dict) -> bool:
    """Whether the party who did not cancel can still dispute. Anchored on the
    cancellation system event rather than contract.updated_at, which a trigger moves on
    every later write to the row and which would silently restart the 72h clock. Open by
    default when neither timestamp resolves - an admin reviews the dispute either way."""
    cancelled_at = ContractFunctions.get_cancelled_at(contract_id) or contract.get("updated_at")
    if isinstance(cancelled_at, str):
        cancelled_at = datetime.fromisoformat(cancelled_at)
    if not cancelled_at:
        return True
    if cancelled_at.tzinfo is None:
        cancelled_at = cancelled_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - cancelled_at <= _CANCELLATION_DISPUTE_WINDOW


# agreed_duration is a number plus a unit from a locked dropdown, not free prose.
# A value in this shape can't carry harmful text, so a format check is enough.
_DURATION_FORMAT_RE = re.compile(r"^\d+\s+(day|days|week|weeks|month|months)$", re.IGNORECASE)


def _reject_contract_duration_if_invalid(agreed_duration: Optional[str]) -> Optional[Dict]:
    if not agreed_duration or not agreed_duration.strip():
        return None
    if _DURATION_FORMAT_RE.match(agreed_duration.strip()):
        return None
    return {"message": "agreed_duration must look like '<number> days|weeks|months' (e.g. '3 months')."}


def _reject_generation_terms_if_invalid(terms) -> Optional[str]:
    """Validate the terms block a contract is generated from."""
    if terms.termination_notice not in {7, 14, 30}:
        return "Termination notice must be 7, 14, or 30 days."
    if terms.dispute_resolution not in {"negotiation", "mediation", "arbitration"}:
        return "Choose a dispute resolution method: negotiation, mediation, or arbitration."
    return None


def _derive_end_date(start_date, agreed_duration: Optional[str]):
    """The end date is the start date plus the agreed duration, not a separate answer.

    agreed_duration is fixed by the accepted proposal, so letting the client also
    pick an end date allowed a contract to state "3 weeks" beside a date three
    months out - and both are printed on the PDF. Deriving it removes the
    contradiction rather than adding a check for it.

    "3 weeks from the 5th" is read as the deadline, so the result is start + the
    full duration: work delivered on that date is on time. Months are added
    calendrically, not as 30-day blocks, so a month-long contract starting on the
    31st ends on the last day of the next month rather than slipping into the one
    after.

    Returns None when either input is missing or the duration is malformed; the
    caller then keeps whatever end date it already had.
    """
    if not start_date or not agreed_duration:
        return None
    match = _DURATION_FORMAT_RE.match(agreed_duration.strip())
    if not match:
        return None

    amount = int(agreed_duration.strip().split()[0])
    unit = match.group(1).lower().rstrip("s")
    if unit == "day":
        return start_date + timedelta(days=amount)
    if unit == "week":
        return start_date + timedelta(weeks=amount)
    return start_date + relativedelta(months=amount)


_DEFAULT_CONTRACT_NOTIFICATION = (
    "Hello {freelancer_name},\n\n"
    'The contract for "{contract_title}" ({role_title}) has been finalized '
    "and is ready for your review.\n\n"
    "I attached the contract PDF below.\n\n"
    "Looking forward to working with you!"
)


def _render_notification(template: str, subs: dict) -> str:
    for key, val in subs.items():
        template = template.replace(f"{{{key}}}", str(val) if val else "")
    template = template.replace("{pdf_url}", "").strip()

    return template

# Printed on the contract PDF, which is rendered once at creation and never again, so
# these can no longer change. end_date is excluded on purpose: extensions move it while
# the document keeps the originally agreed deadline, preserved in original_end_date.
_PDF_FROZEN_FIELDS = {
    "contract_title", "role_title", "agreed_budget", "budget_currency",
    "agreed_duration", "start_date",
}

_CANCELLATION_DISPUTE_WINDOW = timedelta(hours=72)

_CENTS = Decimal("0.01")


async def _announce_contract_started(contract: Dict) -> None:
    """Open the DM thread and tell the freelancer the contract has begun.

    Runs only after the creating transaction has committed, so the contract the
    freelancer is being told about is complete and readable. Non-fatal by design: a
    contract that is live in the database should not be reported as failed because a
    notification could not be delivered.
    """
    contract_id = str(contract["contract_id"])
    try:
        cl_row = ClientFunctions.get_client_by_id(str(contract["client_id"]))
        fl_row = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
        if not (cl_row and fl_row):
            return
        client_user_id = str(cl_row["user_id"])
        freelancer_user_id = str(fl_row["user_id"])
        default_msg = _contract_accepted_default(
            role_title=contract.get("role_title", ""),
            contract_title=contract.get("contract_title", ""),
        )
        DMFunctions.activate_or_create_thread(
            client_user_id=client_user_id,
            freelancer_user_id=freelancer_user_id,
            message_text=default_msg,
            sender_id=client_user_id,
            job_post_id=str(contract["job_post_id"]) if contract.get("job_post_id") else None,
            job_role_id=str(contract["job_role_id"]) if contract.get("job_role_id") else None,
            contract_id=contract_id,
            role_title=contract.get("role_title"),
            contract_title=contract.get("contract_title"),
        )
        logger("CONTRACT", f"DM thread activated for contract {contract_id}", "POST /contracts/{contract_id}/generate", "INFO")

        await NotificationFunctions.notify(
            recipient_user_id=freelancer_user_id,
            notif_type="contract_started",
            title="Contract started",
            body=f"A new contract \"{contract.get('contract_title')}\" has begun",
            data={"contract_id": contract_id},
        )
    except Exception as dm_err:
        logger("CONTRACT", f"DM/notification on activation failed (non-fatal): {dm_err}", "POST /contracts/{contract_id}/generate", "WARNING")


# What the freelancer bid on is not the client's to rewrite afterwards. The role's
# title and currency come from the job role, the money and the duration from the
# proposal; the contract only records them. Everything else on the setup screen
# (contract_title, start_date, the legal terms) stays editable.
def _locked_field_error(field: str, submitted, expected) -> Dict:
    return {
        "message": (
            f"{field} is fixed by the accepted proposal and cannot be changed "
            f"(expected {expected!r}, got {submitted!r})."
        ),
        "field": field,
        "expected": expected,
        "submitted": submitted,
    }


def _budgets_differ(submitted: Optional[float], expected) -> bool:
    """Compare against a numeric(12,2) column, so only two decimals are significant."""
    if submitted is None or expected is None:
        return False
    return Decimal(str(submitted)).quantize(_CENTS) != Decimal(str(expected)).quantize(_CENTS)


def _check_terms_against_proposal(
    proposal: Dict,
    job_role: Optional[Dict],
    role_title: Optional[str] = None,
    agreed_budget: Optional[float] = None,
    budget_currency: Optional[str] = None,
    agreed_duration: Optional[str] = None,
) -> Optional[Dict]:
    """Reject any submitted value that contradicts the proposal or the job role.

    A None argument means "not submitted" and is left to be filled in server-side.
    agreed_duration is only checked when the proposal actually carried one - a
    freelancer may bid without proposing a duration, and the client sets it at
    creation time in that case.

    Only creation calls this. Afterwards these fields are frozen outright, since they
    are printed on a PDF that is never re-rendered - see _PDF_FROZEN_FIELDS.
    """
    if role_title is not None and job_role and role_title != job_role.get("role_title"):
        return _locked_field_error("role_title", role_title, job_role.get("role_title"))

    if budget_currency is not None and job_role and budget_currency != job_role.get("budget_currency"):
        return _locked_field_error("budget_currency", budget_currency, job_role.get("budget_currency"))

    if _budgets_differ(agreed_budget, proposal.get("proposed_budget")):
        return _locked_field_error("agreed_budget", agreed_budget, float(proposal["proposed_budget"]))

    proposed_duration = proposal.get("proposed_duration")
    if agreed_duration is not None and proposed_duration and agreed_duration != proposed_duration:
        return _locked_field_error("agreed_duration", agreed_duration, proposed_duration)

    return None

contract_router = APIRouter(prefix="/contracts", tags=["Contracts"])


# GET /contracts
@contract_router.get("", response_model=None)
async def get_all_contracts(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts visible to the current user."""
    try:
        if not current_user.client_id and not current_user.freelancer_id:
            return ResponseSchema.error("Only clients and freelancers can access contracts", 403)
        contracts = []
        if current_user.client_id:
            client = get_client_profile_for_user(current_user)
            contracts += ContractFunctions.get_contracts_by_client_id(client["client_id"])
        if current_user.freelancer_id:
            freelancer = get_freelancer_profile_for_user(current_user)
            contracts += ContractFunctions.get_contracts_by_freelancer_id(freelancer["freelancer_id"])
        logger("CONTRACT", f"Retrieved {len(contracts)} contracts for user {current_user.user_id}", "GET /contracts", "INFO")
        return ResponseSchema.success(contracts, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contracts: {str(e)}", "GET /contracts", "ERROR")
        return ResponseSchema.error("Failed to fetch contracts. Please try again.", 500)


# Specific sub-paths BEFORE /{contract_id} so they are not shadowed
@contract_router.get("/freelancer/{freelancer_id}", response_model=None)
async def get_contracts_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts for a given freelancer."""
    try:
        assert_freelancer_owns(current_user, freelancer_id)
        contracts = ContractFunctions.get_contracts_by_freelancer_id(freelancer_id)
        logger("CONTRACT", f"Retrieved {len(contracts)} contracts for freelancer {freelancer_id}", "GET /contracts/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(contracts, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/freelancer/{freelancer_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contracts for freelancer {freelancer_id}: {str(e)}", "GET /contracts/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch contracts for freelancer. Please try again.", 500)


@contract_router.get("/client/{client_id}", response_model=None)
async def get_contracts_by_client(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts for a given client."""
    try:
        assert_client_owns(current_user, client_id)
        contracts = ContractFunctions.get_contracts_by_client_id(client_id)
        logger("CONTRACT", f"Retrieved {len(contracts)} contracts for client {client_id}", "GET /contracts/client/{client_id}", "INFO")
        return ResponseSchema.success(contracts, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/client/{client_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contracts for client {client_id}: {str(e)}", "GET /contracts/client/{client_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch contracts for client. Please try again.", 500)


@contract_router.get("/proposal/{proposal_id}", response_model=None)
async def get_contract_by_proposal(proposal_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return the contract created from a proposal, or 404 if it has none yet.

    Answers "does this accepted bid already have a contract?" directly, so the client
    app does not have to pull its whole contract list and filter. Returns drafts too:
    the client needs to find its own unfinished setup and resume it.
    """
    try:
        contract = ContractFunctions.get_contract_by_proposal_id(proposal_id)
        if not contract:
            return ResponseSchema.error(f"No contract exists for proposal {proposal_id}", 404)
        assert_current_user_is_contract_party(current_user, contract)
        logger("CONTRACT", f"Found contract {contract['contract_id']} for proposal {proposal_id}", "GET /contracts/proposal/{proposal_id}", "INFO")
        return ResponseSchema.success(contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/proposal/{proposal_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contract for proposal {proposal_id}: {str(e)}", "GET /contracts/proposal/{proposal_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch contract for proposal. Please try again.", 500)


@contract_router.get("/{contract_id}/generation-data")
async def get_contract_generation_data(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all auto-filled contract generation fields visible to the current party."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)

        context = ContractGenerationFunctions.build_generation_context(contract_id)
        if not context:
            return ResponseSchema.error("Couldn't prepare this contract. Please try again.", 500)

        logger("CONTRACT", f"Retrieved generation data for contract {contract_id}", "GET /contracts/{contract_id}/generation-data", "INFO")
        return ResponseSchema.success(context, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}/generation-data", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch generation data for contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}/generation-data", "ERROR")
        return ResponseSchema.error("Failed to fetch generation data for contract. Please try again.", 500)


@contract_router.get("/{contract_id}/milestones")
async def get_contract_milestones(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return this contract's milestone schedule, in order."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)

        milestones = MilestoneFunctions.get_milestones_by_contract_id(contract_id)
        logger("CONTRACT", f"Retrieved {len(milestones)} milestone(s) for contract {contract_id}", "GET /contracts/{contract_id}/milestones", "INFO")
        return ResponseSchema.success(milestones, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}/milestones", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch milestones for contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}/milestones", "ERROR")
        return ResponseSchema.error("Failed to fetch milestones. Please try again.", 500)


@contract_router.get("/{contract_id}/pdf-url")
async def get_contract_pdf_url(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return a proxy URL for a generated contract PDF."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)

        pdf_path = contract.get("contract_pdf_url")
        if not pdf_path:
            return ResponseSchema.error("Contract PDF has not been generated yet", 404)

        signed_url = ContractGenerationFunctions.get_signed_contract_url(pdf_path)
        logger("CONTRACT", f"Created signed PDF URL for contract {contract_id}", "GET /contracts/{contract_id}/pdf-url", "INFO")
        return ResponseSchema.success({"pdf_url": signed_url}, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}/pdf-url", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to create PDF URL for contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}/pdf-url", "ERROR")
        return ResponseSchema.error("Failed to create PDF URL for contract. Please try again.", 500)


# dev/admin only - not called by the Flutter app
@contract_router.get("/{contract_id}/pdf-download")
async def download_contract_pdf(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Download the generated contract PDF directly."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)

        pdf_path = contract.get("contract_pdf_url")
        if not pdf_path:
            return ResponseSchema.error("Contract PDF has not been generated yet", 404)

        pdf_bytes = download_file("contract-assets", pdf_path)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=contract_{contract_id}.pdf"},
        )
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}/pdf-download", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to download PDF for contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}/pdf-download", "ERROR")
        return ResponseSchema.error("Failed to download PDF for contract. Please try again.", 500)


# Generic /{contract_id} GET, must come AFTER all literal sub-paths
@contract_router.get("/{contract_id}", response_model=None)
async def get_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return a single contract by ID."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)
        ContractFunctions.attach_job_closure([contract])
        logger("CONTRACT", f"Retrieved contract {contract_id}", "GET /contracts/{contract_id}", "INFO")
        return ResponseSchema.success(contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error("Failed to fetch contract. Please try again.", 500)


# Mutations
@contract_router.post("", response_model=None, status_code=201)
async def create_contract(contract: ContractCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new contract."""
    try:
        contract_id = contract.contract_id or str(uuid.uuid4())
        if not current_user.client_id and not current_user.freelancer_id:
            return ResponseSchema.error("Only clients or freelancers can create contracts", 403)
        if current_user.client_id:
            client = get_client_profile_for_user(current_user)
            if contract.client_id and str(contract.client_id) != str(client["client_id"]):
                return ResponseSchema.error("Cannot create a contract for another client", 403)
        else:
            return ResponseSchema.error("Only clients can create contracts", 403)

        # A contract can only be finalized from a proposal the client already accepted.
        # Harmful proposals are blocked at submission time, so nothing more to check.
        proposal = ProposalFunctions.get_proposal_by_id(str(contract.proposal_id))
        if not proposal:
            return ResponseSchema.error(f"Proposal {contract.proposal_id} not found", 404)
        if proposal["status"] != "accepted":
            return ResponseSchema.error(
                f"Cannot create a contract from a proposal that hasn't been accepted (current status: {proposal['status']})", 400
            )
        existing_contract = ContractFunctions.get_contract_by_proposal_id(str(contract.proposal_id))
        if existing_contract:
            return ResponseSchema.error(
                f"A contract already exists for this proposal (contract_id: {existing_contract['contract_id']})", 409
            )
        if str(proposal["freelancer_id"]) != str(contract.freelancer_id):
            return ResponseSchema.error("Contract freelancer does not match the proposal's freelancer", 400)
        if str(proposal["job_post_id"]) != str(contract.job_post_id):
            return ResponseSchema.error("Contract job post does not match the proposal's job post", 400)
        if proposal.get("job_role_id") and str(proposal["job_role_id"]) != str(contract.job_role_id):
            return ResponseSchema.error("Contract job role does not match the proposal's job role", 400)

        active_count = ContractFunctions.count_live_contracts_for_freelancer(str(contract.freelancer_id))
        if active_count >= MAX_ACTIVE_CONTRACTS_PER_FREELANCER:
            return ResponseSchema.error(
                f"This freelancer already has {active_count} active contract(s) and cannot take on a new one until at least one is resolved.",
                409,
            )

        terms_in = contract.terms
        rejection = _reject_contract_short_text_if_harmful(
            contract.contract_title, contract.role_title, terms_in.governing_law
        )
        if rejection:
            return ResponseSchema.error(rejection["message"], 400, extra={"blocked_by": "harmful_text", "detected_labels": rejection["detected_labels"]})
        duration_error = _reject_contract_duration_if_invalid(terms_in.agreed_duration or contract.agreed_duration)
        if duration_error:
            return ResponseSchema.error(duration_error["message"], 400)
        terms_error = _reject_generation_terms_if_invalid(terms_in)
        if terms_error:
            return ResponseSchema.error(terms_error, 400)

        # The setup screen prefills these from the proposal and the role, so a correct
        # client sends them back unchanged. A mismatch means the form let them be edited.
        job_role = JobRoleFunctions.get_job_role_by_id(str(contract.job_role_id))
        if not job_role:
            return ResponseSchema.error(f"Job role {contract.job_role_id} not found", 404)
        locked = _check_terms_against_proposal(
            proposal,
            job_role,
            role_title=contract.role_title,
            agreed_budget=contract.agreed_budget,
            budget_currency=contract.budget_currency,
            agreed_duration=terms_in.agreed_duration or contract.agreed_duration,
        )
        if locked:
            logger("CONTRACT", f"Rejected create: {locked['field']} does not match the accepted proposal", "POST /contracts", "WARNING")
            return ResponseSchema.error(locked["message"], 400, extra={"blocked_by": "locked_field", "field": locked["field"], "expected": locked["expected"]})

        # Fall back to the authoritative source when the client omits these, rather
        # than to the schema default.
        agreed_duration = terms_in.agreed_duration or contract.agreed_duration or proposal.get("proposed_duration")
        role_title = contract.role_title or job_role.get("role_title")
        budget_currency = contract.budget_currency or job_role.get("budget_currency")

        # end_date is computed, not collected. A submitted one is accepted and
        # ignored the same way status is, and only stands in when there is no
        # duration to compute from.
        end_date = _derive_end_date(contract.start_date, agreed_duration) or terms_in.end_date

        terms = {
            "termination_notice": terms_in.termination_notice,
            "governing_law": terms_in.governing_law,
            "confidentiality": terms_in.confidentiality,
            "confidentiality_text": terms_in.confidentiality_text,
            "late_payment_penalty": terms_in.late_payment_penalty,
            "dispute_resolution": terms_in.dispute_resolution,
            "revision_rounds": terms_in.revision_rounds,
            "additional_clauses": terms_in.additional_clauses,
            "payment_schedule": terms_in.payment_schedule,
        }

        # Render and upload BEFORE writing anything. Object storage cannot join a
        # database transaction, so the document is produced first and the database is
        # the last thing to happen: a failure from here on leaves an unreferenced file
        # in the bucket and nothing else. Doing it the other way round would risk a
        # live contract with no document behind it.
        generated_at = datetime.utcnow()
        pending_contract = {
            "contract_id": contract_id,
            "job_post_id": str(contract.job_post_id),
            "job_role_id": str(contract.job_role_id),
            "proposal_id": str(contract.proposal_id),
            "freelancer_id": str(contract.freelancer_id),
            "client_id": str(contract.client_id),
            "contract_title": contract.contract_title,
            "role_title": role_title,
            "agreed_budget": contract.agreed_budget,
            "budget_currency": budget_currency,
            "agreed_duration": agreed_duration,
            "start_date": contract.start_date,
            "end_date": end_date,
        }
        milestones_in = [m.model_dump() for m in contract.milestones]
        pdf_bytes = ContractGenerationFunctions.render_contract_pdf(
            contract_id, generated_at=generated_at, contract=pending_contract, contract_terms=terms,
            milestones=milestones_in,
        )
        storage_path = ContractGenerationFunctions.upload_contract_pdf(contract_id, pdf_bytes)

        try:
            created = ContractFunctions.create_contract(
                contract_id=contract_id,
                job_post_id=contract.job_post_id,
                job_role_id=contract.job_role_id,
                proposal_id=contract.proposal_id,
                freelancer_id=contract.freelancer_id,
                client_id=contract.client_id,
                contract_title=contract.contract_title,
                agreed_budget=contract.agreed_budget,
                milestones=milestones_in,
                start_date=contract.start_date,
                terms=terms,
                role_title=role_title,
                budget_currency=budget_currency,
                agreed_duration=agreed_duration,
                end_date=end_date,
                actual_completion_date=contract.actual_completion_date,
                total_hours_worked=contract.total_hours_worked,
                total_paid=contract.total_paid,
                contract_pdf_url=storage_path,
                contract_pdf_generated_at=generated_at,
            )
        except IntegrityError:
            # Another request created a contract for this proposal in between, caught
            # by the UNIQUE(proposal_id) constraint.
            logger("CONTRACT", f"Duplicate contract insert blocked by UNIQUE(proposal_id) for proposal {contract.proposal_id}", "POST /contracts", "WARNING")
            return ResponseSchema.error("A contract already exists for this proposal", 409)

        new_contract = created["contract"]
        logger("CONTRACT", f"Created contract {contract_id}", "POST /contracts", "INFO")

        # The contract is committed and whole by this point, so telling the freelancer
        # about it cannot be premature.
        await _announce_contract_started(new_contract)

        return ResponseSchema.success(new_contract, 201)
    except ValueError as e:
        logger("CONTRACT", f"Validation error: {str(e)}", "POST /contracts", "WARNING")
        # str(e) on purpose: ValueError here is a hand-written sentence from
        # contract_functions.py, not a system exception. "Try again" would be wrong advice.
        return ResponseSchema.error(str(e), 400)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "POST /contracts", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to create contract: {str(e)}", "POST /contracts", "ERROR")
        return ResponseSchema.error("Failed to create contract. Please try again.", 500)


@contract_router.post("/{contract_id}/send", response_model=None)
async def send_contract_to_freelancer(contract_id: str, payload: ContractSendRequest, current_user: UserInDB = Depends(get_current_user)):
    """Deliver an already-generated contract to the freelancer.

    A contract's PDF is rendered once, inside the transaction that creates it, and is
    never re-rendered - so this only delivers what already exists. The stored document
    is fetched back from object storage and attached to a DM, which keeps what the
    freelancer receives identical to what was generated rather than a fresh render that
    could differ.

    Safe to call more than once: sending again posts another message with the same
    document, which is what a client re-sending a contract means.
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        # The client sends their own contract; the freelancer is the recipient.
        client_profile = get_client_profile_for_user(current_user) if current_user.client_id else None
        if not client_profile or str(client_profile["client_id"]) != str(contract["client_id"]):
            return ResponseSchema.error("Only the contract's client can send it to the freelancer", 403)

        pdf_path = contract.get("contract_pdf_url")
        if not pdf_path:
            return ResponseSchema.error("This contract has no generated PDF to send", 409)

        freelancer = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
        freelancer_user_id = str((freelancer or {}).get("user_id", ""))
        if not freelancer_user_id:
            return ResponseSchema.error("Could not resolve the freelancer for this contract", 404)

        # Resolved by participant pair, not by contract_id: this client and freelancer
        # share one thread no matter how many contracts they sign, and only the most
        # recent contract is recorded on it. Opens the thread if this is their first.
        thread = DMFunctions.get_or_open_thread_for_contract(contract)
        if not thread:
            return ResponseSchema.error("Could not open a message thread for this contract", 500)

        custom_msg = payload.notification_message
        raw_template = custom_msg or client_profile.get("contract_message_template") or _DEFAULT_CONTRACT_NOTIFICATION
        message_text = _render_notification(raw_template, {
            "freelancer_name": (freelancer or {}).get("full_name") or "there",
            "contract_title": contract.get("contract_title") or "",
            "role_title": contract.get("role_title") or "",
        })

        pdf_bytes = download_file(CONTRACT_BUCKET, pdf_path)

        msg = DMFunctions.send_message(
            thread_id=thread["thread_id"],
            sender_id=str(current_user.user_id),
            message_text=message_text,
            metadata={"type": "contract_pdf_shared", "contract_id": contract_id},
        )

        file_name = f"contract_{contract_id}.pdf"
        attachment_path = upload_thread_attachment(
            thread_id=thread["thread_id"],
            message_id=msg["dm_message_id"],
            file_name=file_name,
            file_bytes=pdf_bytes,
            content_type="application/pdf",
        )
        attachment = DMFunctions.create_attachment(
            dm_message_id=msg["dm_message_id"],
            file_name=file_name,
            file_url=attachment_path,
            mime_type="application/pdf",
            file_type="document",
            file_size_bytes=len(pdf_bytes),
        )
        attachment["file_url"] = resolve_file_url(BUCKET_MESSAGE_ATTACHMENTS, attachment["file_url"])
        msg["attachments"] = [attachment]

        # Only persist the template once the message it came from actually sent.
        if payload.save_message_as_template and custom_msg:
            get_db().execute_query(
                "UPDATE client SET contract_message_template = :tpl WHERE client_id = :cid",
                {"tpl": custom_msg, "cid": str(client_profile["client_id"])},
            )

        await NotificationFunctions.notify(
            recipient_user_id=freelancer_user_id,
            notif_type="contract_shared",
            title="Contract received",
            body=f"You have received the contract \"{contract.get('contract_title')}\"",
            data={"contract_id": contract_id},
        )

        logger("CONTRACT", f"Sent contract {contract_id} to freelancer", "POST /contracts/{contract_id}/send", "INFO")
        return ResponseSchema.success(contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "POST /contracts/{contract_id}/send", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to send contract {contract_id}: {str(e)}", "POST /contracts/{contract_id}/send", "ERROR")
        return ResponseSchema.error("Failed to send the contract. Please try again.", 500)


@contract_router.put("/{contract_id}", response_model=None)
async def update_contract(contract_id: str, contract_update: ContractUpdate, background_tasks: BackgroundTasks, current_user: UserInDB = Depends(get_current_user)):
    """Update an existing contract."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        update_data = contract_update.model_dump(exclude_unset=True)
        if existing_contract.get("status") in _ARBITRATION_LOCKED_STATUSES:
            return ResponseSchema.error(
                "This contract is under dispute and cannot be edited until an admin resolves it",
                409,
            )

        new_status = update_data.get("status")
        if new_status and new_status != existing_contract.get("status"):
            return ResponseSchema.error(
                "Contract status can only change through the dedicated submission, "
                "approve, revision, or cancel endpoints - not through this generic update",
                400,
            )

        if "contract_title" in update_data or "role_title" in update_data:
            _contract_title = update_data.get("contract_title", existing_contract.get("contract_title", ""))
            _role_title = update_data.get("role_title", existing_contract.get("role_title", ""))
            rejection = _reject_contract_short_text_if_harmful(_contract_title, _role_title)
            if rejection:
                return ResponseSchema.error(rejection["message"], 400, extra={"blocked_by": "harmful_text", "detected_labels": rejection["detected_labels"]})

        if "agreed_duration" in update_data:
            duration_error = _reject_contract_duration_if_invalid(update_data.get("agreed_duration"))
            if duration_error:
                return ResponseSchema.error(duration_error["message"], 400)

        # Everything the PDF prints is fixed once the contract exists, because the PDF
        # is rendered once at creation and never re-rendered. Allowing these to change
        # would leave the stored document contradicting the row it describes, with no
        # way to bring them back into agreement.
        #
        # end_date is the deliberate exception: an arbitration extension moves it, and
        # the document keeps showing the deadline that was actually agreed - which is
        # what original_end_date preserves and what on-time delivery is scored against.
        frozen = _PDF_FROZEN_FIELDS.intersection(update_data.keys())
        if frozen:
            changed = {f for f in frozen if update_data[f] != existing_contract.get(f)}
            if changed:
                field = sorted(changed)[0]
                logger("CONTRACT", f"Rejected update: {field} is printed on the contract PDF and cannot change", "PUT /contracts/{contract_id}", "WARNING")
                return ResponseSchema.error(
                    f"{field} appears on the signed contract PDF and cannot be changed after the contract is created.",
                    400,
                    extra={"blocked_by": "frozen_field", "field": field, "expected": existing_contract.get(field)},
                )

        updated_contract = ContractFunctions.update_contract(contract_id, update_data)

        if update_data.get("status") == "completed" and existing_contract.get("status") != "completed":
            mark_contract_dirty(contract_id)
            db = get_db()
            db.execute_query(
                "UPDATE freelancer SET total_jobs = total_jobs + 1 WHERE freelancer_id = :fid",
                {"fid": existing_contract["freelancer_id"]},
            )
            db.execute_query(
                "UPDATE client SET total_jobs_completed = total_jobs_completed + 1 WHERE client_id = :cid",
                {"cid": existing_contract["client_id"]},
            )
            await trigger_review_pipeline_on_completion(contract_id, background_tasks)
            await trigger_client_review_pipeline_on_completion(contract_id, background_tasks)

        # Status-change notifications
        new_status = update_data.get("status")
        old_status = existing_contract.get("status")

        if new_status and new_status != old_status:
            try:
                fl = FreelancerFunctions.get_freelancer_by_id(str(existing_contract["freelancer_id"]))
                cl = ClientFunctions.get_client_by_id(str(existing_contract["client_id"]))
                title_str = existing_contract.get("contract_title", "your contract")

                notif_map = {
                    "under_review": (
                        str(cl["user_id"]),
                        "Work submitted",
                        f"{fl.get('full_name')} submitted work for review",
                        "work_submitted",
                    ),
                    "revision_requested": (
                        str(fl["user_id"]),
                        "Revision requested",
                        f"{cl.get('full_name')} requested a revision",
                        "revision_requested",
                    ),
                    "completed": (
                        str(fl["user_id"]),
                        "Contract completed",
                        f"\"{title_str}\" has been marked as completed",
                        "contract_completed",
                    ),
                }

                if new_status in notif_map:
                    recipient, title, body, ntype = notif_map[new_status]
                    await NotificationFunctions.notify(
                        recipient_user_id=recipient,
                        notif_type=ntype,
                        title=title,
                        body=body,
                        data={"contract_id": contract_id},
                    )
            except Exception as notif_err:
                logger("CONTRACT", f"Status notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}", "WARNING")

        logger("CONTRACT", f"Updated contract {contract_id}", "PUT /contracts/{contract_id}", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to update contract {contract_id}: {str(e)}", "PUT /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error("Failed to update contract. Please try again.", 500)


# Dispute endpoint (either party can raise; admin resolves via /admin/contracts/{id}/arbitrate)


@contract_router.put("/{contract_id}/dispute")
async def raise_dispute(
    contract_id: str,
    payload: RaiseDisputeRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Either party raises a dispute while work is under review or being revised. It is also
    the recourse against a cancellation, which the other party can dispute within
    _CANCELLATION_DISPUTE_WINDOW.

    Moves the contract to 'disputed', which only an admin can resolve from there.
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        assert_current_user_is_contract_party(current_user, contract)

        disputable_statuses = {"under_review", "revision_requested", "pending_payment", "payment_review", "payment_rejected"}
        if contract["status"] == "cancelled":
            if str(contract.get("cancelled_by")) == str(current_user.user_id):
                return ResponseSchema.error("You cannot dispute your own cancellation.", 403)

            # An admin who cancels as the outcome of an arbitration is not a party, so
            # neither party matches the check above and both could re-dispute the ruling,
            # bouncing the contract between 'cancelled' and 'disputed' indefinitely.
            if contract.get("cancelled_by") and not _cancellation_was_by_a_party(contract):
                return ResponseSchema.error(
                    "This contract was cancelled by an admin resolving a dispute. That decision is final.",
                    403,
                )

            if not _cancellation_dispute_window_open(contract_id, contract):
                return ResponseSchema.error(
                    "The window to dispute this cancellation has passed.", 400,
                )
        elif contract["status"] not in disputable_statuses:
            return ResponseSchema.error(
                f"Cannot raise a dispute on a contract with status '{contract['status']}'", 400,
            )

        # After the status checks: the reason scan is an ML call, no point spending it on
        # a request that was never going to be accepted. The reason is the whole record of
        # why this dispute exists - there is no dispute_reason column, only the system
        # event - so an empty one is refused.
        if not payload.reason or not payload.reason.strip():
            return ResponseSchema.error("A reason is required to raise a dispute.", 400)

        rejection = _reject_reason_if_invalid(payload.reason, "dispute")
        if rejection:
            return ResponseSchema.error(rejection["message"], 400, extra=_harmful_extra(rejection))

        updated_contract = ContractFunctions.raise_dispute(
            contract_id=contract_id,
            raised_by=str(current_user.user_id),
            reason=payload.reason,
        )

        try:
            fl = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            cl = ClientFunctions.get_client_by_id(str(contract["client_id"]))
            is_client_raising = current_user.client_id and str(current_user.user_id) == str(cl["user_id"])
            other_party = fl if is_client_raising else cl

            await NotificationFunctions.notify(
                recipient_user_id=str(other_party["user_id"]),
                notif_type="contract_disputed",
                title="Contract under dispute",
                body=f"A dispute was raised on \"{contract.get('contract_title')}\". An admin will review it.",
                data={"contract_id": contract_id},
            )
        except Exception as notif_err:
            logger("CONTRACT", f"Dispute notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}/dispute", "WARNING")

        logger("CONTRACT", f"Contract {contract_id} disputed by {current_user.user_id}", "PUT /contracts/{contract_id}/dispute", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except ValueError as e:
        # Lost the race against a concurrent status change - the message is written for
        # the user, so it goes through as-is rather than as a generic failure.
        logger("CONTRACT", f"Dispute rejected: {e}", "PUT /contracts/{contract_id}/dispute", "WARNING")
        return ResponseSchema.error(str(e), 409)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}/dispute", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to raise dispute for {contract_id}: {str(e)}", "PUT /contracts/{contract_id}/dispute", "ERROR")
        return ResponseSchema.error("Failed to raise dispute. Please try again.", 500)


# Cancel endpoint


@contract_router.put("/{contract_id}/cancel")
async def cancel_contract(
    contract_id: str,
    payload: CancelContractRequest = Body(default=CancelContractRequest()),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Cancel an active contract.
    Only the client or freelancer who is a party to the contract can cancel it.
    Only contracts with status 'active', 'under_review', 'revision_requested',
    'pending_payment', 'payment_review', or 'payment_rejected' can be cancelled.
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        assert_current_user_is_contract_party(current_user, contract)

        cancellable_statuses = {
            "active", "under_review", "revision_requested",
            "pending_payment", "payment_review", "payment_rejected",
        }
        if contract["status"] not in cancellable_statuses:
            return ResponseSchema.error(
                f"Cannot cancel a contract with status '{contract['status']}'",
                400,
            )

        # No reason needed while nothing has been delivered. Once work exists a reason
        # is mandatory, since it's what a later dispute responds to.
        if contract["status"] != "active" and not (payload.reason and payload.reason.strip()):
            return ResponseSchema.error(
                "A reason is required to cancel a contract once work is in progress", 400,
            )

        rejection = _reject_reason_if_invalid(payload.reason, "cancellation")
        if rejection:
            return ResponseSchema.error(rejection["message"], 400, extra=_harmful_extra(rejection))

        cancelled_contract = ContractFunctions.cancel_contract(
            contract_id=contract_id,
            cancelled_by=str(current_user.user_id),
            reason=payload.reason,
        )

        # Notify the other party
        try:
            fl = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            cl = ClientFunctions.get_client_by_id(str(contract["client_id"]))
            is_client_cancelling = str(current_user.user_id) == str(cl["user_id"])
            other_party = fl if is_client_cancelling else cl

            await NotificationFunctions.notify(
                recipient_user_id=str(other_party["user_id"]),
                notif_type="contract_cancelled",
                title="Contract cancelled",
                body=f"The contract \"{contract.get('contract_title')}\" was cancelled",
                data={"contract_id": contract_id},
            )
        except Exception as notif_err:
            logger("CONTRACT", f"Cancel notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}/cancel", "WARNING")

        logger("CONTRACT", f"Contract {contract_id} cancelled by user {current_user.user_id}", "PUT /contracts/{contract_id}/cancel", "INFO")
        return ResponseSchema.success(cancelled_contract, 200)

    except ValueError as e:
        # Lost the race against a concurrent status change - the message is written for
        # the user, so it goes through as-is rather than as a generic failure.
        logger("CONTRACT", f"Cancel rejected: {e}", "PUT /contracts/{contract_id}/cancel", "WARNING")
        return ResponseSchema.error(str(e), 409)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}/cancel", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to cancel contract {contract_id}: {str(e)}", "PUT /contracts/{contract_id}/cancel", "ERROR")
        return ResponseSchema.error("Failed to cancel contract. Please try again.", 500)


# DELETE


@contract_router.delete("/{contract_id}", status_code=200)
async def delete_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a contract by ID."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        # Deleting a disputed contract would destroy the record an admin is arbitrating,
        # so a party can't use it as an exit from a ruling that is going against them.
        if existing_contract.get("status") in _ARBITRATION_LOCKED_STATUSES:
            return ResponseSchema.error(
                "This contract is under dispute and cannot be deleted until an admin resolves it",
                409,
            )

        # Same reasoning for a fresh cancellation: deleting it inside the dispute window
        # takes away the other party's only recourse against the cancellation.
        if (existing_contract.get("status") == "cancelled"
                and _cancellation_dispute_window_open(contract_id, existing_contract)):
            return ResponseSchema.error(
                "This contract was cancelled recently and cannot be deleted while the other "
                "party can still dispute the cancellation",
                409,
            )

        ContractFunctions.delete_contract(contract_id)

        logger("CONTRACT", f"Deleted contract {contract_id}", "DELETE /contracts/{contract_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "DELETE /contracts/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to delete contract {contract_id}: {str(e)}", "DELETE /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error("Failed to delete contract. Please try again.", 500)