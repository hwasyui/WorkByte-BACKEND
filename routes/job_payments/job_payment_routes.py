import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends
from typing import List, Optional
from functions.schema_model import (
    JobPaymentCreate, JobPaymentUpdate, JobPaymentResponse,
    JobMilestoneCreate, JobMilestoneBulkCreate, JobMilestoneUpdate, JobMilestoneResponse,
    JobPaymentWithMilestonesCreate,
)
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.job_payments.job_payment_functions import JobPaymentFunctions, JobMilestoneFunctions


job_payment_router = APIRouter(prefix="/job-payments", tags=["Job Payments"])
job_milestone_router = APIRouter(prefix="/job-milestones", tags=["Job Milestones"])


# ─────────────────────────────────────────────────────────────────────────────
# JOB PAYMENT ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@job_payment_router.post("", response_model=JobPaymentResponse, status_code=201)
async def create_job_payment(
    payload: JobPaymentCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """Create a job payment record for a job post. Client only."""
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create job payments", 403)

        result = JobPaymentFunctions.create_job_payment(
            job_post_id=payload.job_post_id,
            payment_type=payload.payment_type,
            payment_option=payload.payment_option,
        )
        logger("JOB_PAYMENT", f"Created job_payment for job_post {payload.job_post_id}", "POST /job-payments", "INFO")
        return ResponseSchema.success(result, 201)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed to create job_payment: {str(e)}", "POST /job-payments", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_payment_router.post("/with-milestones", response_model=JobPaymentResponse, status_code=201)
async def create_job_payment_with_milestones(
    payload: JobPaymentWithMilestonesCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    """
    Single endpoint called from Flutter summary screen.
    Creates job_payment + all job_milestone rows in one request.
    """
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create job payments", 403)

        # 1. Create payment
        payment = JobPaymentFunctions.create_job_payment(
            job_post_id=payload.job_post_id,
            payment_type=payload.payment_type,
            payment_option=payload.payment_option,
        )

        # 2. Create milestones if milestone-based
        milestones = []
        if payload.payment_type == "milestone" and payload.milestones:
            milestones = JobMilestoneFunctions.bulk_create_job_milestones(
                job_payment_id=payment["job_payment_id"],
                milestones=[m.model_dump() for m in payload.milestones],
            )

        logger("JOB_PAYMENT", f"Created job_payment + {len(milestones)} milestones for job_post {payload.job_post_id}", "POST /job-payments/with-milestones", "INFO")
        return ResponseSchema.success({**payment, "milestones": milestones}, 201)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed: {str(e)}", "POST /job-payments/with-milestones", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_payment_router.get("/{job_payment_id}", response_model=JobPaymentResponse)
async def get_job_payment(
    job_payment_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        payment = JobPaymentFunctions.get_job_payment_by_id(job_payment_id)
        if not payment:
            return ResponseSchema.error(f"job_payment {job_payment_id} not found", 404)

        logger("JOB_PAYMENT", f"Retrieved job_payment {job_payment_id}", "GET /job-payments/{id}", "INFO")
        return ResponseSchema.success(payment, 200)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed: {str(e)}", "GET /job-payments/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_payment_router.get("/job-post/{job_post_id}", response_model=JobPaymentResponse)
async def get_job_payment_by_job_post(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        payment = JobPaymentFunctions.get_job_payment_by_job_post_id(job_post_id)
        if not payment:
            return ResponseSchema.error(f"No job_payment found for job_post {job_post_id}", 404)

        logger("JOB_PAYMENT", f"Retrieved job_payment for job_post {job_post_id}", "GET /job-payments/job-post/{id}", "INFO")
        return ResponseSchema.success(payment, 200)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed: {str(e)}", "GET /job-payments/job-post/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_payment_router.put("/{job_payment_id}", response_model=JobPaymentResponse)
async def update_job_payment(
    job_payment_id: str,
    payload: JobPaymentUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can update job payments", 403)

        existing = JobPaymentFunctions.get_job_payment_by_id(job_payment_id)
        if not existing:
            return ResponseSchema.error(f"job_payment {job_payment_id} not found", 404)

        updated = JobPaymentFunctions.update_job_payment(job_payment_id, payload.model_dump(exclude_unset=True))
        logger("JOB_PAYMENT", f"Updated job_payment {job_payment_id}", "PUT /job-payments/{id}", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed: {str(e)}", "PUT /job-payments/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_payment_router.delete("/{job_payment_id}", status_code=200)
async def delete_job_payment(
    job_payment_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can delete job payments", 403)

        existing = JobPaymentFunctions.get_job_payment_by_id(job_payment_id)
        if not existing:
            return ResponseSchema.error(f"job_payment {job_payment_id} not found", 404)

        JobPaymentFunctions.delete_job_payment(job_payment_id)
        logger("JOB_PAYMENT", f"Deleted job_payment {job_payment_id}", "DELETE /job-payments/{id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        logger("JOB_PAYMENT", f"Failed: {str(e)}", "DELETE /job-payments/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


# ─────────────────────────────────────────────────────────────────────────────
# JOB MILESTONE ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@job_milestone_router.post("", response_model=JobMilestoneResponse, status_code=201)
async def create_job_milestone(
    payload: JobMilestoneCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create job milestones", 403)

        result = JobMilestoneFunctions.create_job_milestone(
            job_payment_id=payload.job_payment_id,
            milestone_order=payload.milestone_order,
            work_progress=payload.work_progress,
            payment_percentage=payload.payment_percentage,
        )
        logger("JOB_MILESTONE", f"Created milestone for payment {payload.job_payment_id}", "POST /job-milestones", "INFO")
        return ResponseSchema.success(result, 201)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "POST /job-milestones", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.post("/bulk", status_code=201)
async def bulk_create_job_milestones(
    payload: JobMilestoneBulkCreate,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can create job milestones", 403)

        results = JobMilestoneFunctions.bulk_create_job_milestones(
            job_payment_id=payload.job_payment_id,
            milestones=[m.model_dump() for m in payload.milestones],
        )
        logger("JOB_MILESTONE", f"Bulk-created {len(results)} milestones", "POST /job-milestones/bulk", "INFO")
        return ResponseSchema.success(results, 201)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "POST /job-milestones/bulk", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.get("/{milestone_id}", response_model=JobMilestoneResponse)
async def get_job_milestone(
    milestone_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        milestone = JobMilestoneFunctions.get_job_milestone_by_id(milestone_id)
        if not milestone:
            return ResponseSchema.error(f"job_milestone {milestone_id} not found", 404)

        logger("JOB_MILESTONE", f"Retrieved milestone {milestone_id}", "GET /job-milestones/{id}", "INFO")
        return ResponseSchema.success(milestone, 200)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "GET /job-milestones/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.get("/payment/{job_payment_id}", response_model=List[JobMilestoneResponse])
async def get_milestones_by_payment(
    job_payment_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        milestones = JobMilestoneFunctions.get_job_milestones_by_payment_id(job_payment_id)
        logger("JOB_MILESTONE", f"Retrieved {len(milestones)} milestones for payment {job_payment_id}", "GET /job-milestones/payment/{id}", "INFO")
        return ResponseSchema.success(milestones, 200)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "GET /job-milestones/payment/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.get("/job-post/{job_post_id}", response_model=List[JobMilestoneResponse])
async def get_milestones_by_job_post(
    job_post_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        milestones = JobMilestoneFunctions.get_job_milestones_by_job_post_id(job_post_id)
        logger("JOB_MILESTONE", f"Retrieved {len(milestones)} milestones for job_post {job_post_id}", "GET /job-milestones/job-post/{id}", "INFO")
        return ResponseSchema.success(milestones, 200)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "GET /job-milestones/job-post/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.put("/{milestone_id}", response_model=JobMilestoneResponse)
async def update_job_milestone(
    milestone_id: str,
    payload: JobMilestoneUpdate,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can update job milestones", 403)

        existing = JobMilestoneFunctions.get_job_milestone_by_id(milestone_id)
        if not existing:
            return ResponseSchema.error(f"job_milestone {milestone_id} not found", 404)

        updated = JobMilestoneFunctions.update_job_milestone(milestone_id, payload.model_dump(exclude_unset=True))
        logger("JOB_MILESTONE", f"Updated milestone {milestone_id}", "PUT /job-milestones/{id}", "INFO")
        return ResponseSchema.success(updated, 200)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "PUT /job-milestones/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)


@job_milestone_router.delete("/{milestone_id}", status_code=200)
async def delete_job_milestone(
    milestone_id: str,
    current_user: UserInDB = Depends(get_current_user)
):
    try:
        if current_user.type != "client":
            return ResponseSchema.error("Only clients can delete job milestones", 403)

        existing = JobMilestoneFunctions.get_job_milestone_by_id(milestone_id)
        if not existing:
            return ResponseSchema.error(f"job_milestone {milestone_id} not found", 404)

        JobMilestoneFunctions.delete_job_milestone(milestone_id)
        logger("JOB_MILESTONE", f"Deleted milestone {milestone_id}", "DELETE /job-milestones/{id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        logger("JOB_MILESTONE", f"Failed: {str(e)}", "DELETE /job-milestones/{id}", "ERROR")
        return ResponseSchema.error(str(e), 500)