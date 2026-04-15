import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional
import uuid
from functions.schema_model import ContractCreate, ContractUpdate, ContractResponse, ContractGenerateRequest
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

contract_router = APIRouter(prefix="/contracts", tags=["Contracts"])


# ── GET /contracts ────────────────────────────────────────────────────────────

@contract_router.get("", response_model=List[ContractResponse])
async def get_all_contracts(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts visible to the current user."""
    try:
        if current_user.type == "client":
            client = get_client_profile_for_user(current_user)
            contracts = ContractFunctions.get_contracts_by_client_id(client["client_id"])
        elif current_user.type == "freelancer":
            freelancer = get_freelancer_profile_for_user(current_user)
            contracts = ContractFunctions.get_contracts_by_freelancer_id(freelancer["freelancer_id"])
        else:
            return ResponseSchema.error("Only clients and freelancers can access contracts", 403)
        success_msg = f"Retrieved {len(contracts)} contracts for user {current_user.user_id}"
        logger("CONTRACT", success_msg, "GET /contracts", "INFO")
        return ResponseSchema.success(contracts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contracts: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Specific sub-paths BEFORE /{contract_id} so they are not shadowed ─────────

@contract_router.get("/freelancer/{freelancer_id}", response_model=List[ContractResponse])
async def get_contracts_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts for a given freelancer."""
    try:
        assert_freelancer_owns(current_user, freelancer_id)
        contracts = ContractFunctions.get_contracts_by_freelancer_id(freelancer_id)
        success_msg = f"Retrieved {len(contracts)} contracts for freelancer {freelancer_id}"
        logger("CONTRACT", success_msg, "GET /contracts/freelancer/{freelancer_id}", "INFO")
        return ResponseSchema.success(contracts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contracts for freelancer {freelancer_id}: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts/freelancer/{freelancer_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.get("/client/{client_id}", response_model=List[ContractResponse])
async def get_contracts_by_client(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all contracts for a given client."""
    try:
        assert_client_owns(current_user, client_id)
        contracts = ContractFunctions.get_contracts_by_client_id(client_id)
        success_msg = f"Retrieved {len(contracts)} contracts for client {client_id}"
        logger("CONTRACT", success_msg, "GET /contracts/client/{client_id}", "INFO")
        return ResponseSchema.success(contracts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contracts for client {client_id}: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts/client/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.get("/{contract_id}/generation-data")
async def get_contract_generation_data(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return all auto-filled contract generation fields visible to the current party."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "GET /contracts/{contract_id}/generation-data", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_current_user_is_contract_party(current_user, contract)

        context = ContractGenerationFunctions.build_generation_context(contract_id)
        if not context:
            error_msg = f"Failed to build generation context for contract {contract_id}"
            logger("CONTRACT", error_msg, "GET /contracts/{contract_id}/generation-data", "ERROR")
            return ResponseSchema.error(error_msg, 500)

        success_msg = f"Retrieved generation data for contract {contract_id}"
        logger("CONTRACT", success_msg, "GET /contracts/{contract_id}/generation-data", "INFO")
        return ResponseSchema.success(context, 200)
    except Exception as e:
        error_msg = f"Failed to fetch generation data for contract {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts/{contract_id}/generation-data", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.get("/{contract_id}/pdf-url")
async def get_contract_pdf_url(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return a signed Supabase URL for a generated contract PDF."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "GET /contracts/{contract_id}/pdf-url", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_current_user_is_contract_party(current_user, contract)

        pdf_path = contract.get("contract_pdf_url")
        if not pdf_path:
            return ResponseSchema.error("Contract PDF has not been generated yet", 404)

        signed_url = ContractGenerationFunctions.get_signed_contract_url(pdf_path)
        success_msg = f"Created signed PDF URL for contract {contract_id}"
        logger("CONTRACT", success_msg, "GET /contracts/{contract_id}/pdf-url", "INFO")
        return ResponseSchema.success({"pdf_url": signed_url}, 200)
    except Exception as e:
        error_msg = f"Failed to create PDF URL for contract {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts/{contract_id}/pdf-url", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Generic /{contract_id} GET — must come AFTER all literal sub-paths ────────

@contract_router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Return a single contract by ID."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "GET /contracts/{contract_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_current_user_is_contract_party(current_user, contract)
        success_msg = f"Retrieved contract {contract_id}"
        logger("CONTRACT", success_msg, "GET /contracts/{contract_id}", "INFO")
        return ResponseSchema.success(contract, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contract {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "GET /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Mutations ─────────────────────────────────────────────────────────────────

@contract_router.post("", response_model=ContractResponse, status_code=201)
async def create_contract(contract: ContractCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new contract."""
    try:
        contract_id = contract.contract_id or str(uuid.uuid4())
        if current_user.type == "client":
            client = get_client_profile_for_user(current_user)
            if contract.client_id and str(contract.client_id) != str(client["client_id"]):
                return ResponseSchema.error("Cannot create a contract for another client", 403)
        elif current_user.type == "freelancer":
            freelancer = get_freelancer_profile_for_user(current_user)
            if contract.freelancer_id and str(contract.freelancer_id) != str(freelancer["freelancer_id"]):
                return ResponseSchema.error("Cannot create a contract for another freelancer", 403)
        else:
            return ResponseSchema.error("Only clients or freelancers can create contracts", 403)

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
            total_paid=contract.total_paid
        )

        success_msg = f"Created contract {contract_id}"
        logger("CONTRACT", success_msg, "POST /contracts", "INFO")
        return ResponseSchema.success(new_contract, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("CONTRACT", error_msg, "POST /contracts", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create contract: {str(e)}"
        logger("CONTRACT", error_msg, "POST /contracts", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.post("/{contract_id}/generate", response_model=ContractResponse)
async def generate_contract_pdf(contract_id: str, generation_data: ContractGenerateRequest, current_user: UserInDB = Depends(get_current_user)):
    """Generate a contract PDF and persist the contract terms and storage path."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "POST /contracts/{contract_id}/generate", "WARNING")
            return ResponseSchema.error(error_msg, 404)
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
            },
            milestones=(
                [m.model_dump() for m in generation_data.milestones]
                if generation_data.milestones else None
            ),
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
        success_msg = f"Generated contract PDF for {contract_id}"
        logger("CONTRACT", success_msg, "POST /contracts/{contract_id}/generate", "INFO")
        return ResponseSchema.success(refreshed, 200)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("CONTRACT", error_msg, "POST /contracts/{contract_id}/generate", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to generate contract PDF for {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "POST /contracts/{contract_id}/generate", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.put("/{contract_id}", response_model=ContractResponse)
async def update_contract(contract_id: str, contract_update: ContractUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update an existing contract."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "PUT /contracts/{contract_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        update_data = contract_update.model_dump(exclude_unset=True)
        updated_contract = ContractFunctions.update_contract(contract_id, update_data)

        success_msg = f"Updated contract {contract_id}"
        logger("CONTRACT", success_msg, "PUT /contracts/{contract_id}", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except Exception as e:
        error_msg = f"Failed to update contract {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "PUT /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_router.delete("/{contract_id}", status_code=200)
async def delete_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a contract by ID."""
    try:
        existing_contract = ContractFunctions.get_contract_by_id(contract_id)
        if not existing_contract:
            error_msg = f"Contract {contract_id} not found"
            logger("CONTRACT", error_msg, "DELETE /contracts/{contract_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_current_user_is_contract_party(current_user, existing_contract)

        ContractFunctions.delete_contract(contract_id)

        success_msg = f"Deleted contract {contract_id}"
        logger("CONTRACT", success_msg, "DELETE /contracts/{contract_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete contract {contract_id}: {str(e)}"
        logger("CONTRACT", error_msg, "DELETE /contracts/{contract_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
