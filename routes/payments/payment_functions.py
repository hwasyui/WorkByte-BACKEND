import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

from functions.db_manager import get_db
from functions.logger import logger
from routes.contracts.contract_functions import ContractFunctions, _fire_notification
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
    def get_expected_amount(contract: Dict, payee: str) -> float:
        agreed_budget = float(contract["agreed_budget"])
        if payee == "admin":
            return round(agreed_budget * PLATFORM_COMMISSION_RATE, 2)
        return round(agreed_budget * (1 - PLATFORM_COMMISSION_RATE), 2)

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

            expected = PaymentFunctions.get_expected_amount(contract, payee)
            submitted = round(float(amount), 2)
            if abs(submitted - expected) > AMOUNT_TOLERANCE:
                raise ValueError(
                    f"Amount mismatch: {payee}'s share of the {contract.get('budget_currency', 'USD')} "
                    f"{contract['agreed_budget']} budget should be {expected}, got {submitted}"
                )

            proof_id = proof_id or str(uuid.uuid4())
            db = get_db()
            db.insert_data(
                table_name="payment_proof",
                data={
                    "proof_id": proof_id,
                    "contract_id": contract_id,
                    "payee": payee,
                    "amount": submitted,
                    "reference_number": reference_number,
                    "file_url": file_url,
                    "uploaded_by": uploaded_by,
                    "status": "pending_review",
                },
            )
            db.update_data(
                table_name="contract",
                data={"status": "payment_review"},
                conditions=[("contract_id", "=", contract_id)],
            )

            notify_admins(
                notif_type="payment_proof_uploaded",
                title="Payment proof uploaded",
                body=f"A payment proof for \"{contract.get('contract_title')}\" ({payee}'s share) is awaiting verification.",
                data={"contract_id": contract_id, "proof_id": proof_id, "payee": payee},
            )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=uploaded_by,
                    message_text=f"Payment proof uploaded for {payee}.",
                    event_type="payment_proof_uploaded",
                    metadata={"proof_id": proof_id, "payee": payee},
                )
            except Exception:
                pass

            logger("PAYMENT_FUNCTIONS", f"Payment proof {proof_id} created for contract {contract_id} ({payee})", level="INFO")
            return PaymentFunctions.get_proof_by_id(proof_id)
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error creating payment proof: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def verify_proof(proof_id: str, admin_user_id: str) -> Dict:
        try:
            proof = PaymentFunctions.get_proof_by_id(proof_id)
            if not proof:
                raise ValueError("Payment proof not found")
            if proof["status"] != "pending_review":
                raise ValueError(f"Cannot verify a proof that is already '{proof['status']}'")

            db = get_db()
            db.update_data(
                table_name="payment_proof",
                data={"status": "verified", "verified_by": admin_user_id, "verified_at": datetime.now(timezone.utc)},
                conditions=[("proof_id", "=", proof_id)],
            )
            updated_proof = PaymentFunctions.get_proof_by_id(proof_id)

            _fire_notification(NotificationFunctions.notify(
                recipient_user_id=proof["uploaded_by"],
                notif_type="payment_proof_verified",
                title="Payment proof verified",
                body="Your payment proof has been verified by the admin.",
                data={"contract_id": proof["contract_id"], "proof_id": proof_id},
            ))

            PaymentFunctions._maybe_complete_contract(proof["contract_id"])

            logger("PAYMENT_FUNCTIONS", f"Payment proof {proof_id} verified by admin {admin_user_id}", level="INFO")
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
            if contract.get("freelancer_confirmed_receipt_at"):
                raise ValueError("Receipt has already been confirmed for this contract")

            db = get_db()
            db.update_data(
                table_name="contract",
                data={"freelancer_confirmed_receipt_at": datetime.now(timezone.utc)},
                conditions=[("contract_id", "=", contract_id)],
            )

            notify_admins(
                notif_type="freelancer_confirmed_receipt",
                title="Freelancer confirmed receipt",
                body=f"The freelancer confirmed receiving their share for \"{contract.get('contract_title')}\".",
                data={"contract_id": contract_id},
            )

            completed = PaymentFunctions._maybe_complete_contract(contract_id)
            logger("PAYMENT_FUNCTIONS", f"Freelancer {freelancer_user_id} confirmed receipt for contract {contract_id}", level="INFO")
            return completed or ContractFunctions.get_contract_by_id(contract_id)
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error confirming freelancer receipt: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def _write_completion(tx, contract_id: str, agreed_budget, verified_by: str, admin_override: bool) -> None:
        commission_amount = round(float(agreed_budget) * PLATFORM_COMMISSION_RATE, 2)
        payout_amount = round(float(agreed_budget) - commission_amount, 2)
        tx.update_data(
            table_name="contract",
            data={
                "status": "completed",
                "commission_rate": PLATFORM_COMMISSION_RATE,
                "commission_amount": commission_amount,
                "payout_amount": payout_amount,
                "payment_verified_at": datetime.now(timezone.utc),
                "payment_verified_by": verified_by,
                "completed_by_admin_override": admin_override,
                "actual_completion_date": datetime.now(timezone.utc).date(),
            },
            conditions=[("contract_id", "=", contract_id)],
        )

    @staticmethod
    def _maybe_complete_contract(contract_id: str) -> Optional[Dict]:
        try:
            ready = False
            with get_db().transaction() as tx:
                rows = tx.execute_query(
                    "SELECT status, freelancer_confirmed_receipt_at, agreed_budget FROM contract WHERE contract_id = :cid FOR UPDATE",
                    {"cid": contract_id},
                )
                if rows:
                    contract = dict(rows[0])
                    if contract["status"] == "payment_review" and contract.get("freelancer_confirmed_receipt_at"):
                        admin_proof_rows = tx.execute_query(
                            "SELECT verified_by FROM payment_proof WHERE contract_id = :cid AND payee = 'admin' AND status = 'verified' ORDER BY verified_at DESC LIMIT 1",
                            {"cid": contract_id},
                        )
                        if admin_proof_rows:
                            PaymentFunctions._write_completion(
                                tx, contract_id, contract["agreed_budget"], str(admin_proof_rows[0]["verified_by"]), False
                            )
                            ready = True

            if not ready:
                return None
            completed_contract = ContractFunctions.get_contract_by_id(contract_id)
            PaymentFunctions._apply_completion_side_effects(completed_contract)
            return completed_contract
        except Exception as e:
            logger("PAYMENT_FUNCTIONS", f"Error completing contract: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def admin_override_completion(contract_id: str, admin_user_id: str, reason: str) -> Dict:
        try:
            with get_db().transaction() as tx:
                rows = tx.execute_query(
                    "SELECT status, agreed_budget FROM contract WHERE contract_id = :cid FOR UPDATE",
                    {"cid": contract_id},
                )
                if not rows:
                    raise ValueError("Contract not found")
                contract = dict(rows[0])
                if contract["status"] != "payment_review":
                    raise ValueError(f"Cannot override completion when contract status is '{contract['status']}'")

                admin_proof_rows = tx.execute_query(
                    "SELECT verified_by FROM payment_proof WHERE contract_id = :cid AND payee = 'admin' AND status = 'verified' ORDER BY verified_at DESC LIMIT 1",
                    {"cid": contract_id},
                )
                if not admin_proof_rows:
                    raise ValueError("The admin's payment proof must be verified before the contract can be completed")

                PaymentFunctions._write_completion(
                    tx, contract_id, contract["agreed_budget"], str(admin_proof_rows[0]["verified_by"]), True
                )

            try:
                DMFunctions.send_system_event(
                    contract_id=contract_id,
                    actor_id=admin_user_id,
                    message_text=f"Admin marked this contract completed without freelancer confirmation: {reason}",
                    event_type="payment_admin_override",
                    metadata={"admin_user_id": admin_user_id, "reason": reason},
                )
            except Exception:
                pass

            completed_contract = ContractFunctions.get_contract_by_id(contract_id)
            PaymentFunctions._apply_completion_side_effects(completed_contract)

            logger("PAYMENT_FUNCTIONS", f"Contract {contract_id} completion overridden by admin {admin_user_id}: {reason}", level="WARNING")
            return completed_contract
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
