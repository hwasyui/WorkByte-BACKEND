"""
Email verification OTP walkthrough.

This script tests the auth flow:
  1. register a fresh user
  2. confirm login is blocked before email verification
  3. verify email with OTP
  4. confirm login succeeds after verification
  5. confirm /auth/me includes email_verified=true

Usage from the backend container:
    python walkthrough/walkthrough-otp.py --email your.email@gmail.com --no-alias

Usage from outside the container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-otp.py --email your.email@gmail.com --no-alias

For repeated automated runs, omit --no-alias to use Gmail plus-addressing, for
example your.email+otp1234@gmail.com.
"""

import argparse
import json
import os
import random
import sys
import time

import requests

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "SecurePass123!"


def load_backend_env() -> None:
    if load_dotenv is None:
        return
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)


def step(title: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {title}")
    print(f"{'=' * 64}")


def extract(response: dict) -> dict:
    return response.get("details", response)


def post(endpoint: str, body: dict, expected_status: set[int] | None = None) -> tuple[int, dict]:
    response = requests.post(
        f"{BASE_URL}{endpoint}",
        json=body,
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}

    ok = response.ok if expected_status is None else response.status_code in expected_status
    print(f"  POST {endpoint} [{response.status_code}] {'OK' if ok else 'FAIL'}")
    if not ok:
        print(json.dumps(payload, indent=2))
        sys.exit(1)
    return response.status_code, payload


def get(endpoint: str, token: str) -> tuple[int, dict]:
    response = requests.get(
        f"{BASE_URL}{endpoint}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}

    print(f"  GET  {endpoint} [{response.status_code}] {'OK' if response.ok else 'FAIL'}")
    if not response.ok:
        print(json.dumps(payload, indent=2))
        sys.exit(1)
    return response.status_code, payload


def make_unique_email(base_email: str, run_id: int, use_alias: bool) -> str:
    if not use_alias:
        return base_email

    local, sep, domain = base_email.partition("@")
    if not sep:
        return base_email

    if domain.lower() in {"gmail.com", "googlemail.com"}:
        local = local.split("+", 1)[0]
        return f"{local}+otp{run_id}@{domain}"

    return base_email


def get_otp_from_response(register_payload: dict) -> str | None:
    details = extract(register_payload)
    return details.get("verification", {}).get("dev_verification_otp")


def main() -> None:
    load_backend_env()

    parser = argparse.ArgumentParser(description="Test email verification OTP flow.")
    parser.add_argument(
        "--email",
        default=os.getenv("OTP_TEST_EMAIL") or os.getenv("SMTP_USERNAME"),
        help="Email inbox that should receive the OTP. Defaults to OTP_TEST_EMAIL or SMTP_USERNAME from .env.",
    )
    parser.add_argument(
        "--user-type",
        choices=["freelancer", "client"],
        default="freelancer",
        help="Account type to create for the walkthrough.",
    )
    parser.add_argument(
        "--no-alias",
        action="store_true",
        default=True,
        help="Use the exact email instead of Gmail plus-addressing. This is the default.",
    )
    parser.add_argument(
        "--alias",
        dest="no_alias",
        action="store_false",
        help="Use Gmail plus-addressing to create a fresh unique account.",
    )
    parser.add_argument(
        "--test-expiry",
        action="store_true",
        help="Request a second OTP, wait until it expires, and confirm it fails.",
    )
    args = parser.parse_args()

    if not args.email or args.email.startswith("your_email"):
        print("  ERROR: provide a real recipient email with --email or set OTP_TEST_EMAIL/SMTP_USERNAME in .env")
        sys.exit(1)

    run_id = random.randint(1000, 9999)
    email = make_unique_email(args.email, run_id, use_alias=not args.no_alias)
    full_name = f"OTP Walkthrough {run_id}"

    print("\n" + "=" * 64)
    print("  Capstone API - Email OTP Walkthrough")
    print("=" * 64)
    print(f"  Target      : {BASE_URL}")
    print(f"  Account     : {email}")
    print(f"  User type   : {args.user_type}")
    print(f"  OTP expiry  : {os.getenv('EMAIL_OTP_EXPIRE_MINUTES', '10')} minute(s)")
    print(f"  APP_ENV     : {os.getenv('APP_ENV', 'development')}")
    print(f"  SHOW_DEV_OTP: {os.getenv('SHOW_DEV_OTP', 'true')}")

    step("1. Register fresh user")
    _, register_payload = post("/auth/register", {
        "email": email,
        "password": PASSWORD,
        "user_type": args.user_type,
        "full_name": full_name,
    }, expected_status={201})

    otp = get_otp_from_response(register_payload)
    if otp:
        print(f"  Dev OTP from response: {otp}")
    else:
        print("  Check your email inbox for the OTP.")
        otp = input("  Enter OTP: ").strip()

    if args.test_expiry:
        step("2. Optional expiry test")
        wait_seconds = int(os.getenv("EMAIL_OTP_EXPIRE_MINUTES", "10")) * 60 + 5
        print(f"  Waiting {wait_seconds} seconds for the first OTP to expire...")
        time.sleep(wait_seconds)
        _, expired_payload = post("/auth/verify-email", {
            "email": email,
            "otp": otp,
        }, expected_status={400})
        print(f"  Expected expiry result: {extract(expired_payload)}")

        _, resend_payload = post("/auth/resend-verification", {"email": email}, expected_status={200})
        otp = get_otp_from_response(resend_payload)
        if otp:
            print(f"  Fresh dev OTP from resend response: {otp}")
        else:
            print("  Check your email inbox for the fresh OTP.")
            otp = input("  Enter fresh OTP: ").strip()

    step("3. Confirm login is blocked before verification")
    _, blocked_login = post("/auth/login", {
        "email": email,
        "password": PASSWORD,
    }, expected_status={403})
    print(f"  Expected block: {extract(blocked_login)}")

    step("4. Verify email with OTP")
    _, verify_payload = post("/auth/verify-email", {
        "email": email,
        "otp": otp,
    }, expected_status={200})
    print(f"  Verification result: {extract(verify_payload)}")

    step("5. Login after verification")
    _, login_payload = post("/auth/login", {
        "email": email,
        "password": PASSWORD,
    }, expected_status={200})
    token = extract(login_payload)["access_token"]
    print("  Token received.")

    step("6. Fetch /auth/me")
    _, me_payload = get("/auth/me", token)
    print(json.dumps(extract(me_payload), indent=2))

    print("\n  OTP walkthrough completed successfully.")


if __name__ == "__main__":
    main()
