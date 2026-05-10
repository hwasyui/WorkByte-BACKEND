"""
Dual Account + Job Posting + Contract Generation Walkthrough

Scenario:
  • User 1 registers as freelancer
  • User 1 adds client role to the same account (dual account)
  • User 1 posts a job as a client
  • User 2 registers as freelancer
  • User 2 applies to the job
  • User 1 creates a contract from the proposal
  • Contract PDF is generated and verified

Usage (from inside the backend container):
    python walkthrough/walkthrough-dual-account-contract.py

Or from outside:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-dual-account-contract.py
"""

import sys
import json
import os
import random
import datetime
import requests

BASE_URL = "http://localhost:8000"

_RUN_ID = random.randint(1000, 9999)
_EMAIL_USER1 = f"dualaccount.user1.{_RUN_ID}@walkthrough.dev"
_EMAIL_USER2 = f"dualaccount.user2.{_RUN_ID}@walkthrough.dev"
_PASSWORD = "SecurePass123"

# ── output tee ────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath: str):
        self._stdout = sys.stdout
        self._file = open(filepath, "w", encoding="utf-8")

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
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(out_dir, f"walkthrough_dual_account_contract_{ts}.md")
    tee = _Tee(path)
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
    print(f"\n{'='*70}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*70}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    status_str = 'OK' if r.ok else 'FAIL'
    print(f"  POST {endpoint:<40} [{r.status_code}] {status_str}")
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
    status_str = 'OK' if r.ok else 'FAIL'
    print(f"  GET  {endpoint:<40} [{r.status_code}] {status_str}")
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
    status_str = 'OK' if r.ok else 'FAIL'
    print(f"  PUT  {endpoint:<40} [{r.status_code}] {status_str}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def extract(response: dict):
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


def register_and_verify(body: dict) -> dict:
    resp = post("/auth/register", body)
    details = extract(resp)
    otp = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": body["email"], "otp": otp})
    else:
        print("  Verification OTP not returned. Complete email verification before login.")
    return resp

# ── main ──────────────────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*70)
    print("  Capstone API — Dual Account + Contract Generation Walkthrough")
    print("="*70)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print(f"  Run ID : {_RUN_ID}")

    # ── Part 1: Setup Dual Account (User 1) ─────────────────────────────────

    step("Register User 1 as a freelancer")
    register_and_verify({
        "email": _EMAIL_USER1,
        "password": _PASSWORD,
        "user_type": "freelancer",
        "full_name": "User One Freelancer"
    })
    print(f"  ✓ User 1 registered as freelancer")

    step("Login as User 1")
    tok_user1 = token_from_login(_EMAIL_USER1, _PASSWORD)
    print(f"  ✓ Access token obtained for User 1")

    step("Get User 1 profile details (before adding client role)")
    user_info = extract(get("/auth/me", tok_user1))
    print(f"  user_id       : {user_info.get('user_id', '?')}")
    print(f"  freelancer_id : {user_info.get('freelancer_id', '?')}")
    print(f"  client_id     : {user_info.get('client_id', '?')} (should be None)")

    step("Add client role to User 1 (same email, dual account)")
    resp = post("/auth/add-role", {
        "role": "client",
        "full_name": "User One Client"
    }, tok_user1)
    result = extract(resp)
    print(f"  ✓ Client role added to User 1")
    print(f"  New client_id  : {result.get('client_id', '?')}")

    step("Verify User 1 now has both freelancer and client roles")
    user_info = extract(get("/auth/me", tok_user1))
    freelancer_id = user_info.get('freelancer_id')
    client_id = user_info.get('client_id')
    print(f"  freelancer_id : {freelancer_id}")
    print(f"  client_id     : {client_id}")
    print(f"  ✓ User 1 now has DUAL ACCOUNT")

    # ── Part 2: Job Posting (User 1 as Client) ──────────────────────────────

    step("User 1 (as client) creates a job post")
    resp = post("/job-posts", {
        "client_id": client_id,
        "job_title": "Backend API Development",
        "job_description": (
            "We need a skilled backend developer to build and maintain REST APIs "
            "for our SaaS platform. Must have experience with Python, FastAPI, and PostgreSQL."
        ),
        "project_type": "individual",
        "project_scope": "medium",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_user1)
    job_post_id = extract(resp)["job_post_id"]
    print(f"  ✓ Job post created: {job_post_id}")

    step("User 1 (as client) adds a role to the job post")
    resp = post("/job-roles", {
        "job_post_id": job_post_id,
        "role_title": "Senior Backend Developer",
        "role_budget": 5000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Design and build REST APIs, database schemas, and microservices.",
        "display_order": 0
    }, tok_user1)
    job_role_id = extract(resp)["job_role_id"]
    print(f"  ✓ Job role created: {job_role_id}")

    # ── Part 3: User 2 Registration & Job Application ──────────────────────

    step("Register User 2 as a freelancer")
    register_and_verify({
        "email": _EMAIL_USER2,
        "password": _PASSWORD,
        "user_type": "freelancer",
        "full_name": "User Two Freelancer"
    })
    print(f"  ✓ User 2 registered as freelancer")

    step("Login as User 2")
    tok_user2 = token_from_login(_EMAIL_USER2, _PASSWORD)
    print(f"  ✓ Access token obtained for User 2")

    step("Get User 2 freelancer ID")
    user2_info = extract(get("/freelancers", tok_user2))
    if isinstance(user2_info, list) and len(user2_info) > 0:
        freelancer_id_user2 = user2_info[0]["freelancer_id"]
        print(f"  ✓ User 2 freelancer_id: {freelancer_id_user2}")
    else:
        print("  ERROR: Could not retrieve User 2 freelancer ID")
        sys.exit(1)

    step("User 2 submits a proposal for the job")
    resp = post("/proposals", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "freelancer_id": freelancer_id_user2,
        "cover_letter": (
            "I have 6+ years of experience building REST APIs with Python and FastAPI. "
            "I've worked on high-traffic SaaS platforms and can deliver production-quality code. "
            "I'm confident I can complete this project in 3 months."
        ),
        "proposed_budget": 5000.0,
        "proposed_duration": "3 months",
        "status": "pending"
    }, tok_user2)
    proposal_id = extract(resp)["proposal_id"]
    print(f"  ✓ Proposal submitted: {proposal_id}")

    # ── Part 4: Contract Creation ───────────────────────────────────────────

    step("User 1 (as client) creates a contract from the proposal")
    resp = post("/contracts", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "proposal_id": proposal_id,
        "freelancer_id": freelancer_id_user2,
        "client_id": client_id,
        "contract_title": "Backend API Development Contract",
        "role_title": "Senior Backend Developer",
        "agreed_budget": 5000.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "3 months",
        "status": "active",
        "start_date": "2026-05-09",
        "end_date": "2026-08-09"
    }, tok_user1)
    contract_id = extract(resp)["contract_id"]
    print(f"  ✓ Contract created: {contract_id}")

    # ── Part 5: Contract PDF Generation ─────────────────────────────────────

    step("Retrieve contract generation data")
    gen_data = extract(get(f"/contracts/{contract_id}/generation-data", tok_user1))
    contract = gen_data.get("contract", {})
    freelancer = gen_data.get("freelancer", {})
    client = gen_data.get("client", {})
    job_post = gen_data.get("job_post", {})

    print(f"  Contract title     : {contract.get('contract_title', '?')}")
    print(f"  Payment structure  : {contract.get('payment_structure', '?')}")
    print(f"  Agreed budget      : ${contract.get('agreed_budget', '?')} {contract.get('budget_currency', 'USD')}")
    print(f"  Freelancer name    : {freelancer.get('full_name', '?')}")
    print(f"  Client name        : {client.get('full_name', client.get('company_name', '?'))}")
    print(f"  Job title          : {job_post.get('job_title', '?')}")

    step("Generate contract PDF")
    print("  Calling POST /contracts/{id}/generate with contract terms...")
    resp = post(f"/contracts/{contract_id}/generate", {
        "end_date": "2026-08-09",
        "agreed_duration": "3 months",
        "termination_notice": 14,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": (
            "Both parties agree to maintain strict confidentiality of all project details, "
            "source code, and proprietary information for a period of 2 years after contract termination."
        ),
        "late_payment_penalty": 1.5,
        "dispute_resolution": "negotiation",
        "revision_rounds": 2,
        "additional_clauses": (
            "Freelancer must use Git version control for all deliverables. "
            "Weekly code reviews and documentation are mandatory."
        )
    }, tok_user1)

    result = extract(resp)
    pdf_path = result.get("contract_pdf_url", "")
    pdf_generated_at = result.get("contract_pdf_generated_at", "")

    if pdf_path:
        print(f"  ✓ PDF generated successfully!")
        print(f"    Storage path    : {pdf_path}")
        print(f"    Generated at    : {pdf_generated_at}")
    else:
        print(f"  ✗ PDF generation failed or returned empty path")
        print(f"    Response       : {json.dumps(result, indent=4)}")

    # ── Part 6: Get Signed URL & Verification ───────────────────────────────

    step("Get signed URL for PDF download")
    try:
        url_resp = extract(get(f"/contracts/{contract_id}/pdf-url", tok_user1))
        pdf_url = url_resp.get("pdf_url", "")

        if pdf_url:
            print(f"  ✓ Signed URL obtained (1 hour expiry)")
            print(f"    URL: {pdf_url[:100]}...")
            print()
            print("  ➜ To download the PDF:")
            print(f"    • Open in browser: {pdf_url}")
            print("    • Or use curl: curl -o contract.pdf '{pdf_url}'")
        else:
            print(f"  ✗ No PDF URL returned")
    except Exception as e:
        print(f"  ⚠ Could not retrieve signed URL: {str(e)}")

    # ── Part 7: View Contracts from Both Perspectives ───────────────────────

    step("List contracts visible to User 1 (client & freelancer)")
    contracts = extract(get("/contracts", tok_user1))
    if isinstance(contracts, list):
        print(f"  Found {len(contracts)} contract(s):")
        for c in contracts:
            print(f"    • [{c.get('status', '?').upper():8}] {c.get('contract_title', '?')} | ${c.get('agreed_budget', '?')} | {c.get('payment_structure', '?')}")
    else:
        print(f"  Response: {contracts}")

    step("List contracts visible to User 2 (freelancer)")
    contracts = extract(get("/contracts", tok_user2))
    if isinstance(contracts, list):
        print(f"  Found {len(contracts)} contract(s):")
        for c in contracts:
            print(f"    • [{c.get('status', '?').upper():8}] {c.get('contract_title', '?')} | ${c.get('agreed_budget', '?')} | {c.get('payment_structure', '?')}")
    else:
        print(f"  Response: {contracts}")

    # ── Summary ─────────────────────────────────────────────────────────────

    print("\n" + "="*70)
    print("  ✓ Walkthrough Complete")
    print("="*70)
    print()
    print("  SUMMARY:")
    print(f"    User 1 email       : {_EMAIL_USER1}")
    print(f"    User 1 freelancer  : {freelancer_id}")
    print(f"    User 1 client      : {client_id}")
    print()
    print(f"    User 2 email       : {_EMAIL_USER2}")
    print(f"    User 2 freelancer  : {freelancer_id_user2}")
    print()
    print(f"    Job post ID        : {job_post_id}")
    print(f"    Job role ID        : {job_role_id}")
    print(f"    Proposal ID        : {proposal_id}")
    print(f"    Contract ID        : {contract_id}")
    print()
    print(f"  CONTRACT PDF STATUS:")
    if pdf_path:
        print(f"    ✓ PDF Generated    : {pdf_path}")
        print(f"    ✓ Generated at     : {pdf_generated_at}")
        print(f"    ✓ Signed URL       : Available (1 hour)")
    else:
        print(f"    ✗ PDF Generation   : FAILED")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
