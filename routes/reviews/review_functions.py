import os
import sys
from datetime import datetime

from fastapi import HTTPException
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import Optional, List, Dict
import uuid


def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    return {
        k: str(v) if hasattr(v, "__class__") and "UUID" in v.__class__.__name__ else v
        for k, v in data.items()
    }


class ReviewFunctions:

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_review_by_id(review_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="reviews",
                conditions=[("id", "=", review_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching review {review_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_review_by_contract_id(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="reviews",
                conditions=[("contract_id", "=", contract_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching review for contract {contract_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_reviews_by_freelancer_id(freelancer_user_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="reviews",
                conditions=[
                    ("freelancer_id", "=", freelancer_user_id),
                    ("status", "=", "published"),
                ],
                order_by="created_at DESC",
            )
            logger("REVIEW_FUNCTIONS", f"Fetched {len(rows)} reviews for freelancer {freelancer_user_id}", level="INFO")
            return [convert_uuids_to_str(dict(r)) for r in rows]
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching reviews: {str(e)}", level="ERROR")
            raise

    # ── Step 2: Create pending review record ──────────────────────────────────

    @staticmethod
    def create_pending_review(
        contract_id: str,
        reviewer_id: str,       # client user_id
        freelancer_id: str,     # freelancer user_id
        inferred_category: str,
    ) -> Dict:
        """
        Called right after contract is marked complete.
        Creates the review shell with status=pending and the AI-inferred category.
        """
        try:
            db = get_db()
            review_id = str(uuid.uuid4())
            data = {
                "id": review_id,
                "contract_id": contract_id,
                "reviewer_id": reviewer_id,
                "freelancer_id": freelancer_id,
                "inferred_category": inferred_category,
                "status": "pending",
                "is_anonymous": False,
            }
            db.insert_data(table_name="reviews", data=data)
            logger("REVIEW_FUNCTIONS", f"Created pending review {review_id} for contract {contract_id}", level="INFO")
            return data
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error creating review: {str(e)}", level="ERROR")
            raise

    # ── Step 3: Save AI targeted question ────────────────────────────────────

    @staticmethod
    def save_ai_question(review_id: str, ai_question: str) -> None:
        """Insert the targeted question shell. client_answer is NULL until Step 5."""
        try:
            db = get_db()
            db.insert_data(
                table_name="review_written_content",
                data={
                    "id": str(uuid.uuid4()),
                    "review_id": review_id,
                    "ai_question": ai_question,
                },
            )
            logger("REVIEW_FUNCTIONS", f"Saved AI question for review {review_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error saving AI question: {str(e)}", level="ERROR")
            raise

    # ── Step 4: Save performance pre-scores ──────────────────────────────────

    @staticmethod
    def save_performance_scores(
        contract_id: str,
        freelancer_id: str,
        on_time_score: float,
        revision_count: int,
        revision_rate_score: float,
        responsiveness_score: float,
        communication_sentiment_score: Optional[float],
        conflict_score: Optional[float],
        communication_summary: Optional[str],
        work_quality_score: Optional[float] = None,
        work_quality_notes: Optional[str] = None,
    ) -> None:
        try:
            db = get_db()
            db.insert_data(
                table_name="freelancer_performance_scores",
                data={
                    "id": str(uuid.uuid4()),
                    "contract_id": contract_id,
                    "freelancer_id": freelancer_id,
                    "work_quality_score": work_quality_score,
                    "work_quality_notes": work_quality_notes,
                    "on_time_score": on_time_score,
                    "revision_count": revision_count,
                    "revision_rate_score": revision_rate_score,
                    "responsiveness_score": responsiveness_score,
                    "communication_sentiment_score": communication_sentiment_score,
                    "conflict_score": conflict_score,
                    "communication_summary": communication_summary,
                },
            )
            logger("REVIEW_FUNCTIONS", f"Saved performance scores for contract {contract_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error saving performance scores: {str(e)}", level="ERROR")
            raise

    # ── Step 5: Save client review submission ────────────────────────────────

    @staticmethod
    def save_client_review(
        review_id: str,
        ratings: List[Dict],            # [{"category": "quality", "score": 4.5}, ...]
        client_answer: str,
        overall_comment: str,
        confirmed_skill_tags: List[str],    # pre-filled from job_role_skill (AI-suggested)
        extra_skill_tags: List[str],        # manually added by client
    ) -> None:
        try:
            db = get_db()

            # 1. Save ratings
            for rating in ratings:
                db.insert_data(
                    table_name="review_ratings",
                    data={
                        "id": str(uuid.uuid4()),
                        "review_id": review_id,
                        "category": rating["category"],
                        "score": rating["score"],
                    },
                )

            # 2. Update written content (fill in client_answer + overall_comment)
            db.execute_query(
                """UPDATE review_written_content
                   SET client_answer = :answer, overall_comment = :comment
                   WHERE review_id = :rid""",
                {"answer": client_answer, "comment": overall_comment, "rid": review_id},
            )

            # 3. Save skill tags
            for tag in confirmed_skill_tags:
                db.insert_data(
                    table_name="review_skill_tags",
                    data={
                        "id": str(uuid.uuid4()),
                        "review_id": review_id,
                        "skill_tag": tag,
                        "is_ai_suggested": True,
                    },
                )
            for tag in extra_skill_tags:
                db.insert_data(
                    table_name="review_skill_tags",
                    data={
                        "id": str(uuid.uuid4()),
                        "review_id": review_id,
                        "skill_tag": tag,
                        "is_ai_suggested": False,
                    },
                )

            logger("REVIEW_FUNCTIONS", f"Saved client review for {review_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error saving client review: {str(e)}", level="ERROR")
            raise

    # ── Step 6: Save AI analysis results ─────────────────────────────────────

    @staticmethod
    def save_ai_analysis(
        review_id: str,
        sentiment_score: float,
        sentiment_label: str,
        sentiment_mismatch: bool,
        authenticity_score: float,
        is_flagged_fake: bool,
        is_flagged_coerced: bool,
        flag_reasons: List[str],
        bias_score: float,
        bias_flags: Dict,
        overall_pass: bool,
    ) -> None:
        try:
            db = get_db()
            db.insert_data(
                table_name="review_ai_analysis",
                data={
                    "id": str(uuid.uuid4()),
                    "review_id": review_id,
                    "sentiment_score": sentiment_score,
                    "sentiment_label": sentiment_label,
                    "sentiment_mismatch": sentiment_mismatch,
                    "authenticity_score": authenticity_score,
                    "is_flagged_fake": is_flagged_fake,
                    "is_flagged_coerced": is_flagged_coerced,
                    "flag_reasons": flag_reasons,
                    "bias_score": bias_score,
                    "bias_flags": bias_flags,
                    "overall_pass": overall_pass,
                },
            )
            logger("REVIEW_FUNCTIONS", f"Saved AI analysis for review {review_id}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error saving AI analysis: {str(e)}", level="ERROR")
            raise

    # ── Step 7: Publish or flag the review ───────────────────────────────────

    @staticmethod
    def publish_review(review_id: str) -> None:
        try:
            db = get_db()
            db.execute_query(
                "UPDATE reviews SET status = 'published', published_at = NOW() WHERE id = :rid",
                {"rid": review_id},
            )
            logger("REVIEW_FUNCTIONS", f"Review {review_id} published", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error publishing review: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def flag_review(review_id: str, suppress: bool = False) -> None:
        try:
            db = get_db()
            new_status = "suppressed" if suppress else "flagged"
            db.execute_query(
                "UPDATE reviews SET status = :status WHERE id = :rid",
                {"status": new_status, "rid": review_id},
            )
            logger("REVIEW_FUNCTIONS", f"Review {review_id} set to {new_status}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error flagging review: {str(e)}", level="ERROR")
            raise

    # ── Step 8: Trust score upsert ────────────────────────────────────────────

    @staticmethod
    def upsert_trust_score(
        freelancer_id: str,
        overall_score: float,
        weighted_review_avg: float,
        work_quality_score: Optional[float],
        revision_rate_score: float,
        responsiveness_score: float,
        communication_sentiment: Optional[float],
        total_reviews: int,
        category: Optional[str],
        category_rank_pct: Optional[float],
    ) -> None:
        try:
            db = get_db()
            existing = db.fetch_data(
                table_name="freelancer_trust_scores",
                conditions=[("freelancer_id", "=", freelancer_id)],
                limit=1,
            )
            data = {
                "freelancer_id": freelancer_id,
                "overall_score": overall_score,
                "weighted_review_avg": weighted_review_avg,
                "work_quality_score": work_quality_score,
                "revision_rate_score": revision_rate_score,
                "responsiveness_score": responsiveness_score,
                "communication_sentiment": communication_sentiment,
                "total_reviews": total_reviews,
                "category": category,
                "category_rank_pct": category_rank_pct,
                "last_updated": datetime.utcnow(),
            }
            if existing:
                db.update_data(
                    table_name="freelancer_trust_scores",
                    data=data,
                    conditions=[("freelancer_id", "=", freelancer_id)],
                )
            else:
                data["id"] = str(uuid.uuid4())
                db.insert_data(table_name="freelancer_trust_scores", data=data)

            # Snapshot
            db.insert_data(
                table_name="trust_score_history",
                data={
                    "id": str(uuid.uuid4()),
                    "freelancer_id": freelancer_id,
                    "overall_score": overall_score,
                    "snapshot_reason": "review_published",
                },
            )
            logger("REVIEW_FUNCTIONS", f"Trust score upserted for freelancer {freelancer_id}: {overall_score}", level="INFO")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error upserting trust score: {str(e)}", level="ERROR")
            raise

    # ── Step 9: Red flag detection ────────────────────────────────────────────

    @staticmethod
    def check_and_create_red_flag(freelancer_id: str, new_score: float) -> None:
        """Compare latest 2 snapshots. Fire alert if drop > 10 points."""
        try:
            db = get_db()
            snapshots = db.fetch_data(
                table_name="trust_score_history",
                conditions=[("freelancer_id", "=", freelancer_id)],
                order_by="recorded_at DESC",
                limit=2,
            )
            if len(snapshots) < 2:
                return

            previous_score = float(snapshots[1]["overall_score"])
            drop = previous_score - new_score
            if drop <= 10:
                return

            severity = "high" if drop > 20 else "medium" if drop > 15 else "low"
            message = (
                f"Trust score dropped by {drop:.1f} points "
                f"(from {previous_score:.1f} to {new_score:.1f}). "
                f"Recent performance may have declined."
            )
            db.insert_data(
                table_name="red_flag_alerts",
                data={
                    "id": str(uuid.uuid4()),
                    "freelancer_id": freelancer_id,
                    "alert_type": "score_drop",
                    "severity": severity,
                    "message": message,
                    "is_resolved": False,
                },
            )
            logger("REVIEW_FUNCTIONS", f"Red flag created for freelancer {freelancer_id} (drop: {drop:.1f})", level="WARNING")
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error checking red flag: {str(e)}", level="ERROR")
            raise

    # ── Getters ───────────────────────────────────────────────────────────────

    @staticmethod
    def get_review_detail(review_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            review = ReviewFunctions.get_review_by_id(review_id)
            if not review:
                raise HTTPException(status_code=404, detail="Review not found")
            
            ratings  = db.fetch_data("review_ratings",       conditions=[("review_id", "=", review_id)])
            written  = db.fetch_data("review_written_content", conditions=[("review_id", "=", review_id)], limit=1)
            tags     = db.fetch_data("review_skill_tags",    conditions=[("review_id", "=", review_id)])
            analysis = db.fetch_data("review_ai_analysis",   conditions=[("review_id", "=", review_id)], limit=1)

            review["ratings"]       = [convert_uuids_to_str(dict(r)) for r in ratings]
            review["written_content"] = convert_uuids_to_str(dict(written[0])) if written else None
            review["skill_tags"]    = [convert_uuids_to_str(dict(t)) for t in tags]
            review["ai_analysis"]   = convert_uuids_to_str(dict(analysis[0])) if analysis else None
            return review
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching review detail: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_trust_score(freelancer_user_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                "freelancer_trust_scores",
                conditions=[("freelancer_id", "=", freelancer_user_id)],
                limit=1,
            )
            return convert_uuids_to_str(dict(rows[0])) if rows else None
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching trust score: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_red_flags(freelancer_user_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                "red_flag_alerts",
                conditions=[("freelancer_id", "=", freelancer_user_id), ("is_resolved", "=", False)],
                order_by="triggered_at DESC",
            )
            return [convert_uuids_to_str(dict(r)) for r in rows]
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching red flags: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_suggested_skill_tags(contract_id: str) -> List[str]:
        """Fetch skill names from job_role_skill for the contract's job_role_id."""
        try:
            db = get_db()
            rows = db.execute_query(
                """SELECT s.skill_name FROM job_role_skill jrs
                   JOIN skill s ON s.skill_id = jrs.skill_id
                   JOIN contract c ON c.job_role_id = jrs.job_role_id
                   WHERE c.contract_id = :cid""",
                {"cid": contract_id},
            )
            return [row["skill_name"] for row in rows] if rows else []
        except Exception as e:
            logger("REVIEW_FUNCTIONS", f"Error fetching skill tags: {str(e)}", level="ERROR")
            raise