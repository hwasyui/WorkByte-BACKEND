"""
Profile photo upload walkthrough for freelancer and client using windah.jpeg.

This walkthrough tests:
1. Dedicated profile-picture upload routes
2. Normal multipart PUT profile update routes with optional profile_picture

Note: the normal POST /freelancers and POST /clients routes are not runtime-tested
here because /auth/register already creates the corresponding profile rows.

Usage (from inside the backend container):
    pip install requests
    python walkthrough/walkthrough-profile-photo.py

Or from outside the container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-profile-photo.py

Set BASE_URL below if your backend runs on a different host/port.
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"
FREELANCER_EMAIL = "photo.freelancer@walkthrough3.dev"
CLIENT_EMAIL = "photo.client@walkthrough3.dev"
PASSWORD = "SecurePass123"

# ── output tee (terminal + markdown file) ─────────────────────────────────────

class _Tee:
    """Write to both the real stdout and a file simultaneously."""

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
    filepath = os.path.join(out_dir, f"walkthrough_profile_photo_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")

# ── helpers ───────────────────────────────────────────────────────────────────

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
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def multipart_post(endpoint: str, files: dict, data: dict = None, token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data or {}, headers=headers, timeout=90)
    try:
        data = r.json()
    except ValueError:
        data = {"raw_text": r.text}
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def multipart_put(endpoint: str, files: dict = None, data: dict = None, token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.put(f"{BASE_URL}{endpoint}", files=files or {}, data=data or {}, headers=headers, timeout=90)
    try:
        data = r.json()
    except ValueError:
        data = {"raw_text": r.text}
    status = "OK" if r.ok else "FAIL"
    print(f"  PUT  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params or {}, timeout=90)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  GET  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def extract(response: dict) -> dict:
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


def get_single_profile(endpoint: str, token: str, id_key: str) -> dict:
    profiles = extract(get(endpoint, token))
    if not profiles or not isinstance(profiles, list):
        print(f"  ERROR: profile not found at {endpoint}")
        sys.exit(1)
    profile = profiles[0]
    print(f"  {id_key}: {profile[id_key]}")
    print(f"  profile_picture_url: {profile.get('profile_picture_url')}")
    return profile


# ── main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  Capstone API — Profile Photo Upload Walkthrough")
    print("="*60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    step("Register freelancer user")
    post("/auth/register", {
        "email": FREELANCER_EMAIL,
        "password": PASSWORD,
        "user_type": "freelancer",
        "full_name": "Photo Freelancer"
    })

    step("Register client user")
    post("/auth/register", {
        "email": CLIENT_EMAIL,
        "password": PASSWORD,
        "user_type": "client",
        "full_name": "Photo Client"
    })

    step("Login both users")
    tok_freelancer = token_from_login(FREELANCER_EMAIL, PASSWORD)
    tok_client = token_from_login(CLIENT_EMAIL, PASSWORD)
    print("  Logged in both users.")

    step("Get freelancer profile ID")
    freelancer_profile = get_single_profile("/freelancers", tok_freelancer, "freelancer_id")
    freelancer_id = freelancer_profile["freelancer_id"]

    step("Get client profile ID")
    client_profile = get_single_profile("/clients", tok_client, "client_id")
    client_id = client_profile["client_id"]

    image_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "windah.jpeg")
    if not os.path.isfile(image_path):
        print(f"  ERROR: Could not find image at {image_path}")
        sys.exit(1)

    step("Dedicated route: upload freelancer profile photo")
    with open(image_path, "rb") as f:
        files = {"file": ("freelancer-dedicated.jpeg", f, "image/jpeg")}
        resp = multipart_post(f"/freelancers/{freelancer_id}/profile-picture", files, token=tok_freelancer)
    details = extract(resp)
    print(f"  Freelancer photo upload response: {json.dumps(details, indent=2)}")

    step("Verify freelancer photo URL after dedicated route")
    freelancer_profile = get_single_profile("/freelancers", tok_freelancer, "freelancer_id")

    step("Dedicated route: upload client profile photo")
    with open(image_path, "rb") as f:
        files = {"file": ("client-dedicated.jpeg", f, "image/jpeg")}
        resp = multipart_post(f"/clients/{client_id}/profile-picture", files, token=tok_client)
    details = extract(resp)
    print(f"  Client photo upload response: {json.dumps(details, indent=2)}")

    step("Verify client photo URL after dedicated route")
    client_profile = get_single_profile("/clients", tok_client, "client_id")

    step("Normal route: update freelancer via PUT with optional profile_picture")
    with open(image_path, "rb") as f:
        files = {"profile_picture": ("freelancer-normal.jpeg", f, "image/jpeg")}
        data = {
            "full_name": "Photo Freelancer Updated",
            "bio": "Updated through the normal PUT route with multipart form data.",
            "estimated_rate": "80",
            "rate_time": "hourly",
            "rate_currency": "USD",
        }
        resp = multipart_put(f"/freelancers/{freelancer_id}", files=files, data=data, token=tok_freelancer)
    details = extract(resp)
    print(f"  Freelancer normal PUT response: {json.dumps(details, indent=2)}")

    step("Verify freelancer photo URL after normal PUT route")
    freelancer_profile = get_single_profile("/freelancers", tok_freelancer, "freelancer_id")

    step("Normal route: update client via PUT with optional profile_picture")
    with open(image_path, "rb") as f:
        files = {"profile_picture": ("client-normal.jpeg", f, "image/jpeg")}
        data = {
            "full_name": "Photo Client Updated",
            "bio": "Updated through the normal PUT route with multipart form data.",
            "website_url": "https://walkthrough.dev/profile-photo",
        }
        resp = multipart_put(f"/clients/{client_id}", files=files, data=data, token=tok_client)
    details = extract(resp)
    print(f"  Client normal PUT response: {json.dumps(details, indent=2)}")

    step("Verify client photo URL after normal PUT route")
    client_profile = get_single_profile("/clients", tok_client, "client_id")

    step("Walkthrough summary")
    print("  Tested dedicated upload routes and normal multipart PUT profile routes.")
    print("  Normal POST profile routes are not exercised here because /auth/register already creates the profile rows.")
    print(f"  Final freelancer profile_picture_url: {freelancer_profile.get('profile_picture_url')}")
    print(f"  Final client profile_picture_url: {client_profile.get('profile_picture_url')}")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
