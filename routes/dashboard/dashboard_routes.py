import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, Depends, Query
from typing import Optional
from functions.authentication import get_current_user
from functions.schema_model import UserInDB
from functions.logger import logger
from functions.response_utils import ResponseSchema
from routes.dashboard.dashboard_functions import DashboardFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.clients.client_functions import ClientFunctions

dashboard_router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_FREELANCER_STATUSES = {
    "applied", "hired", "in_progress", "work_submitted",
    "revision_requested", "completed", "rejected", "withdrawn", "cancelled", "disputed",
}

_CLIENT_STATUSES = {
    "draft", "open", "hiring", "in_progress", "work_submitted",
    "revision_requested", "completed", "disputed",
}

_FREELANCER_ORDER_FIELDS = {
    "last_activity_date", "submitted_at", "start_date", "end_date",
    "actual_completion_date", "job_title", "proposed_budget", "agreed_budget",
}

_CLIENT_ORDER_FIELDS = {
    "last_activity_date", "created_at", "posted_at", "deadline", "job_title",
}


@dashboard_router.get("/freelancer")
async def get_freelancer_dashboard(
    tracking_status: Optional[str] = Query(
        default=None,
        description=f"Filter by status. One of: {', '.join(sorted(_FREELANCER_STATUSES))}",
    ),
    order_by: str = Query(
        default="last_activity_date",
        description=f"Sort field. One of: {', '.join(sorted(_FREELANCER_ORDER_FIELDS))}",
    ),
    order_dir: str = Query(
        default="desc",
        description="Sort direction: asc or desc",
        pattern="^(asc|desc)$",
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Freelancer dashboard — every job applied to, with unified tracking_status.

    **tracking_status values**
    | Value | Meaning |
    |---|---|
    | applied | Proposal sent, waiting for response |
    | hired | Proposal accepted, contract not yet started |
    | in_progress | Contract active, work ongoing |
    | work_submitted | Work submitted, awaiting client review |
    | revision_requested | Client asked for changes |
    | completed | Contract done and approved |
    | rejected | Proposal was rejected |
    | withdrawn | Freelancer withdrew the proposal |
    | cancelled | Contract was cancelled |
    | disputed | Contract under dispute |

    **Dates on each item**
    - `submitted_at` — when the proposal was sent
    - `start_date` — contract start date
    - `end_date` — expected contract end date
    - `actual_completion_date` — when work was accepted
    - `last_activity_date` — most recent of the above (default sort field)
    """
    try:
        if tracking_status and tracking_status not in _FREELANCER_STATUSES:
            return ResponseSchema.error(
                f"Invalid tracking_status '{tracking_status}'. "
                f"Valid values: {', '.join(sorted(_FREELANCER_STATUSES))}",
                400,
            )
        if order_by not in _FREELANCER_ORDER_FIELDS:
            return ResponseSchema.error(
                f"Invalid order_by '{order_by}'. "
                f"Valid values: {', '.join(sorted(_FREELANCER_ORDER_FIELDS))}",
                400,
            )

        freelancer = FreelancerFunctions.get_freelancer_by_user_id(current_user.user_id)
        if not freelancer:
            return ResponseSchema.error("Freelancer profile not found", 404)

        data = DashboardFunctions.get_freelancer_dashboard(
            freelancer_id=freelancer["freelancer_id"],
            tracking_status=tracking_status,
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            page_size=page_size,
        )
        logger("DASHBOARD", f"Freelancer dashboard fetched for {freelancer['freelancer_id']}", level="INFO")
        return ResponseSchema.success(data, 200)
    except Exception as e:
        logger("DASHBOARD", f"Error fetching freelancer dashboard: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"Failed to fetch dashboard: {str(e)}", 500)


@dashboard_router.get("/client")
async def get_client_dashboard(
    tracking_status: Optional[str] = Query(
        default=None,
        description=f"Filter by job tracking status. One of: {', '.join(sorted(_CLIENT_STATUSES))}",
    ),
    order_by: str = Query(
        default="last_activity_date",
        description=f"Sort field. One of: {', '.join(sorted(_CLIENT_ORDER_FIELDS))}",
    ),
    order_dir: str = Query(
        default="desc",
        description="Sort direction: asc or desc",
        pattern="^(asc|desc)$",
    ),
    page: int = Query(default=1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page (max 100)"),
    current_user: UserInDB = Depends(get_current_user),
):
    """
    Client dashboard — every job post, with roles and contracts nested inside.

    **Job tracking_status values**
    | Value | Meaning |
    |---|---|
    | draft | Job not yet published |
    | open | Published, accepting proposals, no contracts yet |
    | hiring | Some positions filled, others still open |
    | in_progress | All positions filled, work ongoing |
    | work_submitted | A freelancer submitted work, awaiting review |
    | revision_requested | Client asked for revisions |
    | completed | All roles completed |
    | disputed | A contract is under dispute |

    **Dates on each job item**
    - `created_at` — when the job post was created
    - `posted_at` — when it went live (published)
    - `deadline` — application deadline
    - `last_activity_date` — most recent contract start across all roles (default sort field)

    **Role tracking_status** follows the same logic per individual role.
    **Contract tracking_status** maps directly from contract status.
    """
    try:
        if tracking_status and tracking_status not in _CLIENT_STATUSES:
            return ResponseSchema.error(
                f"Invalid tracking_status '{tracking_status}'. "
                f"Valid values: {', '.join(sorted(_CLIENT_STATUSES))}",
                400,
            )
        if order_by not in _CLIENT_ORDER_FIELDS:
            return ResponseSchema.error(
                f"Invalid order_by '{order_by}'. "
                f"Valid values: {', '.join(sorted(_CLIENT_ORDER_FIELDS))}",
                400,
            )

        client = ClientFunctions.get_client_by_user_id(current_user.user_id)
        if not client:
            return ResponseSchema.error("Client profile not found", 404)

        data = DashboardFunctions.get_client_dashboard(
            client_id=client["client_id"],
            tracking_status=tracking_status,
            order_by=order_by,
            order_dir=order_dir,
            page=page,
            page_size=page_size,
        )
        logger("DASHBOARD", f"Client dashboard fetched for {client['client_id']}", level="INFO")
        return ResponseSchema.success(data, 200)
    except Exception as e:
        logger("DASHBOARD", f"Error fetching client dashboard: {str(e)}", level="ERROR")
        return ResponseSchema.error(f"Failed to fetch dashboard: {str(e)}", 500)
