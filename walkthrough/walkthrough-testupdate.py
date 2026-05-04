"""
Targeted walkthrough for testing freelancer/client profile updates.

What it does:
  1. Registers one fresh freelancer and one fresh client
  2. Logs both users in
  3. Resolves their profile IDs
  4. Fetches the profile state before update
  5. Sends multipart/form-data PUT updates without picture
  6. Sends multipart/form-data PUT updates with picture
  7. Fetches the profile state after each update
  8. Prints a compact before/after summary and assertions

Usage:
    ./venv/bin/python walkthrough/walkthrough-testupdate.py

Optional:
    BASE_URL=http://localhost:8000 ./venv/bin/python walkthrough/walkthrough-testupdate.py
"""

import sys
import json
import os
import datetime
import random

import requests


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "SecurePass123!"
RUN_ID = f"{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(1000, 9999)}"

FREELANCER_EMAIL = f"update.freelancer.{RUN_ID}@walkthrough.dev"
CLIENT_EMAIL = f"update.client.{RUN_ID}@walkthrough.dev"
WALKTHROUGH_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_IMAGE_PATH = os.path.join(WALKTHROUGH_DIR, "windah.jpeg")


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
    filepath = os.path.join(out_dir, f"walkthrough_testupdate_{ts}.md")
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


def extract(response: dict):
    return response.get("details", response)


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


def put_form(endpoint: str, form_data: dict, token: str = None, file_path: str = None) -> dict:
    files = None
    file_handle = None
    if file_path:
        file_handle = open(file_path, "rb")
        files = {"profile_picture": (os.path.basename(file_path), file_handle, "image/jpeg")}

    try:
        response = requests.put(
            f"{BASE_URL}{endpoint}",
            data=form_data,
            files=files,
            headers=_auth_headers(token),
            timeout=60,
        )
    finally:
        if file_handle:
            file_handle.close()

    data = response.json()
    print(f"  PUT  {endpoint}  [{response.status_code}] {'OK' if response.ok else 'FAIL'}")
    if not response.ok:
        print(json.dumps(data, indent=2))
        sys.exit(1)
    return data


def login(email: str, password: str) -> str:
    resp = post_json("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]

def register_and_verify(body: dict) -> dict:
    resp = post_json("/auth/register", body)
    details = extract(resp)
    otp = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post_json("/auth/verify-email", {"email": body["email"], "otp": otp})
    else:
        print("  Verification OTP not returned. Complete email verification before login.")
    return resp


def show_profile_snapshot(label: str, profile: dict, keys: list[str]) -> None:
    print(f"\n  {label}")
    for key in keys:
        print(f"    {key}: {profile.get(key)}")


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        print(f"\n  ASSERTION FAILED: {label}")
        print(f"    expected: {expected}")
        print(f"    actual  : {actual}")
        sys.exit(1)
    print(f"  OK: {label} -> {actual}")


def run() -> None:
    tee, out_path = _start_tee()

    print("\n" + "=" * 64)
    print("  Capstone API — Profile Update Walkthrough")
    print("=" * 64)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print(f"  Run ID : {RUN_ID}")

    freelancer_initial_name = "Initial Freelancer Name"
    client_initial_name = "Initial Client Name"

    freelancer_update_no_picture = {
        "full_name": "TEST UPDATE DATA full name freelancer",
        "bio": "TEST UPDATE DATA bio freelancer no picture",
        "estimated_rate": "88.75",
        "rate_time": "hourly",
        "rate_currency": "USD",
    }
    freelancer_update_with_picture = {
        "full_name": "TEST UPDATE DATA full name freelancer picture",
        "bio": "TEST UPDATE DATA bio freelancer with picture",
        "estimated_rate": "109.25",
        "rate_time": "monthly",
        "rate_currency": "USD",
    }
    client_update_no_picture = {
        "full_name": "TEST UPDATE DATA full name client",
        "bio": "TEST UPDATE DATA bio client no picture",
        "website_url": "https://example.com/test-update-client",
    }
    client_update_with_picture = {
        "full_name": "TEST UPDATE DATA full name client picture",
        "bio": "TEST UPDATE DATA bio client with picture",
        "website_url": "https://example.com/test-update-client-picture",
    }

    try:
        if not os.path.exists(TEST_IMAGE_PATH):
            print(f"  Missing test image: {TEST_IMAGE_PATH}")
            sys.exit(1)

        step("Register fresh freelancer and client users")
        register_and_verify({
            "email": FREELANCER_EMAIL,
            "password": PASSWORD,
            "user_type": "freelancer",
            "full_name": freelancer_initial_name,
        })
        register_and_verify({
            "email": CLIENT_EMAIL,
            "password": PASSWORD,
            "user_type": "client",
            "full_name": client_initial_name,
        })

        step("Log in both users")
        freelancer_token = login(FREELANCER_EMAIL, PASSWORD)
        client_token = login(CLIENT_EMAIL, PASSWORD)
        print("  Tokens acquired successfully.")

        step("Resolve freelancer and client profile IDs")
        freelancer_profile_before = extract(get_json("/freelancers", freelancer_token))[0]
        client_profile_before = extract(get_json("/clients", client_token))[0]
        freelancer_id = freelancer_profile_before["freelancer_id"]
        client_id = client_profile_before["client_id"]
        print(f"  freelancer_id: {freelancer_id}")
        print(f"  client_id    : {client_id}")

        show_profile_snapshot(
            "Freelancer before update",
            freelancer_profile_before,
            ["full_name", "bio", "estimated_rate", "rate_time", "rate_currency"],
        )
        show_profile_snapshot(
            "Client before update",
            client_profile_before,
            ["full_name", "bio", "website_url", "profile_picture_url"],
        )

        step("Update freelancer without picture")
        freelancer_update_resp = extract(
            put_form(
                f"/freelancers/{freelancer_id}",
                freelancer_update_no_picture,
                freelancer_token,
            )
        )
        show_profile_snapshot(
            "Freelancer response after update without picture",
            freelancer_update_resp,
            ["full_name", "bio", "estimated_rate", "rate_time", "rate_currency", "profile_picture_url"],
        )

        step("Fetch freelancer after update without picture")
        freelancer_after_no_picture = extract(get_json("/freelancers", freelancer_token))[0]
        show_profile_snapshot(
            "Freelancer after update without picture",
            freelancer_after_no_picture,
            ["full_name", "bio", "estimated_rate", "rate_time", "rate_currency", "profile_picture_url"],
        )

        step("Update freelancer with picture")
        freelancer_update_with_picture_resp = extract(
            put_form(
                f"/freelancers/{freelancer_id}",
                freelancer_update_with_picture,
                freelancer_token,
                file_path=TEST_IMAGE_PATH,
            )
        )
        show_profile_snapshot(
            "Freelancer response after update with picture",
            freelancer_update_with_picture_resp,
            ["full_name", "bio", "estimated_rate", "rate_time", "rate_currency", "profile_picture_url"],
        )

        step("Fetch freelancer after update with picture")
        freelancer_after_with_picture = extract(get_json("/freelancers", freelancer_token))[0]
        show_profile_snapshot(
            "Freelancer after update with picture",
            freelancer_after_with_picture,
            ["full_name", "bio", "estimated_rate", "rate_time", "rate_currency", "profile_picture_url"],
        )

        step("Update client without picture")
        client_update_resp = extract(
            put_form(
                f"/clients/{client_id}",
                client_update_no_picture,
                client_token,
            )
        )
        show_profile_snapshot(
            "Client response after update without picture",
            client_update_resp,
            ["full_name", "bio", "website_url", "profile_picture_url"],
        )

        client_after_no_picture = extract(get_json("/clients", client_token))[0]
        show_profile_snapshot(
            "Client after update without picture",
            client_after_no_picture,
            ["full_name", "bio", "website_url", "profile_picture_url"],
        )

        step("Update client with picture")
        client_update_with_picture_resp = extract(
            put_form(
                f"/clients/{client_id}",
                client_update_with_picture,
                client_token,
                file_path=TEST_IMAGE_PATH,
            )
        )
        show_profile_snapshot(
            "Client response after update with picture",
            client_update_with_picture_resp,
            ["full_name", "bio", "website_url", "profile_picture_url"],
        )

        step("Fetch client after update with picture")
        client_after_with_picture = extract(get_json("/clients", client_token))[0]
        show_profile_snapshot(
            "Client after update with picture",
            client_after_with_picture,
            ["full_name", "bio", "website_url", "profile_picture_url"],
        )

        step("Assertions")
        assert_equal(
            freelancer_after_no_picture.get("full_name"),
            freelancer_update_no_picture["full_name"],
            "freelancer full_name updated without picture",
        )
        assert_equal(
            freelancer_after_no_picture.get("bio"),
            freelancer_update_no_picture["bio"],
            "freelancer bio updated without picture",
        )
        assert_equal(
            float(freelancer_after_no_picture.get("estimated_rate")),
            float(freelancer_update_no_picture["estimated_rate"]),
            "freelancer estimated_rate updated without picture",
        )
        assert_equal(
            freelancer_after_no_picture.get("rate_time"),
            freelancer_update_no_picture["rate_time"],
            "freelancer rate_time updated without picture",
        )
        assert_equal(
            freelancer_after_no_picture.get("rate_currency"),
            freelancer_update_no_picture["rate_currency"],
            "freelancer rate_currency updated without picture",
        )
        assert_equal(
            freelancer_after_with_picture.get("full_name"),
            freelancer_update_with_picture["full_name"],
            "freelancer full_name updated with picture",
        )
        assert_equal(
            freelancer_after_with_picture.get("bio"),
            freelancer_update_with_picture["bio"],
            "freelancer bio updated with picture",
        )
        assert_equal(
            float(freelancer_after_with_picture.get("estimated_rate")),
            float(freelancer_update_with_picture["estimated_rate"]),
            "freelancer estimated_rate updated with picture",
        )
        assert_equal(
            freelancer_after_with_picture.get("rate_time"),
            freelancer_update_with_picture["rate_time"],
            "freelancer rate_time updated with picture",
        )
        assert_equal(
            freelancer_after_with_picture.get("rate_currency"),
            freelancer_update_with_picture["rate_currency"],
            "freelancer rate_currency updated with picture",
        )
        if not freelancer_after_with_picture.get("profile_picture_url"):
            print("\n  ASSERTION FAILED: freelancer profile picture was not set")
            sys.exit(1)
        print(f"  OK: freelancer profile picture updated -> {freelancer_after_with_picture.get('profile_picture_url')}")

        assert_equal(
            client_after_no_picture.get("full_name"),
            client_update_no_picture["full_name"],
            "client full_name updated without picture",
        )
        assert_equal(
            client_after_no_picture.get("bio"),
            client_update_no_picture["bio"],
            "client bio updated without picture",
        )
        assert_equal(
            client_after_no_picture.get("website_url"),
            client_update_no_picture["website_url"],
            "client website_url updated without picture",
        )
        assert_equal(
            client_after_with_picture.get("full_name"),
            client_update_with_picture["full_name"],
            "client full_name updated with picture",
        )
        assert_equal(
            client_after_with_picture.get("bio"),
            client_update_with_picture["bio"],
            "client bio updated with picture",
        )
        assert_equal(
            client_after_with_picture.get("website_url"),
            client_update_with_picture["website_url"],
            "client website_url updated with picture",
        )
        if not client_after_with_picture.get("profile_picture_url"):
            print("\n  ASSERTION FAILED: client profile picture was not set")
            sys.exit(1)
        print(f"  OK: client profile picture updated -> {client_after_with_picture.get('profile_picture_url')}")

        print("\n  All profile update checks passed for both without-picture and with-picture flows.")

    finally:
        _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
