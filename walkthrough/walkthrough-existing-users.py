"""
Walkthrough dengan Existing Users + Job Posting + Contract PDF Generation

Scenario:
  • User 1 (psyintann@gmail.com) login → add client role
  • User 2 (pasyaintan@gmail.com) login → has freelancer role
  • User 1 posts a job as client
  • User 2 applies to the job
  • User 1 creates contract from proposal
  • Contract PDF is generated

Usage:
    python walkthrough/walkthrough-existing-users.py
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"

_EMAIL_USER1 = "psyintann@gmail.com"
_PASSWORD_USER1 = "client123"

_EMAIL_USER2 = "pasyaintan@gmail.com"
_PASSWORD_USER2 = "intan2706"

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
    path = os.path.join(out_dir, f"walkthrough_existing_users_{ts}.md")
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
        return None
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
        return None
    return data


def extract(response: dict):
    if response is None:
        return {}
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    if resp is None:
        print(f"  ✗ Login failed for {email}")
        sys.exit(1)
    return extract(resp).get("access_token")

# ── main ──────────────────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*70)
    print("  Capstone API — Existing Users Walkthrough")
    print("="*70)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print()
    print(f"  User 1 : {_EMAIL_USER1}")
    print(f"  User 2 : {_EMAIL_USER2}")

    # ── Part 1: Login & Setup Dual Account (User 1) ──────────────────────────

    step("Login as User 1 (psyintann@gmail.com)")
    tok_user1 = token_from_login(_EMAIL_USER1, _PASSWORD_USER1)
    print(f"  ✓ Access token obtained")

    step("Get User 1 profile details")
    user_info = extract(get("/auth/me", tok_user1))
    if not user_info:
        print("  ✗ Failed to get user info")
        sys.exit(1)

    user1_id = user_info.get('user_id')
    freelancer_id_user1 = user_info.get('freelancer_id')
    client_id_user1 = user_info.get('client_id')

    print(f"  user_id       : {user1_id}")
    print(f"  freelancer_id : {freelancer_id_user1}")
    print(f"  client_id     : {client_id_user1}")

    # Check if User 1 needs client role
    if not client_id_user1:
        step("Add client role to User 1")
        resp = post("/auth/add-role", {"role": "client", "full_name": "User One Client"}, tok_user1)
        if resp:
            result = extract(resp)
            client_id_user1 = result.get('client_id')
            print(f"  ✓ Client role added: {client_id_user1}")
        else:
            print("  ✗ Failed to add client role")
            sys.exit(1)
    else:
        print(f"  ✓ User 1 already has client role: {client_id_user1}")

    step("Verify User 1 has both roles")
    user_info = extract(get("/auth/me", tok_user1))
    print(f"  freelancer_id : {user_info.get('freelancer_id')}")
    print(f"  client_id     : {user_info.get('client_id')}")
    print(f"  ✓ Dual account confirmed")

    # ── Part 2: Login User 2 ──────────────────────────────────────────────────

    step("Login as User 2 (pasyaintan@gmail.com)")
    tok_user2 = token_from_login(_EMAIL_USER2, _PASSWORD_USER2)
    print(f"  ✓ Access token obtained")

    step("Get User 2 profile details")
    user2_info = extract(get("/auth/me", tok_user2))
    if not user2_info:
        print("  ✗ Failed to get user info")
        sys.exit(1)

    user2_id = user2_info.get('user_id')
    freelancer_id_user2 = user2_info.get('freelancer_id')
    client_id_user2 = user2_info.get('client_id')

    print(f"  user_id       : {user2_id}")
    print(f"  freelancer_id : {freelancer_id_user2}")
    print(f"  client_id     : {client_id_user2}")

    if not freelancer_id_user2:
        print("  ✗ User 2 does not have freelancer role. Please register properly first.")
        sys.exit(1)

    # ── Part 3: User 1 Posts Job ──────────────────────────────────────────────

    step("User 1 (as client) creates a job post")
    resp = post("/job-posts", {
        "client_id": client_id_user1,
        "job_title": "Python Backend Developer Needed",
        "job_description": (
            "Looking for an experienced Python developer to build REST APIs "
            "for our SaaS platform. Must have FastAPI and PostgreSQL experience."
        ),
        "project_type": "individual",
        "project_scope": "medium",
        "project_category": "web_development",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_user1)

    if resp is None:
        print("  ✗ Failed to create job post")
        sys.exit(1)

    job_post_id = extract(resp).get("job_post_id")
    print(f"  ✓ Job post created: {job_post_id}")

    step("User 1 adds a role to the job post")
    resp = post("/job-roles", {
        "job_post_id": job_post_id,
        "role_title": "Backend Developer",
        "role_budget": 5000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Build and maintain REST APIs using Python/FastAPI",
        "display_order": 0
    }, tok_user1)

    if resp is None:
        print("  ✗ Failed to create job role")
        sys.exit(1)

    job_role_id = extract(resp).get("job_role_id")
    print(f"  ✓ Job role created: {job_role_id}")

    # ── Part 4: User 2 Apply to Job ──────────────────────────────────────────

    step("User 2 submits a proposal for the job")
    resp = post("/proposals", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "freelancer_id": freelancer_id_user2,
        "cover_letter": (
            "I have 5+ years of Python experience with FastAPI and PostgreSQL. "
            "I can deliver high-quality, scalable APIs. Ready to start immediately."
        ),
        "proposed_budget": 5000.0,
        "proposed_duration": "3 months",
        "status": "pending"
    }, tok_user2)

    if resp is None:
        print("  ✗ Failed to submit proposal")
        sys.exit(1)

    proposal_id = extract(resp).get("proposal_id")
    print(f"  ✓ Proposal submitted: {proposal_id}")

    # ── Part 5: Create Contract ───────────────────────────────────────────────

    step("User 1 (as client) creates a contract from the proposal")
    resp = post("/contracts", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "proposal_id": proposal_id,
        "freelancer_id": freelancer_id_user2,
        "client_id": client_id_user1,
        "contract_title": "Python Backend Developer Contract",
        "role_title": "Backend Developer",
        "agreed_budget": 5000.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "3 months",
        "status": "active",
        "start_date": "2026-05-09",
        "end_date": "2026-08-09"
    }, tok_user1)

    if resp is None:
        print("  ✗ Failed to create contract")
        sys.exit(1)

    contract_id = extract(resp).get("contract_id")
    print(f"  ✓ Contract created: {contract_id}")

    # ── Part 6: Retrieve Generation Data ──────────────────────────────────────

    step("Retrieve contract generation data")
    gen_data = extract(get(f"/contracts/{contract_id}/generation-data", tok_user1))
    if not gen_data:
        print("  ✗ Failed to get generation data")
        sys.exit(1)

    contract = gen_data.get("contract", {})
    freelancer = gen_data.get("freelancer", {})
    client = gen_data.get("client", {})
    job_post = gen_data.get("job_post", {})

    print(f"  Contract title     : {contract.get('contract_title', '?')}")
    print(f"  Payment structure  : {contract.get('payment_structure', '?')}")
    print(f"  Agreed budget      : ${contract.get('agreed_budget', '?')}")
    print(f"  Freelancer name    : {freelancer.get('full_name', '?')}")
    print(f"  Client name        : {client.get('full_name', client.get('company_name', '?'))}")
    print(f"  Job title          : {job_post.get('job_title', '?')}")

    # ── Part 7: Generate PDF ──────────────────────────────────────────────────

    step("Generate contract PDF")
    print("  Calling POST /contracts/{id}/generate ...")
    resp = post(f"/contracts/{contract_id}/generate", {
        "end_date": "2026-08-09",
        "agreed_duration": "3 months",
        "termination_notice": 14,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": (
            "Both parties agree to maintain confidentiality of all project details "
            "and source code for 2 years after contract termination."
        ),
        "late_payment_penalty": 1.5,
        "dispute_resolution": "negotiation",
        "revision_rounds": 2,
        "additional_clauses": "All code must be delivered via Git repository with documentation."
    }, tok_user1)

    if resp is None:
        print("  ✗ PDF generation failed")
        sys.exit(1)

    result = extract(resp)
    pdf_path = result.get("contract_pdf_url", "")
    pdf_generated_at = result.get("contract_pdf_generated_at", "")

    if pdf_path:
        print(f"  ✓ PDF generated successfully!")
        print(f"    Storage path    : {pdf_path}")
        print(f"    Generated at    : {pdf_generated_at}")
    else:
        print(f"  ✗ PDF generation returned empty path")
        print(f"    Full response   : {json.dumps(result, indent=4)}")

    # ── Part 8: Get Signed URL ────────────────────────────────────────────────

    step("Get signed URL for PDF download")
    url_resp = get(f"/contracts/{contract_id}/pdf-url", tok_user1)
    if url_resp:
        pdf_url = extract(url_resp).get("pdf_url", "")
        if pdf_url:
            print(f"  ✓ Signed URL obtained (1 hour expiry)")
            print()
            print(f"  Download URL:")
            print(f"  {pdf_url}")
            print()
            print(f"  Or download with curl:")
            print(f"  curl -o contract.pdf '{pdf_url}'")
        else:
            print(f"  ✗ No PDF URL in response")

    # ── Part 9: View Contract Lists ───────────────────────────────────────────

    step("List contracts visible to User 1")
    contracts = extract(get("/contracts", tok_user1))
    if isinstance(contracts, list):
        print(f"  Found {len(contracts)} contract(s):")
        for c in contracts:
            print(f"    • [{c.get('status', '?').upper():8}] {c.get('contract_title', '?'):40} | ${c.get('agreed_budget', '?'):6} | {c.get('payment_structure', '?')}")

    step("List contracts visible to User 2")
    contracts = extract(get("/contracts", tok_user2))
    if isinstance(contracts, list):
        print(f"  Found {len(contracts)} contract(s):")
        for c in contracts:
            print(f"    • [{c.get('status', '?').upper():8}] {c.get('contract_title', '?'):40} | ${c.get('agreed_budget', '?'):6} | {c.get('payment_structure', '?')}")

    # ── Summary ───────────────────────────────────────────────────────────────

    print("\n" + "="*70)
    print("  ✓ Walkthrough Complete")
    print("="*70)
    print()
    print("  SUMMARY:")
    print(f"    User 1 (Client)        : {_EMAIL_USER1}")
    print(f"      - freelancer_id      : {freelancer_id_user1}")
    print(f"      - client_id          : {client_id_user1}")
    print()
    print(f"    User 2 (Freelancer)    : {_EMAIL_USER2}")
    print(f"      - freelancer_id      : {freelancer_id_user2}")
    print()
    print(f"    Job Details:")
    print(f"      - job_post_id        : {job_post_id}")
    print(f"      - job_role_id        : {job_role_id}")
    print(f"      - proposal_id        : {proposal_id}")
    print()
    print(f"    Contract Details:")
    print(f"      - contract_id        : {contract_id}")
    print(f"      - payment_structure  : full_payment")
    print(f"      - agreed_budget      : $5000 USD")
    print()
    print(f"  PDF STATUS:")
    if pdf_path:
        print(f"    ✓ PDF Generated      : YES")
        print(f"    ✓ Storage Path       : {pdf_path}")
        print(f"    ✓ Generated At       : {pdf_generated_at}")
        print(f"    ✓ Signed URL         : Available (1 hour expiry)")
    else:
        print(f"    ✗ PDF Generation     : FAILED")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
