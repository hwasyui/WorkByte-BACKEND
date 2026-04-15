import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ContractMilestoneCreate, ContractMilestoneUpdate, ContractMilestoneResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_current_user_is_contract_party
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.contract_milestones.contract_milestone_functions import ContractMilestoneFunctions
from routes.contracts.contract_functions import ContractFunctions

contract_milestone_router = APIRouter(prefix="/contract-milestones", tags=["Contract Milestones"])


# ── GET /contract-milestones ──────────────────────────────────────────────────

@contract_milestone_router.get("", response_model=List[ContractMilestoneResponse])
async def get_all_contract_milestones(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all contract milestones visible to the current user."""
    try:
        if current_user.type == "client":
            client_contracts = ContractFunctions.get_contracts_by_client_id(current_user.user_id)
            contract_ids = [c["contract_id"] for c in client_contracts]
        elif current_user.type == "freelancer":
            freelancer_contracts = ContractFunctions.get_contracts_by_freelancer_id(current_user.user_id)
            contract_ids = [c["contract_id"] for c in freelancer_contracts]
        else:
            return ResponseSchema.error("Only clients and freelancers can access contract milestones", 403)
        milestones = []
        for contract_id in contract_ids:
            milestones.extend(ContractMilestoneFunctions.get_contract_milestones_by_contract_id(contract_id))
        success_msg = f"Retrieved {len(milestones)} contract milestones for current user"
        logger("CONTRACT_MILESTONE", success_msg, "GET /contract-milestones", "INFO")
        return ResponseSchema.success(milestones, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contract milestones: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "GET /contract-milestones", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Literal sub-path BEFORE generic /{milestone_id} ──────────────────────────

@contract_milestone_router.get("/contract/{contract_id}", response_model=List[ContractMilestoneResponse])
async def get_contract_milestones_by_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all milestones for a specific contract."""
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        assert_current_user_is_contract_party(current_user, contract)
        milestones = ContractMilestoneFunctions.get_contract_milestones_by_contract_id(contract_id)
        success_msg = f"Retrieved {len(milestones)} milestones for contract {contract_id}"
        logger("CONTRACT_MILESTONE", success_msg, "GET /contract-milestones/contract/{contract_id}", "INFO")
        return ResponseSchema.success(milestones, 200)
    except Exception as e:
        error_msg = f"Failed to fetch milestones for contract {contract_id}: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "GET /contract-milestones/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Generic /{milestone_id} GET — must come AFTER all literal sub-paths ───────

@contract_milestone_router.get("/{milestone_id}", response_model=ContractMilestoneResponse)
async def get_contract_milestone(milestone_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single contract milestone by ID."""
    try:
        milestone = ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
        if not milestone:
            error_msg = f"Contract milestone {milestone_id} not found"
            logger("CONTRACT_MILESTONE", error_msg, "GET /contract-milestones/{milestone_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        contract = ContractFunctions.get_contract_by_id(milestone["contract_id"])
        assert_current_user_is_contract_party(current_user, contract)
        success_msg = f"Retrieved contract milestone {milestone_id}"
        logger("CONTRACT_MILESTONE", success_msg, "GET /contract-milestones/{milestone_id}", "INFO")
        return ResponseSchema.success(milestone, 200)
    except Exception as e:
        error_msg = f"Failed to fetch contract milestone {milestone_id}: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "GET /contract-milestones/{milestone_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


# ── Mutations ─────────────────────────────────────────────────────────────────

@contract_milestone_router.post("", response_model=ContractMilestoneResponse, status_code=201)
async def create_contract_milestone(milestone: ContractMilestoneCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new contract milestone."""
    try:
        milestone_id = milestone.milestone_id or str(uuid.uuid4())
        contract = ContractFunctions.get_contract_by_id(milestone.contract_id)
        assert_current_user_is_contract_party(current_user, contract)

        new_milestone = ContractMilestoneFunctions.create_contract_milestone(
            contract_id=milestone.contract_id,
            milestone_title=milestone.milestone_title,
            milestone_percentage=milestone.milestone_percentage or 0.0,
            milestone_amount=milestone.milestone_amount or 0.0,
            milestone_order=milestone.milestone_order or 0,
            milestone_description=milestone.milestone_description,
            due_date=milestone.due_date,
            status=milestone.status,
        )

        success_msg = f"Created contract milestone {milestone_id} for contract {milestone.contract_id}"
        logger("CONTRACT_MILESTONE", success_msg, "POST /contract-milestones", "INFO")
        return ResponseSchema.success(new_milestone, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "POST /contract-milestones", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create contract milestone: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "POST /contract-milestones", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_milestone_router.put("/{milestone_id}", response_model=ContractMilestoneResponse)
async def update_contract_milestone(milestone_id: str, milestone_update: ContractMilestoneUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update contract milestone information with role-based approval flows."""
    try:
        existing_milestone = ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
        if not existing_milestone:
            error_msg = f"Contract milestone {milestone_id} not found"
            logger("CONTRACT_MILESTONE", error_msg, "PUT /contract-milestones/{milestone_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        contract = ContractFunctions.get_contract_by_id(existing_milestone["contract_id"])
        assert_current_user_is_contract_party(current_user, contract)

        update_data = milestone_update.model_dump(exclude_unset=True)

        # Freelancer may modify milestone details but status must remain pending until client approves.
        if current_user.type == "freelancer":
            if "status" in update_data and update_data.get("status") not in [None, "pending"]:
                return ResponseSchema.error("Freelancers can only leave milestone in pending state for approval", 403)
            update_data["status"] = "pending"
            update_data["client_approved"] = False
            update_data["payment_requested"] = False
            update_data["freelancer_confirmed_paid"] = False

        # Client may approve state transitions.
        if current_user.type == "client":
            desired_status = update_data.get("status")
            if desired_status and desired_status not in ["pending", "in_progress", "completed", "paid"]:
                return ResponseSchema.error("Invalid status update value", 400)
            if desired_status in ["in_progress", "completed"]:
                update_data["client_approved"] = True
            if desired_status == "paid":
                update_data["payment_requested"] = True
                update_data["client_approved"] = True
                update_data["freelancer_confirmed_paid"] = False

        if current_user.type not in ["client", "freelancer"]:
            return ResponseSchema.error("Only client or freelancer can update milestones", 403)

        updated_milestone = ContractMilestoneFunctions.update_contract_milestone(milestone_id, update_data)

        success_msg = f"Updated contract milestone {milestone_id}"
        logger("CONTRACT_MILESTONE", success_msg, "PUT /contract-milestones/{milestone_id}", "INFO")
        return ResponseSchema.success(updated_milestone, 200)
    except Exception as e:
        error_msg = f"Failed to update contract milestone {milestone_id}: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "PUT /contract-milestones/{milestone_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_milestone_router.delete("/{milestone_id}", status_code=200)
async def delete_contract_milestone(milestone_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a contract milestone."""
    try:
        existing_milestone = ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
        if not existing_milestone:
            error_msg = f"Contract milestone {milestone_id} not found"
            logger("CONTRACT_MILESTONE", error_msg, "DELETE /contract-milestones/{milestone_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        contract = ContractFunctions.get_contract_by_id(existing_milestone["contract_id"])
        assert_current_user_is_contract_party(current_user, contract)

        ContractMilestoneFunctions.delete_contract_milestone(milestone_id)

        success_msg = f"Deleted contract milestone {milestone_id}"
        logger("CONTRACT_MILESTONE", success_msg, "DELETE /contract-milestones/{milestone_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete contract milestone {milestone_id}: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "DELETE /contract-milestones/{milestone_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@contract_milestone_router.post("/{milestone_id}/confirm-payment", response_model=ContractMilestoneResponse)
async def confirm_milestone_payment(milestone_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Freelancer confirms a client-requested milestone payment."""
    try:
        if current_user.type != "freelancer":
            return ResponseSchema.error("Only freelancers can confirm payment", 403)

        existing_milestone = ContractMilestoneFunctions.get_contract_milestone_by_id(milestone_id)
        if not existing_milestone:
            return ResponseSchema.error("Contract milestone not found", 404)
        contract = ContractFunctions.get_contract_by_id(existing_milestone["contract_id"])
        assert_current_user_is_contract_party(current_user, contract)

        if not existing_milestone.get("payment_requested"):
            return ResponseSchema.error("No payment request pending", 400)

        update_data = {
            "status": "paid",
            "freelancer_confirmed_paid": True,
            "payment_released": True,
        }

        updated_milestone = ContractMilestoneFunctions.update_contract_milestone(milestone_id, update_data)
        if not updated_milestone:
            return ResponseSchema.error("Failed to confirm payment", 500)

        logger("CONTRACT_MILESTONE", f"Freelancer confirmed payment for milestone {milestone_id}",
               "POST /contract-milestones/{milestone_id}/confirm-payment", "INFO")
        return ResponseSchema.success(updated_milestone, 200)

    except Exception as e:
        error_msg = f"Failed to confirm payment for milestone {milestone_id}: {str(e)}"
        logger("CONTRACT_MILESTONE", error_msg, "POST /contract-milestones/{milestone_id}/confirm-payment", "ERROR")
        return ResponseSchema.error(error_msg, 500)
