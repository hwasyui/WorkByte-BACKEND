"""
Dashboard/workspace walkthrough.

Checks:
- duplicate apply to the same role returns 409
- applying to another role in the same job post still succeeds
- dashboard filter-in, filter-out, sorting, pagination, and invalid params

Usage:
    python walkthrough/walkthrough-dashboard.py
"""

import datetime
import json
import os
import sys
from typing import Optional
from urllib.parse import urlencode

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
    filepath = os.path.join(out_dir, f"walkthrough_dashboard_{ts}.md")
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


def extract(response: dict):
    return response.get("details", response)


def request_json(
    method: str,
    endpoint: str,
    token: Optional[str] = None,
    body: Optional[dict] = None,
    params: Optional[dict] = None,
    expected_status: int = 200,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.request(
        method,
        f"{BASE_URL}{endpoint}",
        json=body,
        params=params,
        headers=headers,
        timeout=90,
    )
    payload = response.json()
    query = f"?{urlencode(params)}" if params else ""
    ok = response.status_code == expected_status
    print(f"  {method:<4} {endpoint}{query}  [{response.status_code}] {'OK' if ok else 'FAIL'}")
    if not ok:
        print(f"  EXPECTED: {expected_status}")
        print(f"  RESPONSE: {json.dumps(payload, indent=2)}")
        sys.exit(1)
    return payload


def get(endpoint: str, token: str, params: Optional[dict] = None, expected_status: int = 200) -> dict:
    return request_json("GET", endpoint, token=token, params=params, expected_status=expected_status)


def post(endpoint: str, body: dict, token: Optional[str] = None, expected_status: int = 201) -> dict:
    return request_json("POST", endpoint, token=token, body=body, expected_status=expected_status)


def register_and_verify(body: dict) -> None:
    resp = post("/auth/register", body, expected_status=201)
    otp = extract(resp).get("verification", {}).get("dev_verification_otp")
    if not otp:
        print("  ERROR: verification OTP not returned")
        sys.exit(1)
    post("/auth/verify-email", {"email": body["email"], "otp": otp}, expected_status=200)


def login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password}, expected_status=200)
    return extract(resp)["access_token"]


def create_role(token: str, job_post_id: str, title: str, display_order: int) -> str:
    resp = post(
        "/job-roles",
        {
            "job_post_id": job_post_id,
            "role_title": title,
            "role_budget": 750.0,
            "budget_currency": "USD",
            "budget_type": "fixed",
            "role_description": f"{title} for dashboard walkthrough.",
            "positions_available": 1,
            "is_required": True,
            "display_order": display_order,
        },
        token,
    )
    return extract(resp)["job_role_id"]


def apply_role(token: str, job_post_id: str, job_role_id: str, expected_status: int) -> dict:
    return post(
        "/proposals",
        {
            "job_post_id": job_post_id,
            "job_role_id": job_role_id,
            "cover_letter": "I can help with this role end to end.",
            "proposed_budget": 750.0,
            "proposed_duration": "14 days",
            "status": "pending",
        },
        token,
        expected_status=expected_status,
    )


def dashboard_case(
    label: str,
    endpoint: str,
    token: str,
    params: dict,
    allowed_statuses: Optional[set[str]] = None,
    forbidden_statuses: Optional[set[str]] = None,
    expected_status: int = 200,
) -> None:
    print(f"\n  Case: {label}")
    resp = get(endpoint, token, params=params, expected_status=expected_status)
    if expected_status != 200:
        return

    data = extract(resp)
    items = data.get("items", [])
    statuses = {item.get("tracking_status") for item in items}
    print(f"  total={data.get('pagination', {}).get('total')} statuses={sorted(s for s in statuses if s)}")
    if allowed_statuses and not statuses <= allowed_statuses:
        print(f"  ERROR: expected only {sorted(allowed_statuses)}, got {sorted(statuses)}")
        sys.exit(1)
    if forbidden_statuses and statuses & forbidden_statuses:
        print(f"  ERROR: forbidden statuses returned: {sorted(statuses & forbidden_statuses)}")
        sys.exit(1)


def exercise_dashboard(
    endpoint: str,
    token: str,
    expected_status: str,
    all_order_fields: tuple[str, ...],
    exclude_statuses: str,
    forbidden_statuses: set[str],
) -> None:
    dashboard_case("baseline", endpoint, token, {}, {expected_status})
    dashboard_case("filter-in tracking_status", endpoint, token, {"tracking_status": expected_status}, {expected_status})
    dashboard_case("filter-in tracking_statuses", endpoint, token, {"tracking_statuses": expected_status}, {expected_status})
    dashboard_case("filter-in include_tracking_statuses", endpoint, token, {"include_tracking_statuses": expected_status}, {expected_status})
    dashboard_case("filter-in filter_in_tracking_statuses", endpoint, token, {"filter_in_tracking_statuses": expected_status}, {expected_status})
    dashboard_case("filter-in multiple statuses", endpoint, token, {"tracking_statuses": f"{expected_status},completed"}, {expected_status, "completed"})
    dashboard_case("filter-out unrelated statuses", endpoint, token, {"exclude_tracking_statuses": exclude_statuses}, forbidden_statuses=forbidden_statuses)
    dashboard_case("filter-in and filter-out together", endpoint, token, {"tracking_statuses": f"{expected_status},completed", "exclude_tracking_statuses": "completed"}, {expected_status})
    dashboard_case("pagination", endpoint, token, {"page": 1, "page_size": 1}, {expected_status})

    for order_by in all_order_fields:
        dashboard_case(f"sort {order_by} asc", endpoint, token, {"order_by": order_by, "order_dir": "asc"}, {expected_status})
        dashboard_case(f"sort {order_by} desc", endpoint, token, {"order_by": order_by, "order_dir": "desc"}, {expected_status})

    dashboard_case("invalid tracking_status", endpoint, token, {"tracking_status": "not_a_status"}, expected_status=400)
    dashboard_case("invalid order_by", endpoint, token, {"order_by": "not_a_field"}, expected_status=400)


def run() -> None:
    tee, out_path = _start_tee()
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    password = "SecurePass123"
    client_email = f"dashboard.client.{ts}@walkthrough.dev"
    freelancer_email = f"dashboard.freelancer.{ts}@walkthrough.dev"

    print("\n" + "=" * 60)
    print("  Capstone API - Dashboard/Workspace Walkthrough")
    print("=" * 60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    step("Register and verify fresh users")
    register_and_verify({"email": client_email, "password": password, "user_type": "client", "full_name": "Dashboard Client"})
    register_and_verify({"email": freelancer_email, "password": password, "user_type": "freelancer", "full_name": "Dashboard Freelancer"})

    step("Log in and resolve profile IDs")
    client_token = login(client_email, password)
    freelancer_token = login(freelancer_email, password)
    client_id = extract(get("/auth/me", client_token))["client_id"]
    freelancer_id = extract(get("/auth/me", freelancer_token))["freelancer_id"]
    print(f"  client_id    : {client_id}")
    print(f"  freelancer_id: {freelancer_id}")

    step("Create a job post with two roles")
    job_resp = post(
        "/job-posts",
        {
            "client_id": client_id,
            "job_title": "Dashboard Workspace Walkthrough",
            "job_description": "Need two specialists so role applications and workspace filters can be tested.",
            "project_type": "team",
            "project_scope": "medium",
            "estimated_duration": "1 month",
            "working_days": 20,
            "experience_level": "intermediate",
            "status": "active",
        },
        client_token,
    )
    job_post_id = extract(job_resp)["job_post_id"]
    role_a_id = create_role(client_token, job_post_id, "Backend API Engineer", 0)
    role_b_id = create_role(client_token, job_post_id, "QA Automation Engineer", 1)
    print(f"  job_post_id: {job_post_id}")
    print(f"  role_a_id  : {role_a_id}")
    print(f"  role_b_id  : {role_b_id}")

    step("Check same-role duplicate and same-job different role apply")
    first = apply_role(freelancer_token, job_post_id, role_a_id, expected_status=201)
    duplicate = apply_role(freelancer_token, job_post_id, role_a_id, expected_status=409)
    second = apply_role(freelancer_token, job_post_id, role_b_id, expected_status=201)
    print(f"  first proposal      : {extract(first)['proposal_id']}")
    print(f"  duplicate same role : {extract(duplicate)}")
    print(f"  second role proposal: {extract(second)['proposal_id']}")

    step("Exercise freelancer workspace parameters")
    exercise_dashboard(
        "/dashboard/freelancer",
        freelancer_token,
        "applied",
        ("last_activity_date", "submitted_at", "start_date", "end_date", "actual_completion_date", "job_title", "proposed_budget", "agreed_budget"),
        "rejected,withdrawn",
        {"rejected", "withdrawn"},
    )

    step("Exercise client workspace parameters")
    exercise_dashboard(
        "/dashboard/client",
        client_token,
        "open",
        ("last_activity_date", "created_at", "posted_at", "deadline", "job_title"),
        "draft,completed",
        {"draft", "completed"},
    )

    step("Walkthrough summary")
    print("  Duplicate same-role proposal is blocked with 409.")
    print("  Different-role proposal in the same job post is allowed.")
    print("  Workspace filter-in/filter-out, sort, pagination, and invalid params passed.")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
