"""
Contract walkthrough — assumes walkthrough.py has already been run.

Logs in as the existing walkthrough users, creates fresh job posts + proposals,
then exercises the full contract lifecycle:

  • full_payment contract  → PDF generated → signed URL returned
  • milestone_based contract → PDF generated → milestone lifecycle (in_progress →
    completed → paid → freelancer confirms)

Usage (from inside the backend container):
    python walkthrough/walkthrough-contract.py

Or from outside:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-contract.py
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"

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
    print()
    print("  Prerequisite: walkthrough.py must have been run at least once")
    print("  so that Budi Santoso, TechStartup Inc., and DataCorp Solutions exist.")

    # ── 1. Log in as existing walkthrough users ───────────────────────────────

    step("Log in as existing walkthrough users")
    tok_freelancer = token_from_login("budi.santoso@walkthrough.dev", "SecurePass123")
    tok_client1    = token_from_login("techstartup@walkthrough.dev",  "SecurePass123")
    tok_client2    = token_from_login("datacorp@walkthrough.dev",     "SecurePass123")
    print("  All tokens obtained.")

    # ── 2. Resolve profile IDs ────────────────────────────────────────────────

    step("Resolve freelancer and client profile IDs")
    fid  = extract(get("/freelancers", tok_freelancer))[0]["freelancer_id"]
    cid1 = extract(get("/clients", tok_client1))[0]["client_id"]
    cid2 = extract(get("/clients", tok_client2))[0]["client_id"]
    print(f"  freelancer_id : {fid}")
    print(f"  client1_id    : {cid1}")
    print(f"  client2_id    : {cid2}")

    # ── 3. Create fresh job posts (so we are not blocked by filled ones) ──────

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

    step("Client 1 — Add a role + required skills to the full_payment job")
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

    # ── 4. Budi submits proposals ─────────────────────────────────────────────

    step("Budi submits a proposal for the full_payment job")
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

    step("Budi submits a proposal for the milestone_based job")
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
    print("  (proposal auto-accepted, job post auto-filled)")

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
    print(f"  existing milestones: {len(gen1['milestones'])}")

    step("Inspect auto-filled generation data for the milestone_based contract")
    gen2 = extract(get(f"/contracts/{contract_ms_id}/generation-data", tok_client2))
    c2   = gen2["contract"]
    print(f"  contract_title     : {c2['contract_title']}")
    print(f"  payment_structure  : {c2['payment_structure']}")
    print(f"  agreed_budget      : ${c2['agreed_budget']} {c2.get('budget_currency', 'USD')}")
    print(f"  existing milestones: {len(gen2['milestones'])}")

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

    step("Generate milestone_based contract PDF (3 milestones) → upload to Supabase")
    print("  Calling POST /contracts/{id}/generate with milestones ...")
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
        "additional_clauses": "Freelancer delivers weekly progress reports every Friday.",
        "milestones": [
            {
                "milestone_title": "Phase 1 — Ingestion Layer",
                "milestone_description": "Build REST API ingestion layer and raw data store.",
                "milestone_amount": 1200.0,
                "milestone_percentage": 30.0,
                "milestone_order": 1,
                "due_date": "2026-05-31"
            },
            {
                "milestone_title": "Phase 2 — Transformation & Load",
                "milestone_description": "Implement transform logic and PostgreSQL loading pipeline.",
                "milestone_amount": 1600.0,
                "milestone_percentage": 40.0,
                "milestone_order": 2,
                "due_date": "2026-07-15"
            },
            {
                "milestone_title": "Phase 3 — Testing & Handoff",
                "milestone_description": "End-to-end tests, documentation, and project handover.",
                "milestone_amount": 1200.0,
                "milestone_percentage": 30.0,
                "milestone_order": 3,
                "due_date": "2026-08-31"
            }
        ]
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

    # ── 9. List and inspect milestones ───────────────────────────────────────

    step("List milestones created by the generate endpoint")
    milestones_raw = extract(get(f"/contract-milestones/contract/{contract_ms_id}", tok_freelancer))
    milestones = milestones_raw if isinstance(milestones_raw, list) else []
    print(f"  {len(milestones)} milestone(s):")
    for ms in milestones:
        print(
            f"    [{ms.get('status', '?').upper():10}] "
            f"{ms.get('milestone_title', '?'):<42} "
            f"${ms.get('milestone_budget', '?')}  due {ms.get('due_date', '?')}"
        )
    milestone1_id = milestones[0]["milestone_id"] if milestones else None

    # ── 10. Milestone lifecycle ───────────────────────────────────────────────

    if not milestone1_id:
        print("\n  WARNING: no milestones found — skipping lifecycle steps.")
    else:
        step("Client 2 moves Milestone 1 → in_progress")
        put(f"/contract-milestones/{milestone1_id}", {"status": "in_progress"}, tok_client2)
        print("  Milestone 1 is now in_progress (client_approved = True).")

        step("Client 2 marks Milestone 1 → completed (work accepted)")
        put(f"/contract-milestones/{milestone1_id}", {"status": "completed"}, tok_client2)
        print("  Milestone 1 marked completed by client.")

        step("Client 2 marks Milestone 1 → paid (releases payment)")
        put(f"/contract-milestones/{milestone1_id}", {"status": "paid"}, tok_client2)
        print("  Payment request sent to freelancer.")

        step("Freelancer confirms receipt of Milestone 1 payment")
        post(f"/contract-milestones/{milestone1_id}/confirm-payment", {}, tok_freelancer)
        print("  Freelancer confirmed payment — milestone fully settled.")

        step("Verify final state of Milestone 1")
        ms1 = extract(get(f"/contract-milestones/{milestone1_id}", tok_freelancer))
        print(f"  status                   : {ms1.get('status')}")
        print(f"  client_approved          : {ms1.get('client_approved')}")
        print(f"  payment_requested        : {ms1.get('payment_requested')}")
        print(f"  freelancer_confirmed_paid: {ms1.get('freelancer_confirmed_paid')}")
        print(f"  payment_released         : {ms1.get('payment_released')}")

    # ── 11. Contract list views ───────────────────────────────────────────────

    step("List all contracts visible to Budi")
    budi_contracts = extract(get("/contracts", tok_freelancer))
    if isinstance(budi_contracts, list):
        print(f"  {len(budi_contracts)} contract(s):")
        for c in budi_contracts:
            print(
                f"    [{c.get('status', '?').upper():10}] "
                f"{c.get('contract_title', '?'):<47} "
                f"${c.get('agreed_budget', '?')} {c.get('budget_currency', '')}  "
                f"({c.get('payment_structure', '?')})"
            )

    step("List all contracts visible to Client 1 (TechStartup Inc.)")
    c1_contracts = extract(get("/contracts", tok_client1))
    if isinstance(c1_contracts, list):
        print(f"  {len(c1_contracts)} contract(s):")
        for c in c1_contracts:
            print(f"    [{c.get('status', '?').upper():10}] {c.get('contract_title', '?')}")

    step("List all contracts visible to Client 2 (DataCorp Solutions)")
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
    print(f"    full_payment   path : {pdf_path_fp or '(not generated)'}")
    print(f"    milestone_based path: {pdf_path_ms or '(not generated)'}")
    print()
    print("  Use the signed URLs printed above to download and inspect the PDFs.")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
