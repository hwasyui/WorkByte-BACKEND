import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from functions.db_manager import get_db
from functions.logger import logger
from routes.contracts.contract_functions import ContractFunctions, _fire_notification
from routes.contracts.milestone_functions import MilestoneFunctions
from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.dm.dm_functions import DMFunctions
from routes.notifications.notification_functions import NotificationFunctions
from ai_related.review_analysis.review_pipeline import run_post_completion_pipeline
from ai_related.review_analysis.client_review_pipeline import run_client_review_post_completion_pipeline

PLATFORM_COMMISSION_RATE = 0.10
AMOUNT_TOLERANCE = 0.01
PAYMENT_STAGE_STATUSES = ("pending_payment", "payment_review", "payment_rejected")

def convert_uuids_to_str(data: Dict) -> Dict:
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result

def notify_admins(notif_type: str, title: str, body: str, data: Dict) -> None:
    try:
        rows = get_db().execute_query("SELECT user_id FROM users WHERE is_admin = TRUE", {})
        for row in rows or []:
            _fire_notification(NotificationFunctions.notify(
                recipient_user_id=str(row["user_id"]),
                notif_type=notif_type,
                title=title,
                body=body,
                data=data,
            ))
    except Exception as e:
        logger("PAYMENT_FUNCTIONS", f"Failed to notify admins: {str(e)}", level="WARNING")

class PaymentFunctions:

    @staticmethod
    def get_freelancer_expected_amount(milestone: Dict) -> float:
        return round(float(milestone["amount"]) * (1 - PLATFORM_COMMISSION_RATE), 2)

    @staticmethod
    def get_proof_by_id(proof_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(table_name="payment_proof", conditions=[("proof_id", "=", proof_id)], limit=1)
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error fetching payment proof: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_proofs_by_contract_id(contract_id: str) -> List[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="payment_proof",
                conditions=[("contract_id", "=", contract_id)],
                order_by="created_at DESC",
            )
            return [convert_uuids_to_str(dict(row)) for row in rows]
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error fetching payment proofs: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_latest_proof_for_payee(contract_id: str, payee: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="payment_proof",
                conditions=[("contract_id", "=", contract_id), ("payee", "=", payee)],
                order_by="created_at DESC",
                limit=1,
            )
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error fetching latest proof for payee: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_proof(
        contract_id: str,
        payee: str,
        amount: float,
        reference_number: Optional[str],
        file_url: str,
        uploaded_by: str,
        proof_id: Optional[str] = None,
    ) -> Dict:
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise ValueError("Contract not found")

            if contract["status"] not in PAYMENT_STAGE_STATUSES:
                raise ValueError(f"Cannot upload payment proof when contract status is '{contract['status']}'")

            milestone = MilestoneFunctions.get_current_milestone(contract_id)
            if milestone:
                if milestone["status"] not in PAYMENT_STAGE_STATUSES:
                    raise ValueError("There is no milestone currently awaiting payment on this contract")
                if payee != "freelancer":
                    raise ValueError("Only the freelancer's share can be paid while milestones are still in progress")
                expected = PaymentFunctions.get_freelancer_expected_amount(milestone)
                milestone_id = milestone["milestone_id"]
                milestone_title = milestone.get("title")
            else:
                if payee != "admin":
                    raise ValueError("All milestones are paid - only the platform fee remains")
                expected = round(float(contract["agreed_budget"]) * PLATFORM_COMMISSION_RATE, 2)
                milestone_id = None
                milestone_title = None

            submitted = round(float(amount), 2)
            if abs(submitted - expected) > AMOUNT_TOLERANCE:
                raise ValueError(
                    f"Amount mismatch: {payee}'s share should be {expected} {contract.get('budget_currency', 'USD')}, got {submitted}"
                )

            proof_id = proof_id or str(uuid.uuid4())
            db = get_db()
            db.insert_data(
                table_name="payment_proof",
                data={
                    "proof_id": proof_id,
                    "contract_id": contract_id,
                    "milestone_id": milestone_id,
                    "payee": payee,
                    "amount": submitted,
                    "reference_number": reference_number,
                    "file_url": file_url,
                    "uploaded_by": uploaded_by,
                    "status": "pending_review",
                },
            )
            if milestone_id:
                db.update_data(
                    table_name="milestone",
                    data={"status": "payment_review"},
                    conditions=[("milestone_id", "=", milestone_id)],
                )
            db.update_data(
                table_name="contract",
                data={"status": "payment_review"},
                conditions=[("contract_id", "=", contract_id)],
            )

            notify_admins(
                notif_type="payment_proof_uploaded",
                title="Payment proof uploaded",
                body=(
                    f"The platform fee proof for \"{contract.get('contract_title')}\" is awaiting verification."
                    if milestone_id is None else
                    f"A payment proof for \"{milestone_title}\" on \"{contract.get('contract_title')}\" ({payee}'s share) is awaiting verification."
                ),
                data={"contract_id": contract_id, "milestone_id": milestone_id, "proof_id": proof_id, "payee": payee},
            )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=uploaded_by,
                    message_text=f"Payment proof uploaded for {payee}" + (f" ({milestone_title})." if milestone_title else "."),
                    event_type="payment_proof_uploaded",
                    metadata={"proof_id": proof_id, "milestone_id": milestone_id, "payee": payee},
                )
            except Exception:
                pass

            logger("PAYMENT_FUNCTIONS", f"Payment proof {proof_id} created for contract {contract_id} ({payee})", level="INFO")
            return PaymentFunctions.get_proof_by_id(proof_id)
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error creating payment proof: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def verify_proof(proof_id: str, admin_user_id: Optional[str] = None) -> Dict:
        try:
            proof = PaymentFunctions.get_proof_by_id(proof_id)
            if not proof:
                raise ValueError("Payment proof not found")
            if proof["status"] != "pending_review":
                raise ValueError(f"Cannot verify a proof that is already '{proof['status']}'")

            now = datetime.now(timezone.utc)
            with get_db().transaction() as tx:
                tx.update_data(
                    table_name="payment_proof",
                    data={"status": "verified", "verified_by": admin_user_id, "verified_at": now},
                    conditions=[("proof_id", "=", proof_id)],
                )
                if proof["payee"] == "admin":
                    contract = ContractFunctions.get_contract_by_id(proof["contract_id"])
                    commission_amount = round(float(contract["agreed_budget"]) * PLATFORM_COMMISSION_RATE, 2)
                    payout_amount = round(float(contract["agreed_budget"]) - commission_amount, 2)
                    tx.update_data(
                        table_name="contract",
                        data={
                            "status": "completed",
                            "commission_rate": PLATFORM_COMMISSION_RATE,
                            "commission_amount": commission_amount,
                            "payout_amount": payout_amount,
                            "payment_verified_at": now,
                            "payment_verified_by": admin_user_id,
                            "actual_completion_date": now.date(),
                        },
                        conditions=[("contract_id", "=", proof["contract_id"])],
                    )

            updated_proof = PaymentFunctions.get_proof_by_id(proof_id)

            _fire_notification(NotificationFunctions.notify(
                recipient_user_id=proof["uploaded_by"],
                notif_type="payment_proof_verified",
                title="Payment proof verified",
                body=(
                    "Your payment proof was automatically verified due to admin inaction."
                    if admin_user_id is None else
                    "Your payment proof has been verified by the admin."
                ),
                data={"contract_id": proof["contract_id"], "proof_id": proof_id},
            ))

            if proof["payee"] == "admin":
                updated_contract = ContractFunctions.get_contract_by_id(proof["contract_id"])
                PaymentFunctions._apply_completion_side_effects(updated_contract)

            logger("PAYMENT_FUNCTIONS", f"Payment proof {proof_id} verified by {'auto-verify sweep' if admin_user_id is None else f'admin {admin_user_id}'}", level="INFO")
            return updated_proof
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error verifying payment proof: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def reject_proof(proof_id: str, admin_user_id: str, reason: str) -> Dict:
        try:
            proof = PaymentFunctions.get_proof_by_id(proof_id)
            if not proof:
                raise ValueError("Payment proof not found")
            if proof["status"] != "pending_review":
                raise ValueError(f"Cannot reject a proof that is already '{proof['status']}'")

            db = get_db()
            db.update_data(
                table_name="payment_proof",
                data={
                    "status": "rejected",
                    "rejection_reason": reason,
                    "verified_by": admin_user_id,
                    "verified_at": datetime.now(timezone.utc),
                },
                conditions=[("proof_id", "=", proof_id)],
            )
            if proof["milestone_id"]:
                db.update_data(
                    table_name="milestone",
                    data={"status": "payment_rejected"},
                    conditions=[("milestone_id", "=", proof["milestone_id"])],
                )
            db.update_data(
                table_name="contract",
                data={"status": "payment_rejected"},
                conditions=[("contract_id", "=", proof["contract_id"])],
            )

            _fire_notification(NotificationFunctions.notify(
                recipient_user_id=proof["uploaded_by"],
                notif_type="payment_proof_rejected",
                title="Payment proof rejected",
                body=f"Your payment proof was rejected: {reason}",
                data={"contract_id": proof["contract_id"], "proof_id": proof_id},
            ))

            logger("PAYMENT_FUNCTIONS", f"Payment proof {proof_id} rejected by admin {admin_user_id}: {reason}", level="INFO")
            return PaymentFunctions.get_proof_by_id(proof_id)
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error rejecting payment proof: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def confirm_freelancer_receipt(contract_id: str, freelancer_user_id: str) -> Dict:
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise ValueError("Contract not found")
            if contract["status"] not in PAYMENT_STAGE_STATUSES:
                raise ValueError(f"Cannot confirm receipt when contract status is '{contract['status']}'")

            milestone = MilestoneFunctions.get_current_milestone(contract_id)
            if not milestone or milestone["status"] not in PAYMENT_STAGE_STATUSES:
                raise ValueError("There is no milestone currently awaiting payment on this contract")
            if milestone.get("freelancer_confirmed_receipt_at"):
                raise ValueError("Receipt has already been confirmed for this milestone")

            now = datetime.now(timezone.utc)
            payout_amount = PaymentFunctions.get_freelancer_expected_amount(milestone)
            db = get_db()
            db.update_data(
                table_name="milestone",
                data={
                    "status": "completed",
                    "payout_amount": payout_amount,
                    "freelancer_confirmed_receipt_at": now,
                },
                conditions=[("milestone_id", "=", milestone["milestone_id"])],
            )

            notify_admins(
                notif_type="freelancer_confirmed_receipt",
                title="Freelancer confirmed receipt",
                body=f"The freelancer confirmed receiving their share for \"{milestone.get('title')}\" on \"{contract.get('contract_title')}\".",
                data={"contract_id": contract_id, "milestone_id": milestone["milestone_id"]},
            )

            next_milestone = MilestoneFunctions.unlock_next_milestone(contract_id, milestone["sequence_order"])
            if next_milestone:
                db.update_data(
                    table_name="contract",
                    data={"status": "active"},
                    conditions=[("contract_id", "=", contract_id)],
                )
                PaymentFunctions._notify_milestone_advanced(contract, next_milestone)
            else:
                db.update_data(
                    table_name="contract",
                    data={"status": "pending_payment"},
                    conditions=[("contract_id", "=", contract_id)],
                )
                PaymentFunctions._notify_commission_stage_started(contract)

            logger("PAYMENT_FUNCTIONS", f"Freelancer {freelancer_user_id} confirmed receipt for contract {contract_id}", level="INFO")
            return ContractFunctions.get_contract_by_id(contract_id)
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error confirming freelancer receipt: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _notify_milestone_advanced(contract: Dict, next_milestone: Dict) -> None:
        try:
            contract_id = contract.get("contract_id")
            fl = FreelancerFunctions.get_freelancer_by_id(str(contract.get("freelancer_id")))
            cl = ClientFunctions.get_client_by_id(str(contract.get("client_id")))
            for party in (fl, cl):
                if party:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(party["user_id"]),
                        notif_type="milestone_paid",
                        title="Milestone paid",
                        body=f"A milestone on \"{contract.get('contract_title')}\" is complete. \"{next_milestone.get('title')}\" is now open.",
                        data={"contract_id": contract_id, "milestone_id": next_milestone.get("milestone_id")},
                    ))
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Milestone-advanced notification failed (non-fatal): {str(e)}", level="WARNING")

    @staticmethod
    def _notify_commission_stage_started(contract: Dict) -> None:
        try:
            contract_id = contract.get("contract_id")
            fl = FreelancerFunctions.get_freelancer_by_id(str(contract.get("freelancer_id")))
            cl = ClientFunctions.get_client_by_id(str(contract.get("client_id")))
            for party in (fl, cl):
                if party:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(party["user_id"]),
                        notif_type="commission_stage_started",
                        title="All milestones paid",
                        body=f"All milestones on \"{contract.get('contract_title')}\" are paid. Please transfer the platform fee to complete this contract.",
                        data={"contract_id": contract_id},
                    ))
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Commission-stage notification failed (non-fatal): {str(e)}", level="WARNING")

    @staticmethod
    def admin_override_completion(contract_id: str, admin_user_id: str, reason: str) -> Dict:
        try:
            contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                raise ValueError("Contract not found")
            if contract["status"] not in PAYMENT_STAGE_STATUSES:
                raise ValueError(f"Cannot override completion when contract status is '{contract['status']}'")

            milestone = MilestoneFunctions.get_current_milestone(contract_id)
            if not milestone:
                raise ValueError("Every milestone on this contract is already completed")
            if milestone["status"] != "payment_review":
                raise ValueError(f"Cannot override completion when the current milestone's status is '{milestone['status']}'")
            if milestone.get("freelancer_confirmed_receipt_at"):
                raise ValueError("Receipt has already been confirmed for this milestone")

            db = get_db()
            proof_rows = db.fetch_data(
                table_name="payment_proof",
                conditions=[("milestone_id", "=", milestone["milestone_id"]), ("payee", "=", "freelancer")],
                limit=1,
            )
            if not proof_rows:
                raise ValueError("No freelancer-share payment proof was found for this milestone")

            now = datetime.now(timezone.utc)
            payout_amount = PaymentFunctions.get_freelancer_expected_amount(milestone)
            db.update_data(
                table_name="milestone",
                data={
                    "status": "completed",
                    "payout_amount": payout_amount,
                    "completed_by_admin_override": True,
                    "payment_verified_at": now,
                    "payment_verified_by": admin_user_id,
                },
                conditions=[("milestone_id", "=", milestone["milestone_id"])],
            )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=admin_user_id,
                    message_text=f"Admin marked \"{milestone.get('title')}\" completed without freelancer confirmation: {reason}",
                    event_type="payment_admin_override",
                    metadata={"admin_user_id": admin_user_id, "milestone_id": milestone["milestone_id"], "reason": reason},
                )
            except Exception:
                pass

            fl = FreelancerFunctions.get_freelancer_by_id(str(contract.get("freelancer_id")))
            if fl:
                _fire_notification(NotificationFunctions.notify(
                    recipient_user_id=str(fl["user_id"]),
                    notif_type="milestone_override_completed",
                    title="Milestone marked complete by admin",
                    body=f"An admin marked \"{milestone.get('title')}\" on \"{contract.get('contract_title')}\" as received on your behalf: {reason}",
                    data={"contract_id": contract_id, "milestone_id": milestone["milestone_id"]},
                ))

            next_milestone = MilestoneFunctions.unlock_next_milestone(contract_id, milestone["sequence_order"])
            if next_milestone:
                db.update_data(
                    table_name="contract",
                    data={"status": "active"},
                    conditions=[("contract_id", "=", contract_id)],
                )
                PaymentFunctions._notify_milestone_advanced(contract, next_milestone)
            else:
                db.update_data(
                    table_name="contract",
                    data={"status": "pending_payment"},
                    conditions=[("contract_id", "=", contract_id)],
                )
                PaymentFunctions._notify_commission_stage_started(contract)

            logger("PAYMENT_FUNCTIONS", f"Milestone {milestone['milestone_id']} on contract {contract_id} completion overridden by admin {admin_user_id}: {reason}", level="WARNING")
            return ContractFunctions.get_contract_by_id(contract_id)
        except ValueError:
            raise
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error overriding contract completion: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _apply_completion_side_effects(contract: Dict) -> None:
        try:
            from ai_related.job_engine.embedding_manager import mark_contract_dirty
            db = get_db()
            client_id = contract.get("client_id")
            freelancer_id = contract.get("freelancer_id")
            contract_id = contract.get("contract_id")

            if client_id:
                rows = db.fetch_data("client", conditions=[("client_id", "=", client_id)], limit=1)
                if rows:
                    current = rows[0].get("total_jobs_completed") or 0
                    db.update_data("client", {"total_jobs_completed": current + 1}, [("client_id", "=", client_id)])

            if freelancer_id:
                rows = db.fetch_data("freelancer", conditions=[("freelancer_id", "=", freelancer_id)], limit=1)
                if rows:
                    current = rows[0].get("total_jobs") or 0
                    db.update_data("freelancer", {"total_jobs": current + 1}, [("freelancer_id", "=", freelancer_id)])

            ContractFunctions._create_auto_portfolio_entry(contract_id=contract_id, contract=contract)
            mark_contract_dirty(contract_id)

            _fire_notification(run_post_completion_pipeline(contract_id))
            _fire_notification(run_client_review_post_completion_pipeline(contract_id))

            fl = FreelancerFunctions.get_freelancer_by_id(str(freelancer_id)) if freelancer_id else None
            cl = ClientFunctions.get_client_by_id(str(client_id)) if client_id else None
            for party in (fl, cl):
                if party:
                    _fire_notification(NotificationFunctions.notify(
                        recipient_user_id=str(party["user_id"]),
                        notif_type="contract_completed",
                        title="Contract completed",
                        body=f"\"{contract.get('contract_title')}\" is complete. Payment has been verified.",
                        data={"contract_id": contract_id},
                    ))
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Completion side effects failed (non-fatal): {str(e)}", level="WARNING")
