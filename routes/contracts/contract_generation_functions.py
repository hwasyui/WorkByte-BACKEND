import uuid
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from datetime import datetime
from typing import Optional, Dict, List
from functions.db_manager import get_db
from functions.logger import logger
from functions.minio_client import upload_file, create_signed_url
from routes.contracts.contract_functions import ContractFunctions
from routes.clients.client_functions import ClientFunctions
from routes.freelancers.freelancer_functions import FreelancerFunctions
from routes.job_posts.job_post_functions import JobPostFunctions
from routes.job_roles.job_role_functions import JobRoleFunctions
from routes.proposals.proposal_functions import ProposalFunctions
from routes.contracts.contract_pdf_generator import generate_contract_pdf

CONTRACT_BUCKET = "contract-assets"


class ContractGenerationFunctions:
    """Support contract PDF generation and contract_terms persistence."""

    @staticmethod
    def get_contract_terms(contract_id: str) -> Optional[Dict]:
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="contract_terms",
                conditions=[("contract_id", "=", contract_id)],
                limit=1
            )
            if rows:
                return dict(rows[0])
            return None
        except Exception as e:
            logger("CONTRACT_GENERATION", f"Error fetching contract terms: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def upsert_contract_terms(contract_id: str, terms: Dict, db=None) -> Dict:
        """Write a contract's terms, inserting or replacing them wholesale.

        Pass `db` an open Transaction to make this part of a larger unit of work -
        contract creation does, so the contract and the terms it was generated from
        can never exist apart from one another.
        """
        try:
            db = db or get_db()
            query = """
                INSERT INTO contract_terms (
                    contract_terms_id, contract_id, termination_notice, governing_law,
                    confidentiality, confidentiality_text, late_payment_penalty,
                    dispute_resolution, revision_rounds, additional_clauses, payment_schedule
                ) VALUES (
                    :contract_terms_id, :contract_id, :termination_notice, :governing_law,
                    :confidentiality, :confidentiality_text, :late_payment_penalty,
                    :dispute_resolution, :revision_rounds, :additional_clauses, :payment_schedule
                )
                ON CONFLICT (contract_id) DO UPDATE SET
                    termination_notice = EXCLUDED.termination_notice,
                    governing_law = EXCLUDED.governing_law,
                    confidentiality = EXCLUDED.confidentiality,
                    confidentiality_text = EXCLUDED.confidentiality_text,
                    late_payment_penalty = EXCLUDED.late_payment_penalty,
                    dispute_resolution = EXCLUDED.dispute_resolution,
                    revision_rounds = EXCLUDED.revision_rounds,
                    additional_clauses = EXCLUDED.additional_clauses,
                    payment_schedule = EXCLUDED.payment_schedule
                RETURNING *
            """
            payload = {
                "contract_terms_id": str(uuid.uuid4()),
                "contract_id": contract_id,
                "termination_notice": terms.get("termination_notice"),
                "governing_law": terms.get("governing_law"),
                "confidentiality": terms.get("confidentiality", False),
                "confidentiality_text": terms.get("confidentiality_text"),
                "late_payment_penalty": terms.get("late_payment_penalty"),
                "dispute_resolution": terms.get("dispute_resolution"),
                "revision_rounds": terms.get("revision_rounds"),
                "additional_clauses": terms.get("additional_clauses"),
                "payment_schedule": terms.get("payment_schedule"),
            }
            rows = db.execute_query(query, payload)
            if rows:
                return dict(rows[0])
            existing = ContractGenerationFunctions.get_contract_terms(contract_id)
            return existing or {}
        except Exception as e:
            logger("CONTRACT_GENERATION", f"Error saving contract terms: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def build_generation_context(
        contract_id: str, contract: Optional[Dict] = None, contract_terms: Optional[Dict] = None
    ) -> Dict:
        """Gather everything the PDF is rendered from.

        `contract` and `contract_terms` can be supplied instead of read, which is what
        lets a contract be rendered before it is inserted: creation renders the PDF
        first and writes the row last, so that a failure leaves nothing behind. Every
        other entity the document needs - the proposal, job post, role, freelancer and
        client - already exists by then and is still read from the database.
        """
        try:
            if contract is None:
                contract = ContractFunctions.get_contract_by_id(contract_id)
            if not contract:
                return None

            if contract_terms is None:
                contract_terms = ContractGenerationFunctions.get_contract_terms(contract_id) or {}
            proposal = ProposalFunctions.get_proposal_by_id(contract["proposal_id"]) or {}
            job_post = JobPostFunctions.get_job_post_by_id(contract["job_post_id"]) or {}
            job_role = JobRoleFunctions.get_job_role_by_id(contract["job_role_id"]) or {}
            freelancer = FreelancerFunctions.get_freelancer_by_id(contract["freelancer_id"]) or {}
            client = ClientFunctions.get_client_by_id(contract["client_id"]) or {}

            return {
                "contract": contract,
                "contract_terms": contract_terms,
                "proposal": proposal,
                "job_post": job_post,
                "job_role": job_role,
                "freelancer": freelancer,
                "client": client,
            }
        except Exception as e:
            logger("CONTRACT_GENERATION", f"Error building generation context: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def upload_contract_pdf(contract_id: str, pdf_bytes: bytes) -> str:
        try:
            storage_path = f"{contract_id}/contract.pdf"
            upload_file(CONTRACT_BUCKET, storage_path, pdf_bytes)
            return storage_path
        except Exception as e:
            logger("CONTRACT_GENERATION", f"Error uploading contract PDF: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_signed_contract_url(contract_pdf_path: str, expires_in: int = 3600) -> str:
        try:
            return create_signed_url(CONTRACT_BUCKET, contract_pdf_path, expires_in)
        except Exception as e:
            logger("CONTRACT_GENERATION", f"Error creating signed URL: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def render_contract_pdf(
        contract_id: str,
        generated_at: Optional[datetime] = None,
        contract: Optional[Dict] = None,
        contract_terms: Optional[Dict] = None,
    ) -> bytes:
        """Render the contract PDF.

        Reads the persisted row by default. Creation passes `contract` and
        `contract_terms` directly, because at that point neither has been written yet -
        see build_generation_context.

        Pass the same `generated_at` that gets written to contract.contract_pdf_generated_at
        so the date on the document matches the column. Defaults to now for callers that
        only want the bytes.
        """
        context = ContractGenerationFunctions.build_generation_context(
            contract_id, contract=contract, contract_terms=contract_terms
        )
        if context is None:
            raise ValueError("Contract not found")

        contract = context["contract"]
        return generate_contract_pdf(
            {
                "contract_id": contract.get("contract_id"),
                "contract_title": contract.get("contract_title"),
                # The contract's own role_title, not the job role's: the two are separate
                # columns, and this one is what an edit to the contract changes.
                "role_title": contract.get("role_title"),
                "agreed_budget": contract.get("agreed_budget"),
                "budget_currency": contract.get("budget_currency"),
                "payment_structure": contract.get("payment_structure"),
                "start_date": contract.get("start_date"),
                "end_date": contract.get("end_date"),
                "agreed_duration": contract.get("agreed_duration"),
                "generated_at": (generated_at or datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC"),
                "job_post": context["job_post"],
                "job_role": context["job_role"],
                "freelancer": context["freelancer"],
                "client": context["client"]
            },
            context["contract_terms"],
        )
