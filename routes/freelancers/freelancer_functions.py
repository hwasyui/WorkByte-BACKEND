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
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        elif hasattr(value, 'isoformat'):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


class EmbeddingFunctions:
    """Helper functions for managing embeddings with pgvector."""

    @staticmethod
    def delete_freelancer_embedding(freelancer_id: str) -> bool:
        try:
            db = get_db()
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.delete_data(table_name="freelancer_embedding", conditions=conditions)
            logger("EMBEDDING_FUNCTIONS", f"Deleted freelancer embedding for {freelancer_id}", level="INFO")
            return True
        except Exception as e:
            logger("EMBEDDING_FUNCTIONS", f"Error deleting freelancer embedding: {str(e)}", level="ERROR")
            raise


class FreelancerFunctions:
    """Handle all freelancer-related database operations."""

    _FREELANCER_SORT_FIELDS = {
    "created_at":           "f.created_at",
    "updated_at":           "f.updated_at",
    "full_name":            "f.full_name",
    "estimated_rate":       "f.estimated_rate",
    "total_jobs":           "f.total_jobs",
    "weighted_review_avg":  "fts.weighted_review_avg",
    "total_reviews":        "fts.total_reviews",
}

    @staticmethod
    def browse_freelancers(
        order_by: str = "weighted_review_avg",
        order_dir: str = "desc",
        page: int = 1,
        page_size: int = 20,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            db = get_db()
            sort_col = FreelancerFunctions._FREELANCER_SORT_FIELDS.get(order_by, "fts.weighted_review_avg")
            direction = "DESC" if order_dir.lower() == "desc" else "ASC"
            offset = (page - 1) * page_size

            where: List[str] = []
            params: Dict[str, Any] = {}
            if created_from:
                where.append("f.created_at >= :created_from")
                params["created_from"] = created_from
            if created_to:
                where.append("f.created_at <= :created_to")
                params["created_to"] = created_to
            where_sql = ("WHERE " + " AND ".join(where)) if where else ""

            count_rows = db.execute_query(
                f"SELECT COUNT(*) AS total FROM freelancer f {where_sql}",
                params,
            )
            total = int(count_rows[0]["total"]) if count_rows else 0

            data_rows = db.execute_query(
                f"""
                SELECT f.freelancer_id, f.user_id, f.full_name, f.bio, f.cv_file_url,
                    f.profile_picture_url, f.estimated_rate, f.rate_time, f.rate_currency,
                    f.total_jobs, f.created_at, f.updated_at,
                    fts.weighted_review_avg, fts.total_reviews
                FROM freelancer f
                LEFT JOIN freelancer_trust_scores fts
                    ON fts.freelancer_id = f.freelancer_id
                {where_sql}
                ORDER BY {sort_col} {direction} NULLS LAST
                LIMIT :limit OFFSET :offset
                """,
                {**params, "limit": page_size, "offset": offset},
            )
            items = [convert_uuids_to_str(dict(row)) for row in data_rows]

            logger("FREELANCER_FUNCTIONS", f"browse_freelancers: {total} total, page {page}", level="INFO")
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
            logger("FREELANCER_FUNCTIONS", f"Error browsing freelancers: {str(e)}", level="ERROR")
            raise
        
    # dev function - no callers.
    @staticmethod
    def get_all_freelancers(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        try:
            db = get_db()
            limit_clause = f"LIMIT {limit}" if limit is not None else ""
            rows = db.execute_query(
                f"""
                SELECT f.freelancer_id, f.user_id, f.full_name, f.bio, f.cv_file_url,
                    f.profile_picture_url, f.estimated_rate, f.rate_time, f.rate_currency,
                    f.total_jobs, f.created_at, f.updated_at,
                    fts.weighted_review_avg, fts.total_reviews
                FROM freelancer f
                LEFT JOIN freelancer_trust_scores fts
                    ON fts.freelancer_id = f.freelancer_id
                ORDER BY fts.weighted_review_avg DESC NULLS LAST
                {limit_clause}
                OFFSET :offset
                """,
                {"offset": offset},
            )
            logger("FREELANCER_FUNCTIONS", f"Fetched {len(rows)} freelancers", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancers: {str(e)}", level="ERROR")
            raise
        
    @staticmethod
    def get_freelancer_by_id(freelancer_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer",
                conditions=[("freelancer_id", "=", freelancer_id)],
                limit=1
            )
            if rows:
                logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_by_user_id(user_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer",
                conditions=[("user_id", "=", user_id)],
                limit=1
            )
            if rows:
                logger("FREELANCER_FUNCTIONS", f"Freelancer for user {user_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer by user_id: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_by_id_or_user_id(identifier: str) -> Optional[Dict]:
        try:
            result = FreelancerFunctions.get_freelancer_by_id(identifier)
            if result:
                return result
            result = FreelancerFunctions.get_freelancer_by_user_id(identifier)
            if result:
                return result
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_freelancer(freelancer_id: str, user_id: str, full_name: str, title: Optional[str] = None,
                          bio: Optional[str] = None, cv_file_url: Optional[str] = None,
                          profile_picture_url: Optional[str] = None, estimated_rate: Optional[float] = None,
                          rate_time: str = "hourly", rate_currency: str = "USD",
                          create_embedding: bool = True) -> Dict:
        try:
            db = get_db()
            if not freelancer_id:
                freelancer_id = str(uuid.uuid4())
            freelancer_data = {
                "freelancer_id": freelancer_id,
                "user_id": user_id,
                "full_name": full_name,
                "title": title,
                "bio": bio,
                "cv_file_url": cv_file_url,
                "profile_picture_url": profile_picture_url,
                "estimated_rate": estimated_rate,
                "rate_time": rate_time,
                "rate_currency": rate_currency,
                "total_jobs": 0
            }
            db.insert_data(table_name="freelancer", data=freelancer_data)
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} created", level="INFO")
            return convert_uuids_to_str(freelancer_data)
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error creating freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_freelancer(freelancer_id: str, update_data: Dict, update_embedding: bool = True) -> Optional[Dict]:
        try:
            db = get_db()
            NULLABLE_FIELDS = {"profile_picture_url", "title", "bio", "cv_file_url", "estimated_rate", "rate_time", "rate_currency"}
            update_data = {
                k: v for k, v in update_data.items()
                if v is not None or k in NULLABLE_FIELDS
            }
            if not update_data:
                logger("FREELANCER_FUNCTIONS", "No data to update", level="WARNING")
                return FreelancerFunctions.get_freelancer_by_id(freelancer_id)
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.update_data(table_name="freelancer", data=update_data, conditions=conditions)
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} updated", level="INFO")
            return FreelancerFunctions.get_freelancer_by_id(freelancer_id)
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error updating freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_freelancer(freelancer_id: str, delete_embedding: bool = True) -> bool:
        try:
            db = get_db()
            if delete_embedding:
                EmbeddingFunctions.delete_freelancer_embedding(freelancer_id)
            conditions = [("freelancer_id", "=", freelancer_id)]
            db.delete_data(table_name="freelancer", conditions=conditions)
            logger("FREELANCER_FUNCTIONS", f"Freelancer {freelancer_id} deleted", level="INFO")
            return True
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error deleting freelancer: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_freelancers_by_name(search_term: str) -> List[Dict]:
        try:
            db = get_db()
            query = "SELECT * FROM freelancer WHERE full_name ILIKE '%' || :search_term || '%' ORDER BY created_at DESC"
            rows = db.execute_query(query, {"search_term": search_term})
            logger("FREELANCER_FUNCTIONS", f"Found {len(rows)} freelancers matching '{search_term}'", level="INFO")
            return [dict(row) for row in rows]
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error searching freelancers: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_embedding(freelancer_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_embedding",
                conditions=[("freelancer_id", "=", freelancer_id)],
                limit=1
            )
            if rows:
                return dict(rows[0])
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer embedding: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_payout_info(freelancer_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="freelancer_payout_info",
                conditions=[("freelancer_id", "=", freelancer_id)],
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching payout info: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def upsert_payout_info(freelancer_id: str, bank_name: str, account_number: str, account_holder_name: str) -> Dict:
        try:
            db = get_db()
            db.execute_query(
                """
                INSERT INTO freelancer_payout_info (freelancer_id, bank_name, account_number, account_holder_name)
                VALUES (:freelancer_id, :bank_name, :account_number, :account_holder_name)
                ON CONFLICT (freelancer_id) DO UPDATE SET
                    bank_name = EXCLUDED.bank_name,
                    account_number = EXCLUDED.account_number,
                    account_holder_name = EXCLUDED.account_holder_name
                """,
                {
                    "freelancer_id": freelancer_id,
                    "bank_name": bank_name,
                    "account_number": account_number,
                    "account_holder_name": account_holder_name,
                },
            )
            logger("FREELANCER_FUNCTIONS", f"Payout info upserted for freelancer {freelancer_id}", level="INFO")
            return FreelancerFunctions.get_payout_info(freelancer_id)
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error upserting payout info: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_freelancer_skills_with_names(freelancer_id: str) -> List[Dict]:
        try:
            db = get_db()
            query = """
                SELECT fs.freelancer_skill_id,
                       fs.freelancer_id,
                       fs.proficiency_level,
                       fs.created_at,
                       s.skill_id,
                       s.skill_name,
                       s.skill_category
                FROM freelancer_skill fs
                JOIN skill s ON fs.skill_id = s.skill_id
                WHERE fs.freelancer_id = :freelancer_id
                ORDER BY fs.created_at DESC
            """
            rows = db.execute_query(query, {"freelancer_id": freelancer_id})
            logger("FREELANCER_FUNCTIONS", f"Fetched skills for freelancer {freelancer_id}", level="INFO")
            return [dict(row) for row in rows] if rows else []
        except Exception as e:
            logger("FREELANCER_FUNCTIONS", f"Error fetching freelancer skills: {str(e)}", level="ERROR")
            raise

def get_comprehensive_freelancer_profile(freelancer_id: str) -> Optional[Dict]:
    """Get complete freelancer profile with all related data."""
    try:
        db = get_db()

        freelancer_rows = db.fetch_data(
            table_name="freelancer",
            conditions=[("freelancer_id", "=", freelancer_id)],
            limit=1
        )
        if not freelancer_rows:
            return None
        freelancer = dict(freelancer_rows[0])

        skills_query = """
            SELECT fs.freelancer_skill_id, fs.proficiency_level, fs.created_at,
                   s.skill_id, s.skill_name, s.skill_category,
                   s.created_at as skill_created_at
            FROM freelancer_skill fs
            JOIN skill s ON fs.skill_id = s.skill_id
            WHERE fs.freelancer_id = :freelancer_id
            ORDER BY fs.created_at DESC
        """
        skills_rows = db.execute_query(skills_query, {"freelancer_id": freelancer_id})
        skills = [dict(row) for row in skills_rows] if skills_rows else []

        education_rows = db.fetch_data(
            table_name="education",
            conditions=[("freelancer_id", "=", freelancer_id)],
            order_by="start_date DESC"
        )
        education = [dict(row) for row in education_rows] if education_rows else []

        work_experience_rows = db.fetch_data(
            table_name="work_experience",
            conditions=[("freelancer_id", "=", freelancer_id)],
            order_by="start_date DESC"
        )
        work_experience = [dict(row) for row in work_experience_rows] if work_experience_rows else []

        portfolio_rows = db.fetch_data(
            table_name="portfolio",
            conditions=[("freelancer_id", "=", freelancer_id)],
            order_by="created_at DESC"
        )
        portfolio = [dict(row) for row in portfolio_rows] if portfolio_rows else []

        # Ratings come from the review system and freelancer_trust_scores, both keyed
        # on freelancer.freelancer_id, so pass the profile id directly.
        from functions.review_views import public_reviews, public_trust_score
        from routes.reviews.review_functions import ReviewFunctions

        # public_reviews strips the moderation analysis: this profile is readable by
        # any authenticated user, so it must not carry authenticity/flag internals.
        reviews = public_reviews(
            ReviewFunctions.get_reviews_by_freelancer_id(str(freelancer_id))
            if freelancer_id else []
        )

        trust_rows = db.fetch_data(
            table_name="freelancer_trust_scores",
            conditions=[("freelancer_id", "=", freelancer_id)],
            limit=1,
        ) if freelancer_id else []
        stored_trust_score = dict(trust_rows[0]) if trust_rows else None
        # Same reasoning as public_reviews above, which this row was missing: the
        # profile is readable by any authenticated user, and the raw
        # freelancer_trust_scores row carries authenticity_confidence,
        # consistency_score and the two internal review averages - judgements about
        # the freelancer's reviewers, not things the freelancer earned. Sanitised
        # here rather than at the route so the totals below still read from the full
        # row.
        trust_score = public_trust_score(stored_trust_score)

        # total_ratings / average_rating are kept for the admin profile view. Prefer the
        # precomputed trust-score aggregate, then fall back to the published reviews.
        if stored_trust_score:
            total_ratings = stored_trust_score.get("total_reviews") or len(reviews)
            average_rating = stored_trust_score.get("display_star_avg")
        else:
            total_ratings = len(reviews)
            average_rating = None

        if average_rating is None and reviews:
            all_scores = [
                float(rt["score"])
                for rv in reviews
                for rt in rv.get("ratings", [])
                if rt.get("score") is not None
            ]
            average_rating = sum(all_scores) / len(all_scores) if all_scores else None

        return {
            "freelancer": freelancer,
            "skills": skills,
            "education": education,
            "work_experience": work_experience,
            "portfolio": portfolio,
            "reviews": reviews,
            "trust_score": trust_score,
            "total_ratings": total_ratings,
            "average_rating": average_rating
        }

    except Exception as e:
        logger("FREELANCER_FUNCTIONS", f"Error fetching comprehensive freelancer profile: {str(e)}", level="ERROR")
        raise