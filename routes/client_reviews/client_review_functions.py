import os
import sys
import uuid
from typing import Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import HTTPException
from functions.db_manager import get_db
from functions.logger import logger


def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    return {
        k: str(v) if hasattr(v, "__class__") and "UUID" in v.__class__.__name__ else v
        for k, v in data.items()
    }


class ClientReviewFunctions:
    """
    Freelancer-reviews-client system - the symmetric counterpart to
    ReviewFunctions (routes/reviews/review_functions.py). Mirrors its
    structure/conventions; see client_review_pipeline.py for the
    orchestration this backs.
    """

    # Helpers

    @staticmethod
    def get_client_review_by_id(client_review_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client_reviews",
                conditions=[("id", "=", client_review_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client review {client_review_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_review_by_contract_id(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client_reviews",
                conditions=[("contract_id", "=", contract_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client review for contract {contract_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_reviews_by_client_id(client_user_id: str) -> List[Dict]:
        """All published reviews FOR a client, written BY freelancers they worked with."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client_reviews",
                conditions=[
                    ("client_id", "=", client_user_id),
                    ("status", "=", "published"),
                ],
                order_by="created_at DESC",
            )

            reviews = []
            for row in rows:
                review = convert_uuids_to_str(dict(row))

                ratings_rows = db.fetch_data(
                    table_name="client_review_ratings",
                    conditions=[("client_review_id", "=", review["id"])],
                )
                written_rows = db.fetch_data(
                    table_name="client_review_written_content",
                    conditions=[("client_review_id", "=", review["id"])],
                    limit=1,
                )
                analysis_rows = db.fetch_data(
                    table_name="client_review_ai_analysis",
                    conditions=[("client_review_id", "=", review["id"])],
                    limit=1,
                )

                review["ratings"] = [convert_uuids_to_str(dict(r)) for r in ratings_rows]
                review["written_content"] = (
                    convert_uuids_to_str(dict(written_rows[0])) if written_rows else None
                )
                review["ai_analysis"] = (
                    convert_uuids_to_str(dict(analysis_rows[0])) if analysis_rows else None
                )
                reviews.append(review)

            logger(
                "CLIENT_REVIEW_FUNCTIONS",
                f"Fetched {len(reviews)} reviews for client {client_user_id}",
                level="INFO",
            )
            return reviews
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client reviews: {str(e)}", level="ERROR")
            raise

    # Create pending review shell (post-completion)

    @staticmethod
    def create_pending_client_review(
        contract_id: str,
        reviewer_id: str,
        client_id: str,
    ) -> Dict:
        try:
            db = get_db()
            review_id = str(uuid.uuid4())
            data = {
                "id": review_id,
                "contract_id": contract_id,
                "reviewer_id": reviewer_id,
                "client_id": client_id,
                "status": "pending",
                "is_anonymous": False,
            }
            db.insert_data(table_name="client_reviews", data=data)
            logger("CLIENT_REVIEW_FUNCTIONS", f"Created pending client review {review_id} for contract {contract_id}", level="INFO")
            return data
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error creating client review: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def save_ai_question(client_review_id: str, ai_question: str) -> None:
        try:
            db = get_db()
            db.insert_data(
                table_name="client_review_written_content",
                data={
                    "id": str(uuid.uuid4()),
                    "client_review_id": client_review_id,
                    "ai_question": ai_question,
                },
            )
            logger("CLIENT_REVIEW_FUNCTIONS", f"Saved AI question for client review {client_review_id}", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error saving AI question: {str(e)}", level="ERROR")
            raise

    # Submit (freelancer fills out the form)

    @staticmethod
    def save_freelancer_review(
        client_review_id: str,
        ratings: List[Dict],
        freelancer_answer: str,
        overall_comment: str,
    ) -> None:
        try:
            db = get_db()
            for rating in ratings:
                db.insert_data(
                    table_name="client_review_ratings",
                    data={
                        "id": str(uuid.uuid4()),
                        "client_review_id": client_review_id,
                        "category": rating["category"],
                        "score": rating["score"],
                    },
                )

            db.execute_query(
                """UPDATE client_review_written_content
                   SET freelancer_answer = :answer, overall_comment = :comment
                   WHERE client_review_id = :crid""",
                {"answer": freelancer_answer, "comment": overall_comment, "crid": client_review_id},
            )

            logger("CLIENT_REVIEW_FUNCTIONS", f"Saved freelancer review for {client_review_id}", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error saving freelancer review: {str(e)}", level="ERROR")
            raise

    # AI analysis

    @staticmethod
    def save_ai_analysis(
        client_review_id: str,
        sentiment_score: float,
        sentiment_label: str,
        sentiment_mismatch: bool,
        authenticity_score: float,
        is_flagged_fake: bool,
        is_flagged_coerced: bool,
        flag_reasons: List[str],
        overall_pass: bool,
        mismatch_severity: Optional[float] = None,
    ) -> None:
        try:
            db = get_db()
            db.insert_data(
                table_name="client_review_ai_analysis",
                data={
                    "id": str(uuid.uuid4()),
                    "client_review_id": client_review_id,
                    "sentiment_score": sentiment_score,
                    "sentiment_label": sentiment_label,
                    "sentiment_mismatch": sentiment_mismatch,
                    "mismatch_severity": mismatch_severity,
                    "authenticity_score": authenticity_score,
                    "is_flagged_fake": is_flagged_fake,
                    "is_flagged_coerced": is_flagged_coerced,
                    "flag_reasons": flag_reasons,
                    "overall_pass": overall_pass,
                },
            )
            logger("CLIENT_REVIEW_FUNCTIONS", f"Saved AI analysis for client review {client_review_id}", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error saving AI analysis: {str(e)}", level="ERROR")
            raise

    # Publish / flag

    @staticmethod
    def publish_review(client_review_id: str) -> None:
        try:
            db = get_db()
            db.execute_query(
                "UPDATE client_reviews SET status = 'published', published_at = NOW() WHERE id = :rid",
                {"rid": client_review_id},
            )
            logger("CLIENT_REVIEW_FUNCTIONS", f"Client review {client_review_id} published", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error publishing client review: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def flag_review(client_review_id: str, suppress: bool = False) -> None:
        try:
            db = get_db()
            new_status = "suppressed" if suppress else "flagged"
            db.execute_query(
                "UPDATE client_reviews SET status = :status WHERE id = :rid",
                {"status": new_status, "rid": client_review_id},
            )
            logger("CLIENT_REVIEW_FUNCTIONS", f"Client review {client_review_id} set to {new_status}", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error flagging client review: {str(e)}", level="ERROR")
            raise

    # Trust score

    @staticmethod
    def upsert_client_trust_score(
        client_id: str,
        trust_score: float,
        weighted_review_avg_received: float,
        responsiveness_score: float,
        communication_sentiment: Optional[float],
        authenticity_confidence: float,
        consistency_score: float,
        dispute_fairness_score: float,
        total_reviews_received: int,
        ai_review_summary: Optional[str] = None,
    ) -> None:
        try:
            db = get_db()
            existing = db.fetch_data(
                table_name="client_trust_score",
                conditions=[("client_id", "=", client_id)],
                limit=1,
            )
            data = {
                "client_id": client_id,
                "trust_score": trust_score,
                "weighted_review_avg_received": weighted_review_avg_received,
                "responsiveness_score": responsiveness_score,
                "communication_sentiment": communication_sentiment,
                "authenticity_confidence": authenticity_confidence,
                "consistency_score": consistency_score,
                "dispute_fairness_score": dispute_fairness_score,
                "total_reviews_received": total_reviews_received,
            }
            # Only touch this column when a fresh summary was actually generated this run
            # (see SUMMARY_REGEN_INTERVAL) - otherwise this upsert would null out the
            # previously cached summary on every review that doesn't regenerate it.
            if ai_review_summary is not None:
                data["ai_review_summary"] = ai_review_summary
            if existing:
                db.update_data(
                    table_name="client_trust_score",
                    data=data,
                    conditions=[("client_id", "=", client_id)],
                )
            else:
                data["client_trust_score_id"] = str(uuid.uuid4())
                db.insert_data(table_name="client_trust_score", data=data)
            logger("CLIENT_REVIEW_FUNCTIONS", f"Trust score upserted for client {client_id}: {trust_score}", level="INFO")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error upserting client trust score: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_client_trust_score(client_user_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="client_trust_score",
                conditions=[("client_id", "=", client_user_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client trust score: {str(e)}", level="ERROR")
            raise

    # Red flags (shared red_flag_alerts table, subject_type='client')

    @staticmethod
    def check_and_create_red_flag(client_id: str, new_score: float) -> None:
        """Compare latest 2 snapshots for this client. Fire alert if drop > 10 points.
        Reuses red_flag_alerts (subject_type='client') rather than a separate table -
        no history-snapshot table exists for clients, so this compares against the
        previous trust_score value directly instead of a trust_score_history row."""
        try:
            db = get_db()
            existing = ClientReviewFunctions.get_client_trust_score(client_id)
            if not existing or existing.get("trust_score") is None:
                return

            previous_score = float(existing["trust_score"])
            drop = previous_score - new_score
            if drop <= 10:
                return

            severity = "high" if drop > 20 else "medium" if drop > 15 else "low"
            message = (
                f"Trust score dropped by {drop:.1f} points "
                f"(from {previous_score:.1f} to {new_score:.1f}). "
                f"Recent reviews may indicate a declining pattern."
            )
            db.insert_data(
                table_name="red_flag_alerts",
                data={
                    "id": str(uuid.uuid4()),
                    "freelancer_id": client_id,  # column name predates client support - see alter_table.sql note
                    "subject_type": "client",
                    "alert_type": "score_drop",
                    "severity": severity,
                    "message": message,
                    "is_resolved": False,
                },
            )
            logger("CLIENT_REVIEW_FUNCTIONS", f"Red flag created for client {client_id} (drop: {drop:.1f})", level="WARNING")
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error checking client red flag: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_red_flags(client_user_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                "red_flag_alerts",
                conditions=[
                    ("freelancer_id", "=", client_user_id),  # column name predates client support - see alter_table.sql note
                    ("subject_type", "=", "client"),
                    ("is_resolved", "=", False),
                ],
                order_by="triggered_at DESC",
            )
            return [convert_uuids_to_str(dict(r)) for r in rows]
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client red flags: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_review_detail(client_review_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            review = ClientReviewFunctions.get_client_review_by_id(client_review_id)
            if not review:
                raise HTTPException(status_code=404, detail="Client review not found")

            ratings = db.fetch_data("client_review_ratings", conditions=[("client_review_id", "=", client_review_id)])
            written = db.fetch_data("client_review_written_content", conditions=[("client_review_id", "=", client_review_id)], limit=1)
            analysis = db.fetch_data("client_review_ai_analysis", conditions=[("client_review_id", "=", client_review_id)], limit=1)

            review["ratings"] = [convert_uuids_to_str(dict(r)) for r in ratings]
            review["written_content"] = convert_uuids_to_str(dict(written[0])) if written else None
            review["ai_analysis"] = convert_uuids_to_str(dict(analysis[0])) if analysis else None
            return review
        except Exception as e:
            logger("CLIENT_REVIEW_FUNCTIONS", f"Error fetching client review detail: {str(e)}", level="ERROR")
            raise
