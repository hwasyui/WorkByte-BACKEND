import asyncio
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query, status
from typing import List, Optional, Dict
import uuid
from functions.schema_model import (
    JobPostCreate,
    JobPostUpdate,
    JobPostResponse,
    JobPostScopeCalculationRequest,
    JobPostScopeCalculationResponse,
)
from functions.schema_model import UserInDB
from functions.authentication import get_current_user
from functions.access_control import assert_client_owns, get_client_profile_for_user, get_freelancer_profile_for_user
from functions.authentication import get_freelancer_user
from routes.clients.client_functions import ClientFunctions as _ClientFunctions
from functions.logger import logger
from functions.response_utils import ResponseSchema
from functions.db_manager import get_db
from routes.job_posts.job_post_functions import JobPostFunctions, convert_uuids_to_str
from ai_related.job_engine.embedding_manager import mark_job_dirty
from routes.admin.admin_functions import queue_harmful_text_scan, queue_scam_scan

job_post_router = APIRouter(prefix="/job-posts", tags=["Job Posts"])


_VALID_JOB_STATUSES   = {"active", "closed", "filled", "draft", "all"}
_VALID_JOB_ORDER_BY   = {"created_at", "posted_at", "deadline", "job_title", "proposal_count", "view_count"}


@job_post_router.post("/calculate-project-scope", response_model=JobPostScopeCalculationResponse)
async def calculate_project_scope(
    payload: JobPostScopeCalculationRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Calculate a recommended project_scope from job-post inputs without saving to the database."""
    try:
        result = JobPostFunctions.calculate_project_scope(
            job_title=payload.job_title,
            job_description=payload.job_description,
            project_type=payload.project_type,
            estimated_duration=payload.estimated_duration,
            working_days=payload.working_days,
            experience_level=payload.experience_level,
            role_count=payload.role_count,
            roles=[role.model_dump() for role in (payload.roles or [])],
        )
        logger("JOB_POST", "Calculated project scope recommendation", "POST /job-posts/calculate-project-scope", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        error_msg = f"Failed to calculate project scope: {str(e)}"
        logger("JOB_POST", error_msg, "POST /job-posts/calculate-project-scope", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.get("", response_model=List[JobPostResponse])
async def get_all_job_posts(
    status: str = Query(
        default="active",
        description="Filter by job status. One of: active (default), closed, filled, draft, all. "
                    "Draft jobs are only visible to their creator.",
    ),
    order_by: str = Query(
        default="created_at",
        description="Sort field. One of: created_at (default), posted_at, deadline, job_title, proposal_count, view_count",
    ),
    order_dir: str = Query(default="desc", description="asc or desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    category: Optional[str] = Query(default=None, description="Filter by project category, e.g. mobiledev, backenddev"),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Browse all job posts with pagination, status filter, and sorting.
    Defaults to active jobs. Draft visibility is restricted to the owning client.
    """
    try:
        if status not in _VALID_JOB_STATUSES:
            return ResponseSchema.error(
                f"Invalid status '{status}'. Valid values: {', '.join(sorted(_VALID_JOB_STATUSES))}", 400
            )
        if order_by not in _VALID_JOB_ORDER_BY:
            return ResponseSchema.error(
                f"Invalid order_by '{order_by}'. Valid values: {', '.join(sorted(_VALID_JOB_ORDER_BY))}", 400
            )

        # Resolve requesting client_id for draft gate (None for freelancers / non-clients)
        requesting_client_id = None
        if current_user.client_id:
            client = _ClientFunctions.get_client_by_user_id(current_user.user_id)
            if client:
                requesting_client_id = str(client["client_id"])

        result = JobPostFunctions.browse_job_posts(
            status=status,
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            page_size=page_size,
            requesting_client_id=requesting_client_id,
            category=category,
        )
        logger("JOB_POST", f"Browsed job posts: status={status} page={page}", "GET /job-posts", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_POST", f"Failed to fetch job posts: {str(e)}", "GET /job-posts", "ERROR")
        return ResponseSchema.error(f"Failed to fetch job posts: {str(e)}", 500)

@job_post_router.get("/category-counts")
async def get_category_counts(
    current_user: UserInDB = Depends(get_current_user),
):
    """Return job count per projectcategory for active posts, sorted descending."""
    try:
        result = JobPostFunctions.get_category_counts()
        logger("JOBPOST", "Fetched category counts", "GET job-posts/category-counts", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        return ResponseSchema.error(f"Failed to fetch category counts: {str(e)}", 500)


@job_post_router.get("/popular")
async def get_popular_jobs(
    category: Optional[str] = Query(default=None, description="Filter by project category"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    current_user: UserInDB = Depends(get_current_user),
):
    """Active jobs ranked by proposal count then view count. Optional category filter."""
    try:
        result = JobPostFunctions.browse_job_posts(
            status="active",
            order_by="proposal_count",
            order_dir="desc",
            page=page,
            page_size=page_size,
            category=category,
        )
        logger("JOB_POST", f"Popular jobs fetched: page={page} category={category}", "GET /job-posts/popular", "INFO")
        return ResponseSchema.success(result, 200)
    except Exception as e:
        logger("JOB_POST", f"Failed to fetch popular jobs: {str(e)}", "GET /job-posts/popular", "ERROR")
        return ResponseSchema.error(f"Failed to fetch popular jobs: {str(e)}", 500)


@job_post_router.get("/relevant")
async def get_relevant_jobs(
    category: Optional[str] = Query(default=None, description="Filter by project category"),
    limit: int = Query(default=10, ge=1, le=50),
    current_user: UserInDB = Depends(get_freelancer_user),
):
    """
    Returns active jobs most relevant to the logged-in freelancer, ranked by
    cosine similarity. We compare each job's role embeddings against everything
    we know about the freelancer: their profile, past contracts, and portfolio.
    The highest similarity across all those sources determines a job's rank.
    Returns an empty list if the freelancer has no embeddings yet.
    """
    try:
        freelancer = get_freelancer_profile_for_user(current_user)
        fid = str(freelancer["freelancer_id"])
        db = get_db()

        category_filter = "AND jp.project_category = :category" if category else ""
        params: dict = {"fid": fid, "limit": limit}
        if category:
            params["category"] = category

        rows = db.execute_query(
            f"""
            WITH candidate_roles AS (
                -- Stage 1: metadata pre-filter. Restricts the candidate set to active jobs
                -- (with optional category) before any vector math runs.
                SELECT jre.job_post_id, jre.embedding_vector
                FROM job_role_embedding jre
                JOIN job_post jp ON jp.job_post_id = jre.job_post_id
                WHERE jp.status = 'active'
                  AND jre.embedding_vector IS NOT NULL
                  {category_filter}
            ),
            freelancer_vecs AS (
                SELECT embedding_vector FROM freelancer_embedding
                WHERE freelancer_id = :fid AND embedding_vector IS NOT NULL
                UNION ALL
                SELECT embedding_vector FROM portfolio_embedding
                WHERE freelancer_id = :fid AND embedding_vector IS NOT NULL
                UNION ALL
                SELECT embedding_vector FROM contract_embedding
                WHERE freelancer_id = :fid AND embedding_vector IS NOT NULL
            ),
            similarity_scores AS (
                -- Stage 2: cosine similarity runs only on the pre-filtered candidate set.
                SELECT cr.job_post_id,
                       MAX(1 - (cr.embedding_vector <=> fv.embedding_vector)) AS similarity_score
                FROM candidate_roles cr
                CROSS JOIN freelancer_vecs fv
                GROUP BY cr.job_post_id
            )
            SELECT
                jp.job_post_id, jp.client_id, jp.job_title, jp.job_description,
                jp.project_type, jp.project_scope, jp.estimated_duration,
                jp.working_days, jp.deadline, jp.experience_level, jp.status,
                jp.is_ai_generated, jp.view_count, jp.project_category,
                jp.created_at, jp.updated_at, jp.posted_at, jp.closed_at,
                jp.closure_reason, jp.closure_note,
                COUNT(DISTINCT jr.job_role_id) AS role_count,
                COALESCE(SUM(DISTINCT jr.positions_available), 0) AS available_positions,
                c.full_name AS client_name,
                c.profile_picture_url,
                (SELECT COUNT(*) FROM proposal p WHERE p.job_post_id = jp.job_post_id) AS proposal_count,
                ss.similarity_score
            FROM similarity_scores ss
            JOIN job_post jp ON jp.job_post_id = ss.job_post_id
            JOIN job_role jr ON jr.job_post_id = jp.job_post_id
            LEFT JOIN client c ON c.client_id = jp.client_id
            GROUP BY
                jp.job_post_id, jp.client_id, jp.job_title, jp.job_description,
                jp.project_type, jp.project_scope, jp.estimated_duration,
                jp.working_days, jp.deadline, jp.experience_level, jp.status,
                jp.is_ai_generated, jp.view_count, jp.project_category,
                jp.created_at, jp.updated_at, jp.posted_at, jp.closed_at,
                jp.closure_reason, jp.closure_note,
                c.full_name, c.profile_picture_url, ss.similarity_score
            ORDER BY ss.similarity_score DESC
            LIMIT :limit
            """,
            params,
        )

        if not rows:
            return ResponseSchema.success([], 200)

        items = [convert_uuids_to_str(dict(row)) for row in rows]
        logger("JOB_POST", f"Relevant jobs fetched: freelancer={fid} count={len(items)} category={category}", "GET /job-posts/relevant", "INFO")
        return ResponseSchema.success(items, 200)

    except Exception as e:
        logger("JOB_POST", f"Failed to fetch relevant jobs: {str(e)}", "GET /job-posts/relevant", "ERROR")
        return ResponseSchema.error(f"Failed to fetch relevant jobs: {str(e)}", 500)

@job_post_router.get("/search", response_model=List[JobPostResponse])
async def search_job_posts(
    name: str = Query(..., description="Keyword to search in job title or description"),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: UserInDB = Depends(get_current_user),
):
    """Search active job posts by title or description keyword."""
    try:
        results = JobPostFunctions.search_job_posts(name, limit=limit)
        logger("JOB_POST", f"Search '{name}': {len(results)} results", "GET /job-posts/search", "INFO")
        return ResponseSchema.success(results, 200)
    except Exception as e:
        error_msg = f"Failed to search job posts: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts/search", "ERROR")
        return ResponseSchema.error(error_msg, 500)

@job_post_router.get("/client/{client_id}", response_model=List[JobPostResponse])
async def get_job_posts_by_client(client_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch all job posts for a specific client - Authenticated users only - JSON response."""
    try:
        job_posts = JobPostFunctions.get_job_posts_by_client_id(client_id)
        success_msg = f"Retrieved {len(job_posts)} job posts for client {client_id}"
        logger("JOB_POST", success_msg, "GET /job-posts/client/{client_id}", "INFO")
        return ResponseSchema.success(job_posts, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job posts for client {client_id}: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts/client/{client_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.get("/{job_post_id}", response_model=JobPostResponse)
async def get_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Fetch a single job post by ID - Authenticated users only - JSON response."""
    try:
        job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "GET /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        success_msg = f"Retrieved job post {job_post_id}"
        logger("JOB_POST", success_msg, "GET /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success(job_post, 200)
    except Exception as e:
        error_msg = f"Failed to fetch job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "GET /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.post("", response_model=JobPostResponse, status_code=201)
async def create_job_post(job_post: JobPostCreate, current_user: UserInDB = Depends(get_current_user)):
    """Create a new job post - Authenticated users only - JSON body accepted."""
    try:
        job_post_id = job_post.job_post_id or str(uuid.uuid4())
        client = get_client_profile_for_user(current_user)
        if job_post.client_id and str(job_post.client_id) != str(client["client_id"]):
            return ResponseSchema.error("Cannot create a job post for another client", 403)
        
        resolved_project_scope = job_post.project_scope
        if not resolved_project_scope:
            calculation = JobPostFunctions.calculate_project_scope(
                job_title=job_post.job_title,
                job_description=job_post.job_description,
                project_type=job_post.project_type,
                estimated_duration=job_post.estimated_duration,
                working_days=job_post.working_days,
                experience_level=job_post.experience_level,
                role_count=1,
            )
            resolved_project_scope = calculation["recommended_project_scope"]
            logger(
                "JOB_POST",
                f"project_scope missing on create; auto-calculated as {resolved_project_scope}",
                "POST /job-posts",
                "INFO",
            )

        new_job_post = JobPostFunctions.create_job_post(
            client_id=client["client_id"],
            job_title=job_post.job_title,
            job_description=job_post.job_description,
            project_type=job_post.project_type,
            project_scope=resolved_project_scope,
            estimated_duration=job_post.estimated_duration,
            working_days=job_post.working_days,
            deadline=job_post.deadline,
            experience_level=job_post.experience_level,
            status=job_post.status,
            is_ai_generated=job_post.is_ai_generated
        )
        
        # Role embeddings are created when job roles are added via POST /job-roles.

        # Background: toxicity detection + scam detection
        _jp_id    = str(new_job_post["job_post_id"])
        _cl_id    = str(client["client_id"])
        _usr_id   = current_user.user_id
        _title    = job_post.job_title
        _desc     = job_post.job_description
        _scan_text = f"{_title} {_desc}"
        asyncio.create_task(asyncio.to_thread(queue_harmful_text_scan, "job_post", _jp_id, _usr_id, _scan_text))
        asyncio.create_task(asyncio.to_thread(
            queue_scam_scan, _jp_id, _cl_id, _scan_text, _title, _desc,
        ))

        success_msg = f"Created job post {job_post_id} for client {job_post.client_id}"
        logger("JOB_POST", success_msg, "POST /job-posts", "INFO")
        return ResponseSchema.success(new_job_post, 201)
    except ValueError as e:
        error_msg = f"Validation error: {str(e)}"
        logger("JOB_POST", error_msg, "POST /job-posts", "WARNING")
        return ResponseSchema.error(error_msg, 400)
    except Exception as e:
        error_msg = f"Failed to create job post: {str(e)}"
        logger("JOB_POST", error_msg, "POST /job-posts", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.put("/{job_post_id}", response_model=JobPostResponse)
async def update_job_post(job_post_id: str, job_post_update: JobPostUpdate, current_user: UserInDB = Depends(get_current_user)):
    """Update job post information - Authenticated users only."""
    try:
        existing_job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not existing_job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "PUT /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, existing_job_post["client_id"])
        
        update_data = job_post_update.model_dump(exclude_unset=True)
        updated_job_post = JobPostFunctions.update_job_post(job_post_id, update_data)
        
        mark_job_dirty(job_post_id)
        success_msg = f"Updated job post {job_post_id}"
        logger("JOB_POST", success_msg, "PUT /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success(updated_job_post, 200)
    except Exception as e:
        error_msg = f"Failed to update job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "PUT /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)


@job_post_router.delete("/{job_post_id}", status_code=200)
async def delete_job_post(job_post_id: str, current_user: UserInDB = Depends(get_current_user)):
    """Delete a job post - Authenticated users only."""
    try:
        existing_job_post = JobPostFunctions.get_job_post_by_id(job_post_id)
        if not existing_job_post:
            error_msg = f"Job post {job_post_id} not found"
            logger("JOB_POST", error_msg, "DELETE /job-posts/{job_post_id}", "WARNING")
            return ResponseSchema.error(error_msg, 404)
        assert_client_owns(current_user, existing_job_post["client_id"])
        
        JobPostFunctions.delete_job_post(job_post_id)

        success_msg = f"Deleted job post {job_post_id}"
        logger("JOB_POST", success_msg, "DELETE /job-posts/{job_post_id}", "INFO")
        return ResponseSchema.success("Deleted successfully", 200)
    except Exception as e:
        error_msg = f"Failed to delete job post {job_post_id}: {str(e)}"
        logger("JOB_POST", error_msg, "DELETE /job-posts/{job_post_id}", "ERROR")
        return ResponseSchema.error(error_msg, 500)
