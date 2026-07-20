import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile
from typing import List, Optional

from functions.schema_model import RevisionRequest, UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.minio_client import upload_contract_submission_file, guess_mime, resolve_file_url, BUCKET_CONTRACT_SUBMISSIONS, MAX_UPLOAD_FILE_SIZE_BYTES
from routes.contract_submissions.contract_submission_functions import ContractSubmissionFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions
from routes.notifications.notification_functions import NotificationFunctions
from routes.reviews.review_routes import trigger_review_pipeline_on_completion
from routes.client_reviews.client_review_routes import trigger_client_review_pipeline_on_completion


contract_submission_router = APIRouter(
    prefix="/contract-submissions",
    tags=["Contract Submissions"],
)

MAX_REVISION_REQUESTS = 3  # per contract, across its whole lifetime


def _resolve_submission_urls(submission: dict) -> dict:
    for f in submission.get("files", []):
        if f.get("file_url"):
            f["file_url"] = resolve_file_url(BUCKET_CONTRACT_SUBMISSIONS, f["file_url"])
    return submission

ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "png", "jpg", "jpeg", "zip"}
MAX_FILE_SIZE_BYTES = MAX_UPLOAD_FILE_SIZE_BYTES


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

        if not current_user.freelancer_id:
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
                return ResponseSchema.error(f"File too large: {file_name}. Max size is 100 MB", 400)

            validated_files.append({
                "file_name": file_name,
                "file_bytes": file_bytes,
                "content_type": file.content_type or guess_mime(file_name),
            })

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

        full_submission = _resolve_submission_urls(ContractSubmissionFunctions.get_submission_by_id(submission_id))

        # Notify client that work has been submitted
        try:
            client = ClientFunctions.get_client_by_id(str(contract["client_id"]))
            if client:
                await NotificationFunctions.notify(
                    recipient_user_id=str(client["user_id"]),
                    notif_type="work_submitted",
                    title="Work Submitted 📦",
                    body=f"{freelancer.get('full_name')} submitted work for review",
                    data={"contract_id": contract_id, "submission_id": submission_id},
                )
        except Exception as notif_err:
            logger("CONTRACT_SUBMISSION", f"Submission notification failed (non-fatal): {notif_err}", "POST /contract-submissions", "WARNING")

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

        is_party = False
        if current_user.freelancer_id:
            freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
            if freelancer and str(freelancer["freelancer_id"]) == str(contract["freelancer_id"]):
                is_party = True
        if current_user.client_id:
            client = ClientFunctions.get_client_by_user_id(current_user.user_id)
            if client and str(client["client_id"]) == str(contract["client_id"]):
                is_party = True
        if not is_party:
            return ResponseSchema.error("Unauthorized to view these submissions", 403)

        submissions = [_resolve_submission_urls(s) for s in ContractSubmissionFunctions.get_submissions_by_contract_id(contract_id)]
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

        if not current_user.client_id:
            return ResponseSchema.error("Only clients can request revision", 403)

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client:
            return ResponseSchema.error("Client profile not found", 404)

        if client["client_id"] != contract["client_id"]:
            return ResponseSchema.error("Unauthorized to request revision for this contract", 403)

        revision_rounds = ContractSubmissionFunctions.count_revision_rounds(contract_id)
        if revision_rounds >= MAX_REVISION_REQUESTS:
            return ResponseSchema.error(
                f"Maximum number of revision requests ({MAX_REVISION_REQUESTS}) reached for this contract",
                400,
            )

        latest_submission = ContractSubmissionFunctions.request_revision_for_latest_submission(
            contract_id=contract_id,
            note=payload.note,
        )
        if not latest_submission:
            return ResponseSchema.error("No submission found for this contract", 404)
        latest_submission = _resolve_submission_urls(latest_submission)

        # Notify freelancer of revision request
        try:
            freelancer = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            if freelancer:
                await NotificationFunctions.notify(
                    recipient_user_id=str(freelancer["user_id"]),
                    notif_type="revision_requested",
                    title="Revision Requested",
                    body=f"{client.get('full_name')} requested a revision on your submission",
                    data={"contract_id": contract_id},
                )
        except Exception as notif_err:
            logger("CONTRACT_SUBMISSION", f"Revision notification failed (non-fatal): {notif_err}", "PUT /contract-submissions/contract/{contract_id}/request-revision", "WARNING")

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

        if not current_user.client_id:
            return ResponseSchema.error("Only clients can approve submissions", 403)

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client:
            return ResponseSchema.error("Client profile not found", 404)

        if client["client_id"] != contract["client_id"]:
            return ResponseSchema.error("Unauthorized to approve this contract", 403)

        if contract["status"] != "under_review":
            return ResponseSchema.error(
                f"Cannot approve a submission when contract status is '{contract['status']}'", 400
            )

        latest_submission_check = ContractSubmissionFunctions.get_latest_submission_by_contract_id(contract_id)
        if not latest_submission_check:
            return ResponseSchema.error("No submission found for this contract", 404)
        if latest_submission_check["status"] != "submitted":
            return ResponseSchema.error(
                f"Cannot approve a submission that is already '{latest_submission_check['status']}'", 400
            )

        latest_submission = ContractSubmissionFunctions.approve_latest_submission(
            contract_id=contract_id
        )
        if not latest_submission:
            return ResponseSchema.error("No submission found for this contract", 404)
        latest_submission = _resolve_submission_urls(latest_submission)

        await trigger_review_pipeline_on_completion(contract_id, background_tasks)
        await trigger_client_review_pipeline_on_completion(contract_id, background_tasks)

        # Notify freelancer that submission was approved
        try:
            freelancer = FreelancerFunctions.get_freelancer_by_id(str(contract["freelancer_id"]))
            if freelancer:
                await NotificationFunctions.notify(
                    recipient_user_id=str(freelancer["user_id"]),
                    notif_type="contract_completed",
                    title="Submission Approved ✅",
                    body=f"{client.get('full_name')} approved your submission",
                    data={"contract_id": contract_id},
                )
        except Exception as notif_err:
            logger("CONTRACT_SUBMISSION", f"Approval notification failed (non-fatal): {notif_err}", "PUT /contract-submissions/contract/{contract_id}/approve", "WARNING")

        logger("CONTRACT_SUBMISSION", f"Latest submission approved for contract {contract_id}", "PUT /contract-submissions/contract/{contract_id}/approve", "INFO")
        return ResponseSchema.success(latest_submission, 200)

    except Exception as e:
        logger("CONTRACT_SUBMISSION", f"Failed to approve submission: {str(e)}", "PUT /contract-submissions/contract/{contract_id}/approve", "ERROR")
        return ResponseSchema.error(f"Failed to approve submission: {str(e)}", 500)