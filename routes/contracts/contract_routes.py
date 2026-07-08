import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from fastapi import APIRouter, Body, Depends, Response, BackgroundTasks, HTTPException
from functions.minio_client import (
    download_file,
    upload_thread_attachment,
    resolve_file_url,
    BUCKET_MESSAGE_ATTACHMENTS,
)
from routes.reviews.review_routes import trigger_review_pipeline_on_completion
from typing import List, Optional
import uuid
from functions.schema_model import CancelContractRequest, ContractCreate, ContractUpdate, ContractResponse, ContractGenerateRequest, ReportPaymentRequest, RaiseDisputeRequest
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
from routes.contracts.contract_functions import ContractFunctions
from routes.contracts.contract_generation_functions import ContractGenerationFunctions
from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.proposals.proposal_functions import ProposalFunctions
from routes.dm.dm_functions import DMFunctions, _contract_accepted_default
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.job_engine.embedding_manager import mark_contract_dirty


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

    # Remove leftover pdf_url placeholder if an old saved template still has it
    template = template.replace("{pdf_url}", "").strip()

    return template

# Fields that feed contract_generation_functions.py's PDF render (see
# build_generation_context / render_contract_pdf) - editing any of these after a PDF has
# already been generated makes contract_pdf_url stale, so update_contract() below clears
# it to force a regenerate before the next download.
_PDF_RELEVANT_FIELDS = {
    "contract_title", "role_title", "agreed_budget", "budget_currency",
    "payment_structure", "agreed_duration", "start_date", "end_date",
}

contract_router = APIRouter(prefix="/contracts", tags=["Contracts"])


# GET /contracts


@contract_router.get("", response_model=List[ContractResponse])
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
        return ResponseSchema.error(f"Failed to fetch contracts: {str(e)}", 500)


# Specific sub-paths BEFORE /{contract_id} so they are not shadowed


@contract_router.get("/freelancer/{freelancer_id}", response_model=List[ContractResponse])
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
        return ResponseSchema.error(f"Failed to fetch contracts for freelancer {freelancer_id}: {str(e)}", 500)


@contract_router.get("/client/{client_id}", response_model=List[ContractResponse])
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
        return ResponseSchema.error(f"Failed to fetch contracts for client {client_id}: {str(e)}", 500)


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
            return ResponseSchema.error(f"Failed to build generation context for contract {contract_id}", 500)

        logger("CONTRACT", f"Retrieved generation data for contract {contract_id}", "GET /contracts/{contract_id}/generation-data", "INFO")
        return ResponseSchema.success(context, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}/generation-data", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch generation data for contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}/generation-data", "ERROR")
        return ResponseSchema.error(f"Failed to fetch generation data for contract {contract_id}: {str(e)}", 500)


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
        return ResponseSchema.error(f"Failed to create PDF URL for contract {contract_id}: {str(e)}", 500)


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
        return ResponseSchema.error(f"Failed to download PDF for contract {contract_id}: {str(e)}", 500)


# Generic /{contract_id} GET, must come AFTER all literal sub-paths


@contract_router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return a single contract by ID."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)
        logger("CONTRACT", f"Retrieved contract {contract_id}", "GET /contracts/{contract_id}", "INFO")
        return ResponseSchema.success(contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "GET /contracts/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to fetch contract {contract_id}: {str(e)}", "GET /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch contract {contract_id}: {str(e)}", 500)


# Mutations


@contract_router.post("", response_model=ContractResponse, status_code=201)
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

        # A contract may only be finalized from a proposal the client has already
        # accepted (via PATCH /proposals/{id}/status) and that passed moderation -
        # without this, a contract could be created from a still-pending, rejected,
        # or blocked proposal since this endpoint never used to look at it at all.
        proposal = ProposalFunctions.get_proposal_by_id(str(contract.proposal_id))
        if not proposal:
            return ResponseSchema.error(f"Proposal {contract.proposal_id} not found", 404)
        if proposal["status"] != "accepted":
            return ResponseSchema.error(
                f"Cannot create a contract from a proposal that hasn't been accepted (current status: {proposal['status']})", 400
            )
        if proposal["moderation_status"] != "visible":
            return ResponseSchema.error("Cannot create a contract from a proposal that hasn't passed moderation", 400)
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

        new_contract = ContractFunctions.create_contract(
            contract_id=contract_id,
            job_post_id=contract.job_post_id,
            job_role_id=contract.job_role_id,
            proposal_id=contract.proposal_id,
            freelancer_id=contract.freelancer_id,
            client_id=contract.client_id,
            contract_title=contract.contract_title,
            agreed_budget=contract.agreed_budget,
            payment_structure=contract.payment_structure,
            start_date=contract.start_date,
            role_title=contract.role_title,
            budget_currency=contract.budget_currency,
            agreed_duration=contract.agreed_duration,
            status=contract.status,
            end_date=contract.end_date,
            actual_completion_date=contract.actual_completion_date,
            total_hours_worked=contract.total_hours_worked,
            total_paid=contract.total_paid,
        )

        logger("CONTRACT", f"Created contract {contract_id}", "POST /contracts", "INFO")

        # Auto-activate DM thread + notify freelancer
        try:
            cl_row = ClientFunctions.get_client_by_id(str(new_contract["client_id"]))
            fl_row = FreelancerFunctions.get_freelancer_by_id(str(new_contract["freelancer_id"]))
            if cl_row and fl_row:
                client_user_id = str(cl_row["user_id"])
                freelancer_user_id = str(fl_row["user_id"])
                default_msg = _contract_accepted_default(
                    role_title=new_contract.get("role_title", ""),
                    contract_title=new_contract.get("contract_title", ""),
                )
                DMFunctions.activate_or_create_thread(
                    client_user_id=client_user_id,
                    freelancer_user_id=freelancer_user_id,
                    message_text=default_msg,
                    sender_id=client_user_id,
                    job_post_id=str(new_contract["job_post_id"]) if new_contract.get("job_post_id") else None,
                    job_role_id=str(new_contract["job_role_id"]) if new_contract.get("job_role_id") else None,
                    contract_id=contract_id,
                    role_title=new_contract.get("role_title"),
                    contract_title=new_contract.get("contract_title"),
                )
                logger("CONTRACT", f"DM thread activated for contract {contract_id}", "POST /contracts", "INFO")

                # Notify freelancer
                await NotificationFunctions.notify(
                    recipient_user_id=freelancer_user_id,
                    notif_type="contract_started",
                    title="Contract Started 🚀",
                    body=f"A new contract \"{new_contract.get('contract_title')}\" has begun",
                    data={"contract_id": contract_id},
                )
        except Exception as dm_err:
            logger("CONTRACT", f"DM/notification post-create failed (non-fatal): {dm_err}", "POST /contracts", "WARNING")

        return ResponseSchema.success(new_contract, 201)
    except ValueError as e:
        logger("CONTRACT", f"Validation error: {str(e)}", "POST /contracts", "WARNING")
        return ResponseSchema.error(f"Validation error: {str(e)}", 400)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "POST /contracts", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to create contract: {str(e)}", "POST /contracts", "ERROR")
        return ResponseSchema.error(f"Failed to create contract: {str(e)}", 500)


@contract_router.post("/{contract_id}/generate", response_model=ContractResponse)
async def generate_contract_pdf(contract_id: str, generation_data: ContractGenerateRequest, current_user: UserInDB = Depends(get_current_user)):
    """Generate a contract PDF and persist the contract terms and storage path."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, contract)

        if generation_data.termination_notice not in {7, 14, 30}:
            return ResponseSchema.error("termination_notice must be 7, 14, or 30 days", 400)
        if generation_data.dispute_resolution not in {"negotiation", "mediation", "arbitration"}:
            return ResponseSchema.error("Invalid dispute_resolution value", 400)

        ContractGenerationFunctions.save_generation_data(
            contract_id=contract_id,
            update_data={
                "end_date": generation_data.end_date,
                "agreed_duration": generation_data.agreed_duration,
            },
            terms={
                "termination_notice": generation_data.termination_notice,
                "governing_law": generation_data.governing_law,
                "confidentiality": generation_data.confidentiality,
                "confidentiality_text": generation_data.confidentiality_text,
                "late_payment_penalty": generation_data.late_payment_penalty,
                "dispute_resolution": generation_data.dispute_resolution,
                "revision_rounds": generation_data.revision_rounds,
                "additional_clauses": generation_data.additional_clauses,
                "payment_schedule": generation_data.payment_schedule,
            },
        )

        pdf_bytes = ContractGenerationFunctions.render_contract_pdf(contract_id)
        storage_path = ContractGenerationFunctions.upload_contract_pdf(contract_id, pdf_bytes)

        db = get_db()
        db.execute_query(
            """UPDATE contract
               SET contract_pdf_url = :url,
                   contract_pdf_generated_at = NOW()
               WHERE contract_id = :cid""",
            {"url": storage_path, "cid": contract_id},
        )

        refreshed = ContractFunctions.get_contract_by_id(contract_id)

        client_profile = None
        if current_user.client_id:
            cp = ClientFunctions.get_client_by_user_id(current_user.user_id)
            if cp and str(cp["client_id"]) == str(refreshed["client_id"]):
                client_profile = cp

        if client_profile and generation_data.send_notification:
            try:
                freelancer = FreelancerFunctions.get_freelancer_by_id(str(refreshed["freelancer_id"]))
                freelancer_name = (freelancer or {}).get("full_name") or "there"
                freelancer_user_id = str((freelancer or {}).get("user_id", ""))
                
                custom_msg = generation_data.notification_message
                saved_template = client_profile.get("contract_message_template")
                raw_template = custom_msg or saved_template or _DEFAULT_CONTRACT_NOTIFICATION

                message_text = _render_notification(raw_template, {
                    "freelancer_name": freelancer_name,
                    "contract_title": refreshed.get("contract_title") or "",
                    "role_title": refreshed.get("role_title") or "",
                })

                if generation_data.save_message_as_template and custom_msg:
                    db.execute_query(
                        "UPDATE client SET contract_message_template = :tpl WHERE client_id = :cid",
                        {"tpl": custom_msg, "cid": str(client_profile["client_id"])},
                    )

                if freelancer_user_id:
                    thread = DMFunctions.get_thread_by_contract_id(contract_id)
                    if thread:
                        msg = DMFunctions.send_message(
                            thread_id=thread["thread_id"],
                            sender_id=str(current_user.user_id),
                            message_text=message_text,
                            metadata={
                                "type": "contract_pdf_shared",
                                "contract_id": contract_id,
                            },
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

                        attachment["file_url"] = resolve_file_url(
                            BUCKET_MESSAGE_ATTACHMENTS,
                            attachment["file_url"],
                        )

                        msg["attachments"] = [attachment]
            except Exception as msg_err:
                logger("CONTRACT", f"Failed to send PDF notification: {str(msg_err)}", "POST /contracts/{contract_id}/generate", "WARNING")

        logger("CONTRACT", f"Generated contract PDF for {contract_id}", "POST /contracts/{contract_id}/generate", "INFO")
        return ResponseSchema.success(refreshed, 200)
    except ValueError as e:
        logger("CONTRACT", f"Validation error: {str(e)}", "POST /contracts/{contract_id}/generate", "WARNING")
        return ResponseSchema.error(f"Validation error: {str(e)}", 400)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "POST /contracts/{contract_id}/generate", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to generate contract PDF for {contract_id}: {str(e)}", "POST /contracts/{contract_id}/generate", "ERROR")
        return ResponseSchema.error(f"Failed to generate contract PDF for {contract_id}: {str(e)}", 500)


@contract_router.put("/{contract_id}", response_model=ContractResponse)
async def update_contract(contract_id: str, contract_update: ContractUpdate, background_tasks: BackgroundTasks, current_user: UserInDB = Depends(get_current_user)):
    """Update an existing contract."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        update_data = contract_update.model_dump(exclude_unset=True)

        # This generic endpoint must never be the place a status transition actually
        # happens - both parties pass assert_current_user_is_contract_party above, so
        # without this guard either side could force-complete or reopen any contract.
        # The frontend does redundantly PUT the status it just set via the dedicated
        # submission/approve endpoints (same value, harmless no-op) - only reject an
        # attempt to change it to something DIFFERENT from the current value here.
        new_status = update_data.get("status")
        if new_status and new_status != existing_contract.get("status"):
            return ResponseSchema.error(
                "Contract status can only change through the dedicated submission, "
                "approve, revision, or cancel endpoints - not through this generic update",
                400,
            )

        updated_contract = ContractFunctions.update_contract(contract_id, update_data)

        if existing_contract.get("contract_pdf_url") and _PDF_RELEVANT_FIELDS.intersection(update_data.keys()):
            get_db().execute_query(
                """UPDATE contract
                   SET contract_pdf_url = NULL, contract_pdf_generated_at = NULL
                   WHERE contract_id = :cid""",
                {"cid": contract_id},
            )
            updated_contract["contract_pdf_url"] = None
            updated_contract["contract_pdf_generated_at"] = None
            logger("CONTRACT", f"Contract {contract_id} PDF invalidated after edit to {sorted(_PDF_RELEVANT_FIELDS.intersection(update_data.keys()))}", "PUT /contracts/{contract_id}", "INFO")

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
                        "Work Submitted 📦",
                        f"{fl.get('full_name')} submitted work for review",
                        "work_submitted",
                    ),
                    "revision_requested": (
                        str(fl["user_id"]),
                        "Revision Requested",
                        f"{cl.get('full_name')} requested a revision",
                        "revision_requested",
                    ),
                    "completed": (
                        str(fl["user_id"]),
                        "Contract Completed ✅",
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
        return ResponseSchema.error(f"Failed to update contract {contract_id}: {str(e)}", 500)


# Payment self-report (payment itself stays off-platform - see business decision;
# this just gives total_paid a real write path and a DM trail either party can point to)


@contract_router.put("/{contract_id}/report-payment")
async def report_payment(
    contract_id: str,
    payload: ReportPaymentRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Client self-reports a payment made outside the platform. Cumulative on
    top of whatever total_paid already holds."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        assert_current_user_is_contract_party(current_user, contract)

        if not current_user.client_id or str(contract["client_id"]) != str(
            get_client_profile_for_user(current_user)["client_id"]
        ):
            return ResponseSchema.error("Only the client on this contract can report a payment", 403)

        if payload.amount <= 0:
            return ResponseSchema.error("amount must be greater than 0", 400)

        updated_contract = ContractFunctions.report_payment(
            contract_id=contract_id,
            amount=payload.amount,
            reported_by=str(current_user.user_id),
            note=payload.note,
        )

        try:
            freelancer = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            if freelancer:
                await NotificationFunctions.notify(
                    recipient_user_id=str(freelancer["user_id"]),
                    notif_type="payment_reported",
                    title="Payment Reported",
                    body=f"The client reported a payment of {payload.amount:,.2f} on \"{contract.get('contract_title')}\"",
                    data={"contract_id": contract_id, "amount": payload.amount},
                )
        except Exception as notif_err:
            logger("CONTRACT", f"Payment-report notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}/report-payment", "WARNING")

        logger("CONTRACT", f"Contract {contract_id} payment reported by {current_user.user_id}", "PUT /contracts/{contract_id}/report-payment", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}/report-payment", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to report payment for {contract_id}: {str(e)}", "PUT /contracts/{contract_id}/report-payment", "ERROR")
        return ResponseSchema.error(f"Failed to report payment: {str(e)}", 500)


# Dispute endpoint (either party can raise; admin resolves via /admin/contracts/{id}/arbitrate)


@contract_router.put("/{contract_id}/dispute")
async def raise_dispute(
    contract_id: str,
    payload: RaiseDisputeRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Either party raises a dispute while work is under review or being revised.
    Moves the contract to 'disputed' - only an admin can resolve it from there
    (PUT /admin/contracts/{contract_id}/arbitrate).
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        assert_current_user_is_contract_party(current_user, contract)

        disputable_statuses = {"under_review", "revision_requested"}
        if contract["status"] not in disputable_statuses:
            return ResponseSchema.error(
                f"Cannot raise a dispute on a contract with status '{contract['status']}'", 400,
            )

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
                title="Contract Under Dispute",
                body=f"A dispute was raised on \"{contract.get('contract_title')}\". An admin will review it.",
                data={"contract_id": contract_id},
            )
        except Exception as notif_err:
            logger("CONTRACT", f"Dispute notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}/dispute", "WARNING")

        logger("CONTRACT", f"Contract {contract_id} disputed by {current_user.user_id}", "PUT /contracts/{contract_id}/dispute", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}/dispute", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to raise dispute for {contract_id}: {str(e)}", "PUT /contracts/{contract_id}/dispute", "ERROR")
        return ResponseSchema.error(f"Failed to raise dispute: {str(e)}", 500)


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
    Only contracts with status 'active', 'under_review', or 'revision_requested' can be cancelled.
    """
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        assert_current_user_is_contract_party(current_user, contract)

        cancellable_statuses = {"active", "under_review", "revision_requested"}
        if contract["status"] not in cancellable_statuses:
            return ResponseSchema.error(
                f"Cannot cancel a contract with status '{contract['status']}'",
                400,
            )

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
                title="Contract Cancelled",
                body=f"The contract \"{contract.get('contract_title')}\" was cancelled",
                data={"contract_id": contract_id},
            )
        except Exception as notif_err:
            logger("CONTRACT", f"Cancel notification failed (non-fatal): {notif_err}", "PUT /contracts/{contract_id}/cancel", "WARNING")

        logger("CONTRACT", f"Contract {contract_id} cancelled by user {current_user.user_id}", "PUT /contracts/{contract_id}/cancel", "INFO")
        return ResponseSchema.success(cancelled_contract, 200)

    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "PUT /contracts/{contract_id}/cancel", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to cancel contract {contract_id}: {str(e)}", "PUT /contracts/{contract_id}/cancel", "ERROR")
        return ResponseSchema.error(f"Failed to cancel contract {contract_id}: {str(e)}", 500)


# DELETE


@contract_router.delete("/{contract_id}", status_code=200)
async def delete_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a contract by ID."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        ContractFunctions.delete_contract(contract_id)

        logger("CONTRACT", f"Deleted contract {contract_id}", "DELETE /contracts/{contract_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except HTTPException as e:
        logger("CONTRACT", f"HTTP {e.status_code}: {e.detail}", "DELETE /contracts/{contract_id}", "WARNING")
        return ResponseSchema.error(e.detail, e.status_code)
    except Exception as e:
        logger("CONTRACT", f"Failed to delete contract {contract_id}: {str(e)}", "DELETE /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error(f"Failed to delete contract {contract_id}: {str(e)}", 500)