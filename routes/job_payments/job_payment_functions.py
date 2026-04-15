import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid


def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    return {
        k: str(v) if hasattr(v, '__class__') and 'UUID' in v.__class__.__name__ else v
        for k, v in data.items()
    }


# ─────────────────────────────────────────────────────────────────────────────
# JOB PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

class JobPaymentFunctions:

    @staticmethod
    def create_job_payment(job_post_id: str, payment_type: str, payment_option: str) -> Dict:
        """
        Create a job payment record when a client posts a job.
        payment_type: 'full' or 'milestone'
        payment_option: e.g. '1 milestone', '2 milestones', 'full'
        """
        try:
            db = get_db()
            job_payment_id = str(uuid.uuid4())

            data = {
                "job_payment_id": job_payment_id,
                "job_post_id": job_post_id,
                "payment_type": payment_type,
                "payment_option": payment_option,
                "status": "pending",
            }

            db.insert_data(table_name="job_payment", data=data)
            logger("JOB_PAYMENT_FUNCTIONS", f"job_payment {job_payment_id} created for job_post {job_post_id}", level="INFO")
            return convert_uuids_to_str(data)

        except Exception as e:
            logger("JOB_PAYMENT_FUNCTIONS", f"Error creating job_payment: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_payment_by_id(job_payment_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_payment",
                conditions=[("job_payment_id", "=", job_payment_id)],
                limit=1
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("JOB_PAYMENT_FUNCTIONS", f"Error fetching job_payment {job_payment_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_payment_by_job_post_id(job_post_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_payment",
                conditions=[("job_post_id", "=", job_post_id)],
                limit=1
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("JOB_PAYMENT_FUNCTIONS", f"Error fetching job_payment for job_post {job_post_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_payment(job_payment_id: str, update_data: Dict) -> Optional[Dict]:
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                return JobPaymentFunctions.get_job_payment_by_id(job_payment_id)

            db.update_data(
                table_name="job_payment",
                data=update_data,
                conditions=[("job_payment_id", "=", job_payment_id)]
            )
            logger("JOB_PAYMENT_FUNCTIONS", f"job_payment {job_payment_id} updated", level="INFO")
            return JobPaymentFunctions.get_job_payment_by_id(job_payment_id)
        except Exception as e:
            logger("JOB_PAYMENT_FUNCTIONS", f"Error updating job_payment {job_payment_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_payment(job_payment_id: str) -> bool:
        try:
            db = get_db()
            db.delete_data(
                table_name="job_payment",
                conditions=[("job_payment_id", "=", job_payment_id)]
            )
            logger("JOB_PAYMENT_FUNCTIONS", f"job_payment {job_payment_id} deleted", level="INFO")
            return True
        except Exception as e:
            logger("JOB_PAYMENT_FUNCTIONS", f"Error deleting job_payment {job_payment_id}: {str(e)}", level="ERROR")
            raise


# ─────────────────────────────────────────────────────────────────────────────
# JOB MILESTONE
# ─────────────────────────────────────────────────────────────────────────────

class JobMilestoneFunctions:

    @staticmethod
    def create_job_milestone(
        job_payment_id: str,
        milestone_order: int,
        work_progress: str,
        payment_percentage: str,
    ) -> Dict:
        """
        Create a single job milestone template row.
        work_progress: e.g. '25%', '50%'
        payment_percentage: e.g. '25%', '50%'
        """
        try:
            db = get_db()
            milestone_id = str(uuid.uuid4())

            data = {
                "milestone_id": milestone_id,
                "job_payment_id": job_payment_id,
                "milestone_order": milestone_order,
                "work_progress": work_progress,
                "payment_percentage": payment_percentage,
            }

            db.insert_data(table_name="job_milestone", data=data)
            logger("JOB_MILESTONE_FUNCTIONS", f"job_milestone {milestone_id} created for payment {job_payment_id}", level="INFO")
            return convert_uuids_to_str(data)

        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error creating job_milestone: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def bulk_create_job_milestones(job_payment_id: str, milestones: List[Dict]) -> List[Dict]:
        """
        Bulk-create job milestone rows from a list.
        Each dict must have: milestone_order, work_progress, payment_percentage
        """
        created = []
        for m in milestones:
            row = JobMilestoneFunctions.create_job_milestone(
                job_payment_id=job_payment_id,
                milestone_order=m["milestone_order"],
                work_progress=m["work_progress"],
                payment_percentage=m["payment_percentage"],
            )
            created.append(row)

        logger("JOB_MILESTONE_FUNCTIONS", f"Bulk-created {len(created)} job milestones for payment {job_payment_id}", level="INFO")
        return created

    @staticmethod
    def get_job_milestone_by_id(milestone_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_milestone",
                conditions=[("milestone_id", "=", milestone_id)],
                limit=1
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error fetching job_milestone {milestone_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_milestones_by_payment_id(job_payment_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="job_milestone",
                conditions=[("job_payment_id", "=", job_payment_id)],
                order_by="milestone_order ASC"
            )
            logger("JOB_MILESTONE_FUNCTIONS", f"Fetched {len(rows)} milestones for payment {job_payment_id}", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error fetching job milestones: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_job_milestones_by_job_post_id(job_post_id: str) -> List[Dict]:
        """
        Convenience method — fetches milestones directly from job_post_id
        by first resolving the job_payment_id.
        """
        try:
            payment = JobPaymentFunctions.get_job_payment_by_job_post_id(job_post_id)
            if not payment:
                return []
            return JobMilestoneFunctions.get_job_milestones_by_payment_id(payment["job_payment_id"])
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error fetching milestones for job_post {job_post_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_job_milestone(milestone_id: str, update_data: Dict) -> Optional[Dict]:
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                return JobMilestoneFunctions.get_job_milestone_by_id(milestone_id)

            db.update_data(
                table_name="job_milestone",
                data=update_data,
                conditions=[("milestone_id", "=", milestone_id)]
            )
            logger("JOB_MILESTONE_FUNCTIONS", f"job_milestone {milestone_id} updated", level="INFO")
            return JobMilestoneFunctions.get_job_milestone_by_id(milestone_id)
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error updating job_milestone {milestone_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_milestone(milestone_id: str) -> bool:
        try:
            db = get_db()
            db.delete_data(
                table_name="job_milestone",
                conditions=[("milestone_id", "=", milestone_id)]
            )
            logger("JOB_MILESTONE_FUNCTIONS", f"job_milestone {milestone_id} deleted", level="INFO")
            return True
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error deleting job_milestone {milestone_id}: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_job_milestones_by_payment_id(job_payment_id: str) -> bool:
        """Delete all milestones under a payment (used when replacing milestone config)."""
        try:
            db = get_db()
            db.delete_data(
                table_name="job_milestone",
                conditions=[("job_payment_id", "=", job_payment_id)]
            )
            logger("JOB_MILESTONE_FUNCTIONS", f"Deleted all milestones for payment {job_payment_id}", level="INFO")
            return True
        except Exception as e:
            logger("JOB_MILESTONE_FUNCTIONS", f"Error deleting milestones for payment {job_payment_id}: {str(e)}", level="ERROR")
            raise