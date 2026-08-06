import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict, Any
import math
import uuid
from datetime import datetime

def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID and datetime objects in dict to strings."""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        elif hasattr(value, 'isoformat'):  # catches datetime and date objects
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result

class ClientFunctions:
    """Handle all client-related database operations."""

    # Count published job posts live instead of reading client.total_jobs_posted,
    # which drifts and counts drafts as posted.
    _NON_DRAFT_JOBS_POSTED_SQL = (
        "(SELECT COUNT(*) FROM job_post jp WHERE jp.client_id = c.client_id "
        "AND jp.status <> 'draft')"
    )

    # ORDER BY still ranks on weighted_review_avg_received even though the SELECT no
    # longer returns it - Postgres will sort on a joined column that is not in the
    # select list. That split is deliberate: the weighted figure is the better
    # RANKING signal, because authenticity and repeat-pair decay stop manufactured
    # reviews from lifting a client up the list, but it is the wrong number to SHOW
    # (see review_views._MODERATION_ONLY_TRUST_FIELDS). The sort key keeps its
    # public name so the documented order_by values do not change.
    _CLIENT_SORT_FIELDS = {
        "created_at":                   "c.created_at",
        "updated_at":                   "c.updated_at",
        "full_name":                    "c.full_name",
        "total_jobs_posted":            _NON_DRAFT_JOBS_POSTED_SQL,
        "total_jobs_completed":         "c.total_jobs_completed",
        "weighted_review_avg_received": "cts.weighted_review_avg_received",
        "display_star_avg":             "cts.display_star_avg",
        "total_reviews_received":       "cts.total_reviews_received",
    }

    @staticmethod
    def browse_clients(
        order_by: str = "created_at",
        order_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated + sorted client browse, optionally filtered by created_at range."""
        try:
            db = get_db()

            sort_col = ClientFunctions._CLIENT_SORT_FIELDS.get(order_by, "c.created_at")
            direction = "DESC" if order_dir.lower() == "desc" else "ASC"
            offset = (page - 1) * page_size

            where: List[str] = []
            params: Dict[str, Any] = {}
            if created_from:
                where.append("c.created_at >= :created_from")
                params["created_from"] = created_from
            if created_to:
                where.append("c.created_at <= :created_to")
                params["created_to"] = created_to
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            count_rows = db.execute_query(
                f"SELECT COUNT(*) AS total FROM client c {where_sql}",
                params,
            )
            total = int(count_rows[0]["total"]) if count_rows else 0

            data_rows = db.execute_query(
                f"""
                SELECT c.client_id, c.user_id, c.full_name, c.bio, c.website_url, c.profile_picture_url,
                       {ClientFunctions._NON_DRAFT_JOBS_POSTED_SQL} AS total_jobs_posted,
                       c.total_jobs_completed, c.average_rating_given,
                       c.created_at, c.updated_at,
                       cts.display_star_avg, cts.total_reviews_received
                FROM client c
                LEFT JOIN client_trust_score cts
                    ON cts.client_id = c.client_id
                {where_sql}
                ORDER BY {sort_col} {direction} NULLS LAST
                LIMIT :limit OFFSET :offset
                """,
                {**params, "limit": page_size, "offset": offset},
            )
            items = [convert_uuids_to_str(dict(row)) for row in data_rows]

            logger("CLIENT_FUNCTIONS", f"browse_clients: {total} total, page {page}", level="INFO")
            return {
                "items": items,
                "pagination": {
                    "page":        page,
                    "page_size":   page_size,
                    "total":       total,
                    "total_pages": math.ceil(total / page_size) if total else 0,
                },
            }
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error browsing clients: {str(e)}", level="ERROR")
            raise

    # dev function - no callers.
    @staticmethod
    def get_all_clients(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all clients."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client",
                columns=["client_id", "user_id", "full_name", "bio", "website_url", "profile_picture_url",
                        "total_jobs_posted", "total_jobs_completed",
                        "average_rating_given", "contract_message_template", "created_at", "updated_at"],
                order_by="created_at DESC",
                limit=limit
            )

            logger("CLIENT_FUNCTIONS", f"Fetched {len(rows)} clients", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching clients: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _count_non_draft_jobs_posted(client_id: str) -> int:
        """Real, always-accurate jobs-posted count - see _NON_DRAFT_JOBS_POSTED_SQL."""
        db = get_db()
        rows = db.execute_query(
            "SELECT COUNT(*) AS total FROM job_post WHERE client_id = :client_id AND status <> 'draft'",
            {"client_id": client_id},
        )
        return int(rows[0]["total"]) if rows else 0

    @staticmethod
    def get_client_by_id(client_id: str) -> Optional[Dict]:
        """Fetch a single client by ID."""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            rows = db.fetch_data(
                table_name="client",
                conditions=conditions,
                limit=1
            )

            if rows:
                logger("CLIENT_FUNCTIONS", f"Client {client_id} found", level="INFO")
                client = convert_uuids_to_str(dict(rows[0]))
                client["total_jobs_posted"] = ClientFunctions._count_non_draft_jobs_posted(client_id)
                return client

            return None

        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_by_user_id(user_id: str) -> Optional[Dict]:
        """Fetch a client by user ID."""
        try:
            db = get_db()
            conditions = [("user_id", "=", user_id)]
            rows = db.fetch_data(
                table_name="client",
                conditions=conditions,
                limit=1
            )

            if rows:
                logger("CLIENT_FUNCTIONS", f"Client for user {user_id} found", level="INFO")
                client = convert_uuids_to_str(dict(rows[0]))
                client["total_jobs_posted"] = ClientFunctions._count_non_draft_jobs_posted(
                    client["client_id"]
                )
                return client

            return None

        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client by user_id: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_by_id_or_user_id(identifier: str) -> Optional[Dict]:
        """Fetch a client by either client_id or user_id."""
        try:
            # Try client_id first
            result = ClientFunctions.get_client_by_id(identifier)
            if result:
                return result
            
            # Try user_id as fallback
            result = ClientFunctions.get_client_by_user_id(identifier)
            if result:
                return result
            
            return None
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error fetching client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_client(client_id: str, user_id: str, full_name: Optional[str] = None,
                     bio: Optional[str] = None, website_url: Optional[str] = None,
                     profile_picture_url: Optional[str] = None) -> Dict:
        """Create a new client profile."""
        try:
            db = get_db()
            if not client_id:
                client_id = str(uuid.uuid4())

            client_data = {
                "client_id": client_id,
                "user_id": user_id,
                "full_name": full_name,
                "bio": bio,
                "website_url": website_url,
                "profile_picture_url": profile_picture_url,
                "total_jobs_posted": 0,
                "total_jobs_completed": 0
            }
            
            db.insert_data(table_name="client", data=client_data)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} created", level="INFO")
            return convert_uuids_to_str(client_data)
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error creating client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_client(client_id: str, update_data: Dict) -> Optional[Dict]:
        """Update client information."""
        try:
            db = get_db()
            # Remove None values, except for fields that are explicitly nullable
            NULLABLE_FIELDS = {"profile_picture_url", "bio", "website_url", "contract_message_template"}
            update_data = {
                k: v for k, v in update_data.items()
                if v is not None or k in NULLABLE_FIELDS
            }

            if not update_data:
                logger("CLIENT_FUNCTIONS", "No data to update", level="WARNING")
                return ClientFunctions.get_client_by_id(client_id)
            
            conditions = [("client_id", "=", client_id)]
            db.update_data(table_name="client", data=update_data, conditions=conditions)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} updated", level="INFO")
            return ClientFunctions.get_client_by_id(client_id)
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error updating client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_client(client_id: str) -> bool:
        """Delete a client profile."""
        try:
            db = get_db()
            conditions = [("client_id", "=", client_id)]
            db.delete_data(table_name="client", conditions=conditions)
            
            logger("CLIENT_FUNCTIONS", f"Client {client_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error deleting client: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_clients_by_full_name(search_term: str) -> List[Dict]:
        """Search clients by full name."""
        try:
            db = get_db()
            query = "SELECT * FROM client WHERE full_name ILIKE '%' || :search_term || '%' ORDER BY created_at DESC"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("CLIENT_FUNCTIONS", f"Found {len(rows)} clients matching '{search_term}'", level="INFO")
            return [dict(row) for row in rows]
        
        except Exception as e:
            logger("CLIENT_FUNCTIONS", f"Error searching clients: {str(e)}", level="ERROR")
            raise
