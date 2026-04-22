"""
Project scope calculation walkthrough.

Tests:
1. register/login a fresh client user
2. calculate project scope from job-post + draft roles
3. create a job post using the calculated scope
4. create matching job roles
5. fetch the job and roles back for verification
6. create a second job post without project_scope to verify backend auto-fill

Usage:
    python walkthrough/walkthrough-scope.py

Optional:
    BASE_URL=http://localhost:8000 python walkthrough/walkthrough-scope.py
"""

import datetime
import json
import os
import random
import sys

import requests


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "SecurePass123!"
RUN_ID = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"
CLIENT_EMAIL = f"scope.client.{RUN_ID}@walkthrough.dev"


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
    filepath = os.path.join(out_dir, f"walkthrough_scope_{ts}.md")
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
    print(f"\n{'=' * 64}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 64}")


def extract(response: dict):
    return response.get("details", response)


def _auth_headers(token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _json_headers(token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def post_json(endpoint: str, body: dict, token: str = None) -> dict:
    response = requests.post(
        f"{BASE_URL}{endpoint}",
        json=body,
        headers=_json_headers(token),
        timeout=60,
    )
    data = response.json()
    print(f"  POST {endpoint}  [{response.status_code}] {'OK' if response.ok else 'FAIL'}")
    if not response.ok:
        print(json.dumps(data, indent=2))
        sys.exit(1)
    return data


def get_json(endpoint: str, token: str = None, params: dict = None) -> dict:
    response = requests.get(
        f"{BASE_URL}{endpoint}",
        headers=_auth_headers(token),
        params=params,
        timeout=60,
    )
    data = response.json()
    print(f"  GET  {endpoint}  [{response.status_code}] {'OK' if response.ok else 'FAIL'}")
    if not response.ok:
        print(json.dumps(data, indent=2))
        sys.exit(1)
    return data


def login(email: str, password: str) -> str:
    resp = post_json("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


def assert_true(condition: bool, label: str) -> None:
    if not condition:
        print(f"\n  ASSERTION FAILED: {label}")
        sys.exit(1)
    print(f"  OK: {label}")


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        print(f"\n  ASSERTION FAILED: {label}")
        print(f"    expected: {expected}")
        print(f"    actual  : {actual}")
        sys.exit(1)
    print(f"  OK: {label} -> {actual}")


def run() -> None:
    tee, out_path = _start_tee()

    draft_roles = [
        {
            "role_title": "Mobile Developer",
            "role_budget": 3000,
            "budget_currency": "USD",
            "budget_type": "fixed",
            "positions_available": 1,
            "is_required": True,
        },
        {
            "role_title": "Backend Developer",
            "role_budget": 2500,
            "budget_currency": "USD",
            "budget_type": "fixed",
            "positions_available": 1,
            "is_required": True,
        },
    ]

    print("\n" + "=" * 64)
    print("  Capstone API — Project Scope Walkthrough")
    print("=" * 64)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print(f"  Run ID : {RUN_ID}")

    try:
        step("Register a fresh client user")
        post_json("/auth/register", {
            "email": CLIENT_EMAIL,
            "password": PASSWORD,
            "user_type": "client",
            "full_name": "Scope Walkthrough Client",
        })

        step("Log in as the client and resolve client profile")
        token = login(CLIENT_EMAIL, PASSWORD)
        client_profile = extract(get_json("/clients", token))[0]
        client_id = client_profile["client_id"]
        print(f"  client_id: {client_id}")

        step("Calculate project scope from draft job post + draft roles")
        calc_payload = {
            "job_title": "Cross-platform app with API and admin dashboard",
            "job_description": (
                "Build a customer-facing mobile app, backend APIs, admin dashboard, "
                "authentication, deployment pipeline, and handoff documentation."
            ),
            "project_type": "team",
            "estimated_duration": "3 months",
            "working_days": 60,
            "experience_level": "intermediate",
            "role_count": len(draft_roles),
            "roles": draft_roles,
        }
        calc_resp = extract(post_json("/job-posts/calculate-project-scope", calc_payload, token))
        recommended_scope = calc_resp["recommended_project_scope"]
        print(f"  recommended_project_scope: {recommended_scope}")
        print(f"  score                    : {calc_resp['score']}")
        print(f"  confidence               : {calc_resp['confidence']}")
        assert_true(recommended_scope in {"small", "medium", "large"}, "calculator returns a valid scope")
        assert_equal(calc_resp["factors"]["role_count"], len(draft_roles), "calculator reflects the input role count")
        assert_true(calc_resp["factors"]["budget_usd"] is not None, "calculator derived a combined role budget")

        step("Create a job post using the calculated scope")
        create_post_payload = {
            "client_id": client_id,
            "job_title": calc_payload["job_title"],
            "job_description": calc_payload["job_description"],
            "project_type": calc_payload["project_type"],
            "project_scope": recommended_scope,
            "estimated_duration": calc_payload["estimated_duration"],
            "working_days": calc_payload["working_days"],
            "experience_level": calc_payload["experience_level"],
            "status": "active",
        }
        job_post = extract(post_json("/job-posts", create_post_payload, token))
        job_post_id = job_post["job_post_id"]
        print(f"  job_post_id: {job_post_id}")
        assert_equal(job_post["project_scope"], recommended_scope, "job post stored the calculated scope")

        step("Create matching job roles for the job post")
        created_role_ids = []
        for role in draft_roles:
            role_payload = {
                "job_post_id": job_post_id,
                "role_title": role["role_title"],
                "role_budget": role["role_budget"],
                "budget_currency": role["budget_currency"],
                "budget_type": role["budget_type"],
                "positions_available": role["positions_available"],
                "is_required": role["is_required"],
            }
            created_role = extract(post_json("/job-roles", role_payload, token))
            created_role_ids.append(created_role["job_role_id"])
            print(
                f"  role created: {created_role['role_title']} | "
                f"{created_role['role_budget']} {created_role['budget_currency']}"
            )
        assert_equal(len(created_role_ids), len(draft_roles), "all draft roles were created")

        step("Fetch the job post and its roles back for verification")
        fetched_job = extract(get_json(f"/job-posts/{job_post_id}", token))
        fetched_roles = extract(get_json(f"/job-roles/job-post/{job_post_id}", token))
        print(f"  fetched job scope: {fetched_job['project_scope']}")
        print(f"  fetched roles    : {len(fetched_roles)}")
        assert_equal(fetched_job["project_scope"], recommended_scope, "fetched job scope matches saved scope")
        assert_equal(len(fetched_roles), len(draft_roles), "fetched role count matches created role count")

        step("Create a second job post without project_scope to verify backend auto-fill fallback")
        fallback_post = extract(post_json("/job-posts", {
            "client_id": client_id,
            "job_title": "Fallback scope calculation test",
            "job_description": "Need a simple landing page, basic CMS wiring, and contact form setup.",
            "project_type": "individual",
            "estimated_duration": "2 weeks",
            "working_days": 10,
            "experience_level": "entry",
            "status": "draft",
        }, token))
        print(f"  fallback job_post_id: {fallback_post['job_post_id']}")
        print(f"  fallback project_scope: {fallback_post['project_scope']}")
        assert_true(fallback_post["project_scope"] in {"small", "medium", "large"}, "backend fallback auto-filled a valid scope")

        step("Walkthrough summary")
        print("  Scope calculation route is working.")
        print(f"  Draft calculate scope : {recommended_scope}")
        print(f"  Saved job_post_id     : {job_post_id}")
        print(f"  Saved role count      : {len(created_role_ids)}")
        print(f"  Fallback scope        : {fallback_post['project_scope']}")

    finally:
        _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
