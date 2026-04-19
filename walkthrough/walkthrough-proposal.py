"""
Proposal + proposal file upload walkthrough.

Tests:
1. register/login fresh client and freelancer users
2. create a job post and role
3. submit a proposal
4. upload multiple proposal files through POST /proposal-files
5. verify uploaded file metadata through GET endpoints

Usage:
    python walkthrough/walkthrough-proposal.py
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"


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
    filepath = os.path.join(out_dir, f"walkthrough_proposal_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")


_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'=' * 60}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 60}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    print(f"  POST {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        sys.exit(1)
    return data


def get(endpoint: str, token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, timeout=90)
    data = r.json()
    print(f"  GET  {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=2)}")
        sys.exit(1)
    return data


def post_multipart(endpoint: str, files: list[tuple[str, tuple]], data: dict, token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data, headers=headers, timeout=120)
    payload = r.json()
    print(f"  POST {endpoint}  [{r.status_code}] {'OK' if r.ok else 'FAIL'}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(payload, indent=2)}")
        sys.exit(1)
    return payload


def extract(response: dict):
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


def run():
    tee, out_path = _start_tee()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    client_email = f"proposal.client.{ts}@walkthrough.dev"
    freelancer_email = f"proposal.freelancer.{ts}@walkthrough.dev"
    password = "SecurePass123"

    print("\n" + "=" * 60)
    print("  Capstone API — Proposal File Walkthrough")
    print("=" * 60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    step("Register a fresh client user")
    post("/auth/register", {
        "email": client_email,
        "password": password,
        "user_type": "client",
        "full_name": "Proposal File Client"
    })

    step("Register a fresh freelancer user")
    post("/auth/register", {
        "email": freelancer_email,
        "password": password,
        "user_type": "freelancer",
        "full_name": "Proposal File Freelancer"
    })

    step("Log in both users")
    client_token = token_from_login(client_email, password)
    freelancer_token = token_from_login(freelancer_email, password)
    client_id = extract(get("/clients", client_token))[0]["client_id"]
    freelancer_id = extract(get("/freelancers", freelancer_token))[0]["freelancer_id"]
    print(f"  client_id    : {client_id}")
    print(f"  freelancer_id: {freelancer_id}")

    step("Client creates a fresh job post")
    resp = post("/job-posts", {
        "client_id": client_id,
        "job_title": "Walkthrough Proposal Upload Test",
        "job_description": "Need a freelancer to test proposal file uploads.",
        "project_type": "individual",
        "project_scope": "small",
        "estimated_duration": "10 days",
        "experience_level": "entry",
        "status": "active"
    }, client_token)
    job_post_id = extract(resp)["job_post_id"]
    print(f"  job_post_id: {job_post_id}")

    step("Client adds a role to the job post")
    resp = post("/job-roles", {
        "job_post_id": job_post_id,
        "role_title": "API Tester",
        "role_budget": 500.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Test proposal upload flow and verify stored file URLs.",
        "display_order": 0
    }, client_token)
    job_role_id = extract(resp)["job_role_id"]
    print(f"  job_role_id: {job_role_id}")

    step("Freelancer submits a proposal")
    resp = post("/proposals", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "freelancer_id": freelancer_id,
        "cover_letter": "I can test and validate the proposal upload flow end to end.",
        "proposed_budget": 500.0,
        "proposed_duration": "10 days",
        "status": "pending"
    }, freelancer_token)
    proposal_id = extract(resp)["proposal_id"]
    print(f"  proposal_id: {proposal_id}")

    pdf_one = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Angelica Suti Whiharto_CV.pdf")
    pdf_two = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Intan Kumala Pasya_CV.pdf")
    image_one = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windah.jpeg")
    for path in (pdf_one, pdf_two, image_one):
        if not os.path.isfile(path):
            print(f"  ERROR: file not found: {path}")
            sys.exit(1)

    step("Upload three proposal files at once to POST /proposal-files")
    with open(pdf_one, "rb") as f1, open(pdf_two, "rb") as f2, open(image_one, "rb") as f3:
        files = [
            ("files", ("proposal-cover.pdf", f1, "application/pdf")),
            ("files", ("portfolio-proof.pdf", f2, "application/pdf")),
            ("files", ("proposal-photo.jpeg", f3, "image/jpeg")),
        ]
        resp = post_multipart("/proposal-files", files=files, data={"proposal_id": proposal_id}, token=freelancer_token)
    created_files = extract(resp)
    print(f"  Uploaded files: {len(created_files)}")
    for item in created_files:
        print(f"    {item['proposal_file_id']} | {item['file_name']} | {item['file_url']}")

    step("Verify proposal files by proposal")
    proposal_files = extract(get(f"/proposal-files/proposal/{proposal_id}", freelancer_token))
    print(f"  Files found for proposal: {len(proposal_files)}")
    for item in proposal_files:
        print(f"    {item['file_name']} | {item['file_type']} | {item['file_url']}")

    if len(proposal_files) < 3:
        print("  ERROR: expected at least 3 uploaded proposal files")
        sys.exit(1)

    step("Fetch one proposal file directly")
    sample_id = created_files[0]["proposal_file_id"]
    sample = extract(get(f"/proposal-files/{sample_id}", freelancer_token))
    print(f"  proposal_file_id: {sample['proposal_file_id']}")
    print(f"  file_name       : {sample['file_name']}")
    print(f"  file_url        : {sample['file_url']}")

    step("Walkthrough summary")
    print("  Proposal file upload flow is working.")
    print(f"  Created proposal: {proposal_id}")
    print(f"  Uploaded file count: {len(created_files)}")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
