"""
Job post + job file upload walkthrough.

Tests:
1. register/login a fresh client user
2. create a job post
3. upload multiple job files through POST /job-files
4. verify uploaded file metadata through GET endpoints

Usage:
    python walkthrough/walkthrough-job.py
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
    filepath = os.path.join(out_dir, f"walkthrough_job_{ts}.md")
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
    email = f"job.client.{ts}@walkthrough.dev"
    password = "SecurePass123"

    print("\n" + "=" * 60)
    print("  Capstone API — Job File Walkthrough")
    print("=" * 60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    step("Register a fresh client user")
    post("/auth/register", {
        "email": email,
        "password": password,
        "user_type": "client",
        "full_name": "Job File Client"
    })

    step("Log in as the client")
    token = token_from_login(email, password)
    client_id = extract(get("/clients", token))[0]["client_id"]
    print(f"  client_id: {client_id}")

    step("Create a fresh job post")
    resp = post("/job-posts", {
        "client_id": client_id,
        "job_title": "Walkthrough Job Upload Test",
        "job_description": "Testing job attachment uploads through multipart form data.",
        "project_type": "individual",
        "project_scope": "small",
        "estimated_duration": "2 weeks",
        "experience_level": "entry",
        "status": "active"
    }, token)
    job_post_id = extract(resp)["job_post_id"]
    print(f"  job_post_id: {job_post_id}")

    pdf_one = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Angelica Suti Whiharto_CV.pdf")
    pdf_two = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Intan Kumala Pasya_CV.pdf")
    image_one = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windah.jpeg")
    for path in (pdf_one, pdf_two, image_one):
        if not os.path.isfile(path):
            print(f"  ERROR: file not found: {path}")
            sys.exit(1)

    step("Upload three job files at once to POST /job-files")
    with open(pdf_one, "rb") as f1, open(pdf_two, "rb") as f2, open(image_one, "rb") as f3:
        files = [
            ("files", ("requirements.pdf", f1, "application/pdf")),
            ("files", ("brief.pdf", f2, "application/pdf")),
            ("files", ("reference-photo.jpeg", f3, "image/jpeg")),
        ]
        resp = post_multipart("/job-files", files=files, data={"job_post_id": job_post_id}, token=token)
    created_files = extract(resp)
    print(f"  Uploaded files: {len(created_files)}")
    for item in created_files:
        print(f"    {item['job_file_id']} | {item['file_name']} | {item['file_url']}")

    step("Verify job files by job post")
    job_files = extract(get(f"/job-files/job-post/{job_post_id}", token))
    print(f"  Files found for job post: {len(job_files)}")
    for item in job_files:
        print(f"    {item['file_name']} | {item['file_type']} | {item['file_url']}")

    if len(job_files) < 3:
        print("  ERROR: expected at least 3 uploaded job files")
        sys.exit(1)

    step("Fetch one job file directly")
    sample_id = created_files[0]["job_file_id"]
    sample = extract(get(f"/job-files/{sample_id}", token))
    print(f"  job_file_id: {sample['job_file_id']}")
    print(f"  file_name  : {sample['file_name']}")
    print(f"  file_url   : {sample['file_url']}")

    step("Walkthrough summary")
    print("  Job file upload flow is working.")
    print(f"  Created job post: {job_post_id}")
    print(f"  Uploaded file count: {len(created_files)}")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
