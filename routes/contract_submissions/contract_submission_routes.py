import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from typing import List, Optional

from functions.schema_model import RevisionRequest, UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.supabase_client import upload_contract_submission_file, guess_mime
from routes.contract_submissions.contract_submission_functions import ContractSubmissionFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from routes.reviews.review_routes import trigger_review_pipeline_on_completion


contract_submission_router = APIRouter(
    prefix="/contract-submissions",
    tags=["Contract Submissions"],
)

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "png", "jpg", "jpeg", "zip"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB per file


def _get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


@contract_submission_router.post("")
async def create_contract_submission(
    contract_id: str = Form(...),
    note: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if current_user.type != "freelancer":
            return ResponseSchema.error("Only freelancers can submit work", 403)

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found", 404)

        if freelancer["freelancer_id"] != contract["freelancer_id"]:
            return ResponseSchema.error("You can only submit work for your own contract", 403)

        if contract["status"] not in ("active", "revision_requested"):
            return ResponseSchema.error(
                f"Cannot submit work when contract status is '{contract['status']}'",
                400,
            )

        if not files or len(files) == 0:
            return ResponseSchema.error("At least one file is required", 400)

        ContractSubmissionFunctions.supersede_latest_revision_requested_submission(contract_id)

        validated_files = []
        for file in files:
            file_name = file.filename or "file"
            ext = _get_extension(file_name)

            if ext not in ALLOWED_EXTENSIONS:
                return ResponseSchema.error(f"File type not allowed: {file_name}", 400)

            file_bytes = await file.read()
            if len(file_bytes) > MAX_FILE_SIZE_BYTES:
                return ResponseSchema.error(f"File too large: {file_name}. Max size is 10 MB", 400)

            validated_files.append({
                "file_name": file_name,
                "file_bytes": file_bytes,
                "content_type": file.content_type or guess_mime(file_name),
            })

        # create_submission handles: insert + contract status update + system message
        submission = ContractSubmissionFunctions.create_submission(
            contract_id=contract_id,
            submitted_by=str(current_user.user_id),
            note=note,
            status="submitted",
        )

        submission_id = submission["submission_id"]

        for item in validated_files:
            file_url = upload_contract_submission_file(
                contract_id=contract_id,
                submission_id=submission_id,
                file_name=item["file_name"],
                file_bytes=item["file_bytes"],
                content_type=item["content_type"],
            )
            ContractSubmissionFunctions.add_submission_file(
                submission_id=submission_id,
                file_url=file_url,
                file_name=item["file_name"],
                file_size_bytes=len(item["file_bytes"]),
                mime_type=item["content_type"],
            )

        full_submission = ContractSubmissionFunctions.get_submission_by_id(submission_id)

        logger("CONTRACT_SUBMISSION", f"Submission {submission_id} created for contract {contract_id}", "POST /contract-submissions", "INFO")
        return ResponseSchema.success(full_submission, 201)

    except Exception as e:
        logger("CONTRACT_SUBMISSION", f"Failed to create submission: {str(e)}", "POST /contract-submissions", "ERROR")
        return ResponseSchema.error(f"Failed to create submission: {str(e)}", 500)


@contract_submission_router.get("/contract/{contract_id}")
async def get_submissions_by_contract(
    contract_id: str,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if current_user.type == "freelancer":
            freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
            if not freelancer:
                return ResponseSchema.error("Freelancer profile not found", 404)
            if freelancer["freelancer_id"] != contract["freelancer_id"]:
                return ResponseSchema.error("Unauthorized to view these submissions", 403)

        elif current_user.type == "client":
            client = ClientFunctions.get_client_by_user_id(current_user.user_id)
            if not client:
                return ResponseSchema.error("Client profile not found", 404)
            if client["client_id"] != contract["client_id"]:
                return ResponseSchema.error("Unauthorized to view these submissions", 403)

        else:
            return ResponseSchema.error("Unauthorized", 403)

        submissions = ContractSubmissionFunctions.get_submissions_by_contract_id(contract_id)
        logger("CONTRACT_SUBMISSION", f"Retrieved {len(submissions)} submissions for contract {contract_id}", "GET /contract-submissions/contract/{contract_id}", "INFO")
        return ResponseSchema.success(submissions, 200)

    except Exception as e:
        logger("CONTRACT_SUBMISSION", f"Failed to fetch submissions: {str(e)}", "GET /contract-submissions/contract/{contract_id}", "ERROR")
        return ResponseSchema.error(f"Failed to fetch submissions: {str(e)}", 500)


@contract_submission_router.put("/contract/{contract_id}/request-revision")
async def request_revision_for_latest_submission(
    contract_id: str,
    payload: RevisionRequest, 
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if current_user.type != "client":
            return ResponseSchema.error("Only clients can request revision", 403)

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client:
            return ResponseSchema.error("Client profile not found", 404)

        if client["client_id"] != contract["client_id"]:
            return ResponseSchema.error("Unauthorized to request revision for this contract", 403)

        latest_submission = ContractSubmissionFunctions.request_revision_for_latest_submission(
            contract_id=contract_id,
            note=payload.note
        )
        if not latest_submission:
            return ResponseSchema.error("No submission found for this contract", 404)

        logger("CONTRACT_SUBMISSION", f"Revision requested for latest submission in contract {contract_id}", "PUT /contract-submissions/contract/{contract_id}/request-revision", "INFO")
        return ResponseSchema.success(latest_submission, 200)

    except Exception as e:
        logger("CONTRACT_SUBMISSION", f"Failed to request revision: {str(e)}", "PUT /contract-submissions/contract/{contract_id}/request-revision", "ERROR")
        return ResponseSchema.error(f"Failed to request revision: {str(e)}", 500)


@contract_submission_router.put("/contract/{contract_id}/approve")
async def approve_latest_submission(
    contract_id: str,
    background_tasks: BackgroundTasks,
    current_user: UserInDB = Depends(get_current_user),
):
    try:
        contract = ContractSubmissionFunctions.get_contract_by_id(contract_id)
        if not contract:
            return ResponseSchema.error(f"Contract {contract_id} not found", 404)

        if current_user.type != "client":
            return ResponseSchema.error("Only clients can approve submissions", 403)

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client:
            return ResponseSchema.error("Client profile not found", 404)

        if client["client_id"] != contract["client_id"]:
            return ResponseSchema.error("Unauthorized to approve this contract", 403)

        latest_submission = ContractSubmissionFunctions.approve_latest_submission(
            contract_id=contract_id
        )
        if not latest_submission:
            return ResponseSchema.error("No submission found for this contract", 404)

        await trigger_review_pipeline_on_completion(contract_id, background_tasks)

        logger("CONTRACT_SUBMISSION", f"Latest submission approved for contract {contract_id}", "PUT /contract-submissions/contract/{contract_id}/approve", "INFO")
        return ResponseSchema.success(latest_submission, 200)

    except Exception as e:
        logger("CONTRACT_SUBMISSION", f"Failed to approve submission: {str(e)}", "PUT /contract-submissions/contract/{contract_id}/approve", "ERROR")
        return ResponseSchema.error(f"Failed to approve submission: {str(e)}", 500)