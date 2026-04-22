"""
Contract walkthrough — assumes walkthrough.py has already been run.

Logs in as the existing walkthrough users, creates fresh job posts + proposals,
then exercises the full contract lifecycle:

  • full_payment contract  → PDF generated → signed URL returned
  • milestone_based contract → PDF generated → signed URL returned

Usage (from inside the backend container):
    python walkthrough/walkthrough-contract.py

Or from outside:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-contract.py
"""

import sys
import json
import os
import random
import datetime
import requests

BASE_URL = "http://localhost:8000"

_RUN_ID           = random.randint(1000, 9999)
_EMAIL_FREELANCER = f"contract.freelancer.{_RUN_ID}@walkthrough.dev"
_EMAIL_CLIENT1    = f"contract.client1.{_RUN_ID}@walkthrough.dev"
_EMAIL_CLIENT2    = f"contract.client2.{_RUN_ID}@walkthrough.dev"
_PASSWORD         = "SecurePass123"

# ── output tee ────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath: str):
        self._stdout = sys.stdout
        self._file   = open(filepath, "w", encoding="utf-8")

    def write(self, data: str):
        self._stdout.write(data)
        self._file.write(data)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def close(self):
        self._file.close()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return False


def _start_tee() -> tuple[_Tee, str]:
    ts      = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path    = os.path.join(out_dir, f"walkthrough_contract_results_{ts}.md")
    tee     = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee: _Tee, path: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {path}")

# ── request helpers ───────────────────────────────────────────────────────────

_step = 0

def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'='*60}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*60}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    print(f"  POST {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params, timeout=90)
    data = r.json()
    print(f"  GET  {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def put(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.put(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    print(f"  PUT  {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def extract(response: dict):
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]

# ── main ──────────────────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  Capstone API — Contract Walkthrough")
    print("="*60)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print(f"  Run ID : {_RUN_ID}")

    # ── 1. Register and log in fresh users ────────────────────────────────────

    step(f"Register fresh users for this run (id={_RUN_ID})")
    post("/auth/register", {
        "email": _EMAIL_FREELANCER,
        "password": _PASSWORD,
        "user_type": "freelancer",
        "full_name": "Contract Freelancer"
    })
    post("/auth/register", {
        "email": _EMAIL_CLIENT1,
        "password": _PASSWORD,
        "user_type": "client",
        "full_name": "Contract Client 1"
    })
    post("/auth/register", {
        "email": _EMAIL_CLIENT2,
        "password": _PASSWORD,
        "user_type": "client",
        "full_name": "Contract Client 2"
    })

    step("Log in all three users")
    tok_freelancer = token_from_login(_EMAIL_FREELANCER, _PASSWORD)
    tok_client1    = token_from_login(_EMAIL_CLIENT1,    _PASSWORD)
    tok_client2    = token_from_login(_EMAIL_CLIENT2,    _PASSWORD)
    print("  All tokens obtained.")

    # ── 2. Resolve profile IDs ────────────────────────────────────────────────

    step("Resolve freelancer and client profile IDs")
    fid  = extract(get("/freelancers", tok_freelancer))[0]["freelancer_id"]
    cid1 = extract(get("/clients", tok_client1))[0]["client_id"]
    cid2 = extract(get("/clients", tok_client2))[0]["client_id"]
    print(f"  freelancer_id : {fid}")
    print(f"  client1_id    : {cid1}")
    print(f"  client2_id    : {cid2}")

    # ── 3. Create fresh job posts ─────────────────────────────────────────────

    step("Client 1 — Create fresh job post for the full_payment contract")
    resp = post("/job-posts", {
        "client_id": cid1,
        "job_title": "Backend API Developer (Contract Run)",
        "job_description": (
            "Build and maintain REST APIs for our SaaS platform using Python, FastAPI, "
            "and PostgreSQL. Redis caching is a plus. Fully remote."
        ),
        "project_type": "individual",
        "project_scope": "medium",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_client1)
    job_fp_id = extract(resp)["job_post_id"]
    print(f"  job_fp_id (full_payment): {job_fp_id}")

    step("Client 1 — Add a role to the full_payment job")
    resp = post("/job-roles", {
        "job_post_id": job_fp_id,
        "role_title": "Backend Developer",
        "role_budget": 3000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Own the API layer — design, build, and maintain FastAPI endpoints.",
        "display_order": 0
    }, tok_client1)
    role_fp_id = extract(resp)["job_role_id"]
    print(f"  role_fp_id: {role_fp_id}")

    step("Client 2 — Create fresh job post for the milestone_based contract")
    resp = post("/job-posts", {
        "client_id": cid2,
        "job_title": "Data Pipeline Build (Contract Run)",
        "job_description": (
            "Build scalable data pipelines using Python and PostgreSQL. "
            "Dockerised deployments and weekly progress reports required."
        ),
        "project_type": "individual",
        "project_scope": "large",
        "estimated_duration": "4 months",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_client2)
    job_ms_id = extract(resp)["job_post_id"]
    print(f"  job_ms_id (milestone_based): {job_ms_id}")

    step("Client 2 — Add a role to the milestone_based job")
    resp = post("/job-roles", {
        "job_post_id": job_ms_id,
        "role_title": "Data Engineer",
        "role_budget": 4000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Design and build batch data pipelines in Python.",
        "display_order": 0
    }, tok_client2)
    role_ms_id = extract(resp)["job_role_id"]
    print(f"  role_ms_id: {role_ms_id}")

    # ── 4. Freelancer submits proposals ──────────────────────────────────────

    step("Freelancer submits a proposal for the full_payment job")
    resp = post("/proposals", {
        "job_post_id": job_fp_id,
        "job_role_id": role_fp_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have 4+ years building REST APIs with Python/FastAPI and PostgreSQL. "
            "I can deliver a clean, well-documented backend in 3 months on time."
        ),
        "proposed_budget": 3000.0,
        "proposed_duration": "3 months",
        "status": "pending"
    }, tok_freelancer)
    proposal_fp_id = extract(resp)["proposal_id"]
    print(f"  proposal_fp_id: {proposal_fp_id}")

    step("Freelancer submits a proposal for the milestone_based job")
    resp = post("/proposals", {
        "job_post_id": job_ms_id,
        "job_role_id": role_ms_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have strong Python + PostgreSQL experience and have built ETL pipelines "
            "with Docker. I can deliver the full pipeline in 4 months."
        ),
        "proposed_budget": 4000.0,
        "proposed_duration": "4 months",
        "status": "pending"
    }, tok_freelancer)
    proposal_ms_id = extract(resp)["proposal_id"]
    print(f"  proposal_ms_id: {proposal_ms_id}")

    # ── 5. Create contracts ───────────────────────────────────────────────────

    step("Client 1 creates a FULL-PAYMENT contract")
    resp = post("/contracts", {
        "job_post_id": job_fp_id,
        "job_role_id": role_fp_id,
        "proposal_id": proposal_fp_id,
        "freelancer_id": fid,
        "client_id": cid1,
        "contract_title": "Backend API Developer — TechStartup Inc.",
        "role_title": "Backend Developer",
        "agreed_budget": 3000.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "3 months",
        "status": "active",
        "start_date": "2026-05-01",
        "end_date": "2026-07-31"
    }, tok_client1)
    contract_fp_id = extract(resp)["contract_id"]
    print(f"  contract_fp_id: {contract_fp_id}")

    step("Client 2 creates a MILESTONE-BASED contract")
    resp = post("/contracts", {
        "job_post_id": job_ms_id,
        "job_role_id": role_ms_id,
        "proposal_id": proposal_ms_id,
        "freelancer_id": fid,
        "client_id": cid2,
        "contract_title": "Data Pipeline Build — DataCorp Solutions",
        "role_title": "Data Engineer",
        "agreed_budget": 4000.0,
        "budget_currency": "USD",
        "payment_structure": "milestone_based",
        "agreed_duration": "4 months",
        "status": "active",
        "start_date": "2026-05-01",
        "end_date": "2026-08-31"
    }, tok_client2)
    contract_ms_id = extract(resp)["contract_id"]
    print(f"  contract_ms_id: {contract_ms_id}")

    # ── 6. Inspect generation data ────────────────────────────────────────────

    step("Inspect auto-filled generation data for the full_payment contract")
    gen1 = extract(get(f"/contracts/{contract_fp_id}/generation-data", tok_client1))
    c1   = gen1["contract"]
    print(f"  contract_title     : {c1['contract_title']}")
    print(f"  payment_structure  : {c1['payment_structure']}")
    print(f"  agreed_budget      : ${c1['agreed_budget']} {c1.get('budget_currency', 'USD')}")
    print(f"  freelancer name    : {gen1['freelancer'].get('full_name', '?')}")
    print(f"  client name        : {gen1['client'].get('full_name', gen1['client'].get('company_name', '?'))}")
    print(f"  job post title     : {gen1['job_post'].get('job_title', '?')}")
    print(f"  existing terms     : {bool(gen1['contract_terms'])}")

    step("Inspect auto-filled generation data for the milestone_based contract")
    gen2 = extract(get(f"/contracts/{contract_ms_id}/generation-data", tok_client2))
    c2   = gen2["contract"]
    print(f"  contract_title     : {c2['contract_title']}")
    print(f"  payment_structure  : {c2['payment_structure']}")
    print(f"  agreed_budget      : ${c2['agreed_budget']} {c2.get('budget_currency', 'USD')}")

    # ── 7. Generate PDFs ──────────────────────────────────────────────────────

    step("Generate full_payment contract PDF → upload to Supabase")
    print("  Calling POST /contracts/{id}/generate ...")
    resp = post(f"/contracts/{contract_fp_id}/generate", {
        "end_date": "2026-07-31",
        "agreed_duration": "3 months",
        "termination_notice": 14,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": (
            "Both parties agree to keep all project details, source code, and business "
            "information strictly confidential for 2 years post-contract."
        ),
        "late_payment_penalty": 1.5,
        "dispute_resolution": "negotiation",
        "revision_rounds": 2,
        "additional_clauses": "Freelancer must use Git for all deliverables with weekly code reviews."
    }, tok_client1)
    result_fp = extract(resp)
    pdf_path_fp = result_fp.get("contract_pdf_url", "")
    print(f"  PDF storage path : {pdf_path_fp}")
    print(f"  PDF generated at : {result_fp.get('contract_pdf_generated_at', '?')}")

    step("Generate milestone_based contract PDF → upload to Supabase")
    print("  Calling POST /contracts/{id}/generate ...")
    resp = post(f"/contracts/{contract_ms_id}/generate", {
        "end_date": "2026-08-31",
        "agreed_duration": "4 months",
        "termination_notice": 30,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": "All pipeline schemas and business data are confidential for 3 years.",
        "late_payment_penalty": 2.0,
        "dispute_resolution": "mediation",
        "revision_rounds": 1,
        "payment_schedule": [
            {
                "phase": "Phase 1 — Ingestion Layer",
                "description": "Build REST API ingestion layer and raw data store.",
                "amount": 1200.0,
                "percentage": 30.0,
                "due_date": "2026-05-31"
            },
            {
                "phase": "Phase 2 — Transformation & Load",
                "description": "Implement transform logic and PostgreSQL loading pipeline.",
                "amount": 1600.0,
                "percentage": 40.0,
                "due_date": "2026-07-15"
            },
            {
                "phase": "Phase 3 — Testing & Handoff",
                "description": "End-to-end tests, documentation, and project handover.",
                "amount": 1200.0,
                "percentage": 30.0,
                "due_date": "2026-08-31"
            }
        ],
        "additional_clauses": "Freelancer delivers weekly progress reports every Friday."
    }, tok_client2)
    result_ms = extract(resp)
    pdf_path_ms = result_ms.get("contract_pdf_url", "")
    print(f"  PDF storage path : {pdf_path_ms}")
    print(f"  PDF generated at : {result_ms.get('contract_pdf_generated_at', '?')}")

    # ── 8. Signed download URLs ───────────────────────────────────────────────

    step("Get signed URL for the full_payment contract PDF")
    url1 = extract(get(f"/contracts/{contract_fp_id}/pdf-url", tok_client1)).get("pdf_url", "")
    print(f"  Signed URL (1 hr expiry):")
    print(f"    {url1[:120]}{'...' if len(url1) > 120 else ''}")
    print("  → Open in a browser to verify the PDF in Supabase.")

    step("Get signed URL for the milestone_based contract PDF")
    url2 = extract(get(f"/contracts/{contract_ms_id}/pdf-url", tok_client2)).get("pdf_url", "")
    print(f"  Signed URL (1 hr expiry):")
    print(f"    {url2[:120]}{'...' if len(url2) > 120 else ''}")
    print("  → Open in a browser to verify the PDF in Supabase.")

    # ── 9. Contract list views ────────────────────────────────────────────────

    step("List all contracts visible to the freelancer")
    fl_contracts = extract(get("/contracts", tok_freelancer))
    if isinstance(fl_contracts, list):
        print(f"  {len(fl_contracts)} contract(s):")
        for c in fl_contracts:
            print(
                f"    [{c.get('status', '?').upper():10}] "
                f"{c.get('contract_title', '?'):<47} "
                f"${c.get('agreed_budget', '?')} {c.get('budget_currency', '')}  "
                f"({c.get('payment_structure', '?')})"
            )

    step("List all contracts visible to Client 1")
    c1_contracts = extract(get("/contracts", tok_client1))
    if isinstance(c1_contracts, list):
        print(f"  {len(c1_contracts)} contract(s):")
        for c in c1_contracts:
            print(f"    [{c.get('status', '?').upper():10}] {c.get('contract_title', '?')}")

    step("List all contracts visible to Client 2")
    c2_contracts = extract(get("/contracts", tok_client2))
    if isinstance(c2_contracts, list):
        print(f"  {len(c2_contracts)} contract(s):")
        for c in c2_contracts:
            print(f"    [{c.get('status', '?').upper():10}] {c.get('contract_title', '?')}")

    # ── Done ─────────────────────────────────────────────────────────────────

    print("\n" + "="*60)
    print("  Contract walkthrough complete.")
    print("="*60)
    print(f"\n  Contracts created this run:")
    print(f"    full_payment   : {contract_fp_id}")
    print(f"    milestone_based: {contract_ms_id}")
    print()
    print(f"  PDF verification (Supabase 'contract-assets' bucket):")
    print(f"    full_payment    path : {pdf_path_fp or '(not generated)'}")
    print(f"    milestone_based path : {pdf_path_ms or '(not generated)'}")
    print()
    print("  Use the signed URLs printed above to download and inspect the PDFs.")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
