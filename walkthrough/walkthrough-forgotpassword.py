"""
Forgot-password flow walkthrough — freelancer and client accounts.

Runs the full password reset cycle end-to-end for both account types:

  FREELANCER
  1.  Register fresh freelancer account
  2.  Verify email (dev OTP from register response)
  3.  Login with original password → success
  4.  POST /auth/forgot-password → dev_reset_otp from response
  5.  POST /auth/reset-password
  6.  Login with OLD password → 401
  7.  Login with NEW password → success
  8.  GET /auth/me → same user
  9.  Replay consumed OTP → 400

  CLIENT  (same 9 steps)

Requirements on the server:
  APP_ENV=development   (dev OTPs are returned in the response body)
  SHOW_DEV_OTP=true

Usage:
    python walkthrough/walkthrough-forgotpassword.py
"""

import json
import os
import sys
import time

import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
ORIGINAL_PASSWORD = "SecurePass123!"
NEW_PASSWORD      = "NewPass456!"


def load_backend_env() -> None:
    if load_dotenv is None:
        return
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)


def section(title: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}")


def step(label: str) -> None:
    print(f"\n  -- {label}")


def extract(payload: dict):
    return payload.get("details", payload)


def require_dev_otp(payload: dict, key: str, hint: str) -> str:
    value = extract(payload).get(key)
    if not value:
        print(f"\n  ERROR: '{key}' not found in response.")
        print("  Make sure APP_ENV=development and SHOW_DEV_OTP=true on the server.")
        print(f"  Hint: {hint}")
        sys.exit(1)
    return value


def post(endpoint: str, body: dict, expected: set) -> dict:
    resp = requests.post(
        f"{BASE_URL}{endpoint}",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text}

    ok = resp.status_code in expected
    print(f"    POST {endpoint} [{resp.status_code}] {'OK' if ok else 'FAIL'}")
    if not ok:
        print(json.dumps(payload, indent=2))
        sys.exit(1)
    return payload


def get_auth(endpoint: str, token: str) -> dict:
    resp = requests.get(
        f"{BASE_URL}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {"raw": resp.text}

    print(f"    GET  {endpoint} [{resp.status_code}] {'OK' if resp.ok else 'FAIL'}")
    if not resp.ok:
        print(json.dumps(payload, indent=2))
        sys.exit(1)
    return payload


def detail_str(payload: dict) -> str:
    d = extract(payload)
    return d if isinstance(d, str) else (d.get("message") or d.get("detail") or str(d))


def run_forgot_password_cycle(label: str, email: str, user_type: str, full_name: str) -> None:
    section(f"{label} — {email}")

    # ── 1. Register ───────────────────────────────────────────────────────────
    step("1. Register")
    reg_payload = post("/auth/register", {
        "email": email,
        "password": ORIGINAL_PASSWORD,
        "user_type": user_type,
        "full_name": full_name,
    }, expected={201})

    verify_otp = (
        extract(reg_payload).get("verification", {}).get("dev_verification_otp")
        or extract(reg_payload).get("dev_verification_otp")
    )
    if not verify_otp:
        print("  ERROR: dev_verification_otp not in register response.")
        print("  Set APP_ENV=development and SHOW_DEV_OTP=true on the server.")
        sys.exit(1)
    print(f"    dev_verification_otp : {verify_otp}")

    # ── 2. Verify email ───────────────────────────────────────────────────────
    step("2. Verify email")
    verify_payload = post("/auth/verify-email", {
        "email": email,
        "otp": verify_otp,
    }, expected={200})
    print(f"    {detail_str(verify_payload)}")

    # ── 3. Login with original password ──────────────────────────────────────
    step("3. Login with original password (expect 200)")
    login_payload = post("/auth/login", {
        "email": email,
        "password": ORIGINAL_PASSWORD,
    }, expected={200})
    print("    Token received.")

    # ── 4. Request password reset ─────────────────────────────────────────────
    step("4. POST /auth/forgot-password")
    forgot_payload = post("/auth/forgot-password", {
        "email": email,
    }, expected={200})
    print(f"    {detail_str(forgot_payload)}")

    reset_otp = require_dev_otp(
        forgot_payload, "dev_reset_otp",
        "forgot-password response should contain dev_reset_otp in development mode"
    )
    print(f"    dev_reset_otp : {reset_otp}")

    # ── 5. Reset password ─────────────────────────────────────────────────────
    step("5. POST /auth/reset-password")
    reset_payload = post("/auth/reset-password", {
        "email": email,
        "otp": reset_otp,
        "new_password": NEW_PASSWORD,
    }, expected={200})
    print(f"    {detail_str(reset_payload)}")

    # ── 6. Old password must be rejected ─────────────────────────────────────
    step("6. Login with OLD password (expect 401)")
    old_payload = post("/auth/login", {
        "email": email,
        "password": ORIGINAL_PASSWORD,
    }, expected={401})
    print(f"    Rejected: {detail_str(old_payload)}")

    # ── 7. New password must work ─────────────────────────────────────────────
    step("7. Login with NEW password (expect 200)")
    new_login = post("/auth/login", {
        "email": email,
        "password": NEW_PASSWORD,
    }, expected={200})
    new_token = extract(new_login)["access_token"]
    print("    Token received with new password.")

    # ── 8. /auth/me must return the same user ─────────────────────────────────
    step("8. GET /auth/me")
    me_payload = get_auth("/auth/me", new_token)
    me = extract(me_payload)
    print(f"    user_id        : {me.get('user_id')}")
    print(f"    email          : {me.get('email')}")
    print(f"    email_verified : {me.get('email_verified')}")

    # ── 9. Replay consumed OTP must fail ──────────────────────────────────────
    step("9. Replay consumed OTP (expect 400)")
    replay_payload = post("/auth/reset-password", {
        "email": email,
        "otp": reset_otp,
        "new_password": "AnotherPass789!",
    }, expected={400})
    print(f"    Rejected: {detail_str(replay_payload)}")

    print(f"\n  {label} cycle complete. Password is now: {NEW_PASSWORD}")


def main() -> None:
    load_backend_env()

    ts = int(time.time())
    fl_email = f"fp.freelancer.{ts}@test.dev"
    cl_email = f"fp.client.{ts}@test.dev"

    print("\n" + "=" * 64)
    print("  Capstone API — Forgot Password Walkthrough")
    print("=" * 64)
    print(f"  Target       : {BASE_URL}")
    print(f"  Freelancer   : {fl_email}")
    print(f"  Client       : {cl_email}")
    print(f"  New password : {NEW_PASSWORD}")
    print(f"  APP_ENV      : {os.getenv('APP_ENV', 'development')}")
    print(f"  SHOW_DEV_OTP : {os.getenv('SHOW_DEV_OTP', 'true')}")

    run_forgot_password_cycle(
        label="FREELANCER",
        email=fl_email,
        user_type="freelancer",
        full_name="FP Freelancer Test",
    )

    run_forgot_password_cycle(
        label="CLIENT",
        email=cl_email,
        user_type="client",
        full_name="FP Client Test",
    )

    print("\n" + "=" * 64)
    print("  All forgot-password cycles passed.")
    print("=" * 64 + "\n")


if __name__ == "__main__":
    main()
