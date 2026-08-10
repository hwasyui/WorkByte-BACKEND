import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import uuid
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from typing import Optional

from functions.schema_model import UserInDB
from functions.authentication import get_current_user, get_client_user, get_freelancer_user
from functions.access_control import assert_client_owns, assert_freelancer_owns, assert_current_user_is_contract_party
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.minio_client import upload_payment_proof_file, guess_mime, resolve_file_url, BUCKET_PAYMENT_PROOFS, MAX_UPLOAD_FILE_SIZE_BYTES
from routes.contracts.contract_functions import ContractFunctions
from routes.payments.payment_functions import PaymentFunctions, PLATFORM_COMMISSION_RATE

payment_router = APIRouter(prefix="/contracts", tags=["Payments"])
payment_config_router = APIRouter(prefix="/payments", tags=["Payments"])

ALLOWED_PROOF_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}


def _get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def _resolve_proof_url(proof: dict) -> dict:
    if proof.get("file_url"):
        proof["file_url"] = resolve_file_url(BUCKET_PAYMENT_PROOFS, proof["file_url"])
    return proof


@payment_config_router.get("/commission-rate")
async def get_commission_rate(current_user: UserInDB = Depends(get_current_user)):
    return ResponseSchema.success({"commission_rate": PLATFORM_COMMISSION_RATE}, 200)


@payment_router.post("/{contract_id}/payment-proof")
async def upload_payment_proof(
    contract_id: str,
    payee: str = Form(...),
    amount: float = Form(...),
    reference_number: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: UserInDB = Depends(get_client_user),
):
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_client_owns(current_user, contract["client_id"])

        if payee not in ("freelancer", "admin"):
            return ResponseSchema.error("payee must be 'freelancer' or 'admin'", 400)

        file_name = file.filename or "proof"
        ext = _get_extension(file_name)
        if ext not in ALLOWED_PROOF_EXTENSIONS:
            return ResponseSchema.error(f"File type not allowed: {file_name}", 400)

        file_bytes = await file.read()
        if not file_bytes:
            return ResponseSchema.error("Proof file must not be empty", 400)
        if len(file_bytes) > MAX_UPLOAD_FILE_SIZE_BYTES:
            return ResponseSchema.error(f"File too large: {file_name}. Max size is 100 MB", 400)

        proof_id = str(uuid.uuid4())
        file_url = upload_payment_proof_file(
            contract_id=contract_id,
            proof_id=proof_id,
            file_name=file_name,
            file_bytes=file_bytes,
            content_type=file.content_type or guess_mime(file_name),
        )

        proof = PaymentFunctions.create_proof(
            contract_id=contract_id,
            payee=payee,
            amount=amount,
            reference_number=reference_number,
            file_url=file_url,
            uploaded_by=str(current_user.user_id),
            proof_id=proof_id,
        )
        proof = _resolve_proof_url(proof)

        logger("PAYMENTS", f"Payment proof {proof_id} uploaded for contract {contract_id} ({payee})", "POST /contracts/{contract_id}/payment-proof", "INFO")
        return ResponseSchema.success(proof, 201)
    except HTTPException:
        raise
    except ValueError as e:
        return ResponseSchema.error(str(e), 400)
    except Exception as e:
        logger("PAYMENTS", f"Failed to upload payment proof: {str(e)}", "POST /contracts/{contract_id}/payment-proof", "ERROR")
        return ResponseSchema.error("Failed to upload payment proof. Please try again.", 500)


@payment_router.post("/{contract_id}/confirm-receipt")
async def confirm_receipt(
    contract_id: str,
    current_user: UserInDB = Depends(get_freelancer_user),
):
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        assert_freelancer_owns(current_user, contract["freelancer_id"])

        updated_contract = PaymentFunctions.confirm_freelancer_receipt(
            contract_id=contract_id,
            freelancer_user_id=str(current_user.user_id),
        )

        logger("PAYMENTS", f"Freelancer confirmed receipt for contract {contract_id}", "POST /contracts/{contract_id}/confirm-receipt", "INFO")
        return ResponseSchema.success(updated_contract, 200)
    except HTTPException:
        raise
    except ValueError as e:
        return ResponseSchema.error(str(e), 400)
    except Exception as e:
        logger("PAYMENTS", f"Failed to confirm receipt: {str(e)}", "POST /contracts/{contract_id}/confirm-receipt", "ERROR")
        return ResponseSchema.error("Failed to confirm receipt. Please try again.", 500)


@payment_router.get("/{contract_id}/payment-proof")
async def list_payment_proofs(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = ContractFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)
        if not current_user.is_admin:
            assert_current_user_is_contract_party(current_user, contract)

        proofs = [_resolve_proof_url(p) for p in PaymentFunctions.get_proofs_by_contract_id(contract_id)]

        logger("PAYMENTS", f"Retrieved {len(proofs)} payment proof(s) for contract {contract_id}", "GET /contracts/{contract_id}/payment-proof", "INFO")
        return ResponseSchema.success(proofs, 200)
    except HTTPException:
        raise
    except Exception as e:
        logger("PAYMENTS", f"Failed to fetch payment proofs: {str(e)}", "GET /contracts/{contract_id}/payment-proof", "ERROR")
        return ResponseSchema.error("Failed to fetch payment proofs. Please try again.", 500)
