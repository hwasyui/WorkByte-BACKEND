import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import ContractCreate, ContractUpdate, ContractResponse
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_current_user_is_contract_party, assert_client_owns, assert_freelancer_owns, get_client_profile_for_user, get_freelancer_profile_for_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.contracts.contract_functions import ContractFunctions

contract_router = APIRouter(prefix="/contracts", tags=["Contracts"])


@contract_router.get("", response_model=List[ContractResponse])
async def get_all_contracts(limit: Optional[int] = None, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all contracts - Authenticated users only - JSON response"""
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


@contract_router.get("/{contract_id}", response_model=ContractResponse)
async def get_contract(contract_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single contract by ID - Authenticated users only - JSON response"""
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


@contract_router.get("/freelancer/{freelancer_id}", response_model=List[ContractResponse])
async def get_contracts_by_freelancer(freelancer_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all contracts for a specific freelancer - Authenticated users only - JSON response"""
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
    """Fetch all contracts for a specific client - Authenticated users only - JSON response"""
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


@contract_router.post("", response_model=ContractResponse, status_code=201)
async def create_contract(contract: ContractCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new contract - Authenticated users only - JSON body accepted"""
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


@contract_router.put("/{contract_id}", response_model=ContractResponse)
async def update_contract(contract_id: str, contract_update: ContractUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update contract information - Authenticated users only"""
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
    """Delete a contract - Authenticated users only"""
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
