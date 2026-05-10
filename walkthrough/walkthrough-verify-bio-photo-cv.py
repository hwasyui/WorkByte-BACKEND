"""
Walkthrough untuk memverifikasi bahwa:
1. Bio freelancer tersimpan dalam database
2. Foto profil freelancer terupload ke Supabase dan URL tersimpan dalam database
3. CV freelancer terupload ke Supabase dan URL tersimpan dalam database

Usage:
    python walkthrough/walkthrough-verify-bio-photo-cv.py

Atau dari container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-verify-bio-photo-cv.py

Ubah BASE_URL jika backend berjalan di host/port lain.
"""

import os
import sys
import json
import datetime
import random
import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "SecurePass123"
_RUN_ID = random.randint(1000, 9999)
FREELANCER_EMAIL = f"verify.freelancer.{_RUN_ID}@walkthrough.dev"

WALKTHROUGH_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_IMAGE_PATH = os.path.join(WALKTHROUGH_DIR, "windah.jpeg")
TEST_CV_PATH = os.path.join(WALKTHROUGH_DIR, "Intan Kumala Pasya_CV.pdf")


def _print_step(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  STEP: {title}")
    print("=" * 60)


def _json_headers(token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _auth_headers(token: str = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _extract(response: dict) -> dict:
    return response.get("details", response)


def _request_json(method: str, endpoint: str, token: str = None, json_body: dict = None, data: dict = None, files: dict = None) -> dict:
    url = f"{BASE_URL}{endpoint}"
    headers = _auth_headers(token)
    if json_body is not None:
        headers.update({"Content-Type": "application/json"})

    response = requests.request(
        method,
        url,
        headers=headers,
        json=json_body,
        data=data,
        files=files,
        timeout=120,
    )

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text}

    status = "OK" if response.ok else "FAIL"
    print(f"  {method.upper()} {endpoint} [{response.status_code}] {status}")
    if not response.ok:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(1)
    return payload


def post(endpoint: str, body: dict, token: str = None) -> dict:
    return _request_json("post", endpoint, token=token, json_body=body)


def put(endpoint: str, data: dict, files: dict = None, token: str = None) -> dict:
    return _request_json("put", endpoint, token=token, data=data, files=files)


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    url = f"{BASE_URL}{endpoint}"
    headers = _auth_headers(token)
    response = requests.get(url, headers=headers, params=params or {}, timeout=60)
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw_text": response.text}
    status = "OK" if response.ok else "FAIL"
    print(f"  GET  {endpoint} [{response.status_code}] {status}")
    if not response.ok:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        sys.exit(1)
    return payload


def token_from_login(email: str, password: str) -> str:
    response = post("/auth/login", {"email": email, "password": password})
    token = _extract(response).get("access_token")
    if not token:
        print("  ERROR: Gagal mendapatkan access_token dari login")
        sys.exit(1)
    return token


def register_and_verify(email: str, password: str) -> None:
    response = post(
        "/auth/register",
        {
            "email": email,
            "password": password,
            "user_type": "freelancer",
            "full_name": "Verifikasi Freelancer",
        },
    )
    details = _extract(response)
    otp = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": email, "otp": otp})
    else:
        print("  WARNING: OTP verifikasi tidak dikembalikan. Pastikan email diverifikasi secara manual jika diperlukan.")


def _check_url_accessible(url: str) -> bool:
    try:
        response = requests.head(url, allow_redirects=True, timeout=30)
        if response.ok:
            return True
        response = requests.get(url, stream=True, timeout=30)
        return response.ok
    except Exception as exc:
        print(f"  WARNING: Tidak bisa mengakses URL: {url}\n    {exc}")
        return False


def _show_profile(profile: dict) -> None:
    print("  Profile snapshot:")
    print(f"    full_name           : {profile.get('full_name')}")
    print(f"    bio                 : {profile.get('bio')}")
    print(f"    profile_picture_url : {profile.get('profile_picture_url')}")
    print(f"    cv_file_url         : {profile.get('cv_file_url')}")


def run() -> None:
    print("\n" + "=" * 60)
    print("  Capstone Walkthrough — Verifikasi Bio, Foto Profil, dan CV")
    print("=" * 60)
    print(f"  Target : {BASE_URL}")
    print(f"  Email  : {FREELANCER_EMAIL}")
    print("=" * 60)

    if not os.path.exists(TEST_IMAGE_PATH):
        print(f"ERROR: File gambar tidak ditemukan: {TEST_IMAGE_PATH}")
        sys.exit(1)
    if not os.path.exists(TEST_CV_PATH):
        print(f"ERROR: File CV tidak ditemukan: {TEST_CV_PATH}")
        sys.exit(1)

    register_and_verify(FREELANCER_EMAIL, PASSWORD)
    token = token_from_login(FREELANCER_EMAIL, PASSWORD)

    _print_step("Ambil ID freelancer dan status awal profil")
    freelancers = _extract(get("/freelancers", token))
    if not freelancers or not isinstance(freelancers, list):
        print("  ERROR: Respon /freelancers tidak berformat daftar profil")
        sys.exit(1)
    freelancer = freelancers[0]
    freelancer_id = freelancer["freelancer_id"]
    print(f"  freelancer_id: {freelancer_id}")
    _show_profile(freelancer)

    _print_step("Update bio dan upload profile picture melalui PUT /freelancers/{id}")
    new_bio = "Saya ingin memastikan bio, foto, dan CV tersimpan dengan benar ke Supabase dan database."
    with open(TEST_IMAGE_PATH, "rb") as image_file:
        files = {"profile_picture": (os.path.basename(TEST_IMAGE_PATH), image_file, "image/jpeg")}
        data = {
            "full_name": "Freelancer Verifikasi",
            "bio": new_bio,
            "estimated_rate": "75",
            "rate_time": "hourly",
            "rate_currency": "USD",
        }
        updated = _extract(put(f"/freelancers/{freelancer_id}", data=data, files=files, token=token))

    print("  ✅ Update profil berhasil")
    _show_profile(updated)

    if updated.get("bio") != new_bio:
        print("  ❌ Bio tidak tersimpan di database")
        sys.exit(1)
    print("  ✅ Bio tersimpan di database")

    profile_picture_url = updated.get("profile_picture_url")
    if not profile_picture_url:
        print("  ❌ profile_picture_url tidak ditemukan di database")
        sys.exit(1)
    print(f"  ✅ profile_picture_url tersimpan: {profile_picture_url}")
    if _check_url_accessible(profile_picture_url):
        print("  ✅ Foto profil dapat diakses di Supabase")
    else:
        print("  ❌ Foto profil tidak dapat diakses dari Supabase")

    _print_step("Upload CV dan verifikasi penyimpanan di Supabase + database")
    with open(TEST_CV_PATH, "rb") as cv_file:
        files = {"file": (os.path.basename(TEST_CV_PATH), cv_file, "application/pdf")}
        cv_response = _extract(_request_json("post", "/cv_upload", token=token, files=files))

    cv_url = cv_response.get("file_url")
    if not cv_url:
        print("  ❌ CV upload tidak mengembalikan file_url")
        sys.exit(1)
    print(f"  ✅ CV upload berhasil: {cv_url}")

    profile_after_cv = _extract(get(f"/freelancers/{freelancer_id}", token))
    print("  Profil setelah upload CV:")
    _show_profile(profile_after_cv)

    if profile_after_cv.get("cv_file_url") != cv_url:
        print("  ❌ cv_file_url di database tidak cocok dengan file_url upload CV")
        sys.exit(1)
    print("  ✅ cv_file_url tersimpan di database")

    if _check_url_accessible(cv_url):
        print("  ✅ CV file dapat diakses di Supabase")
    else:
        print("  ❌ CV file tidak dapat diakses dari Supabase")

    _print_step("Ringkasan final")
    print(f"  bio             : {'tersimpan' if profile_after_cv.get('bio') == new_bio else 'gagal'}")
    print(f"  profile_picture : {'tersimpan' if profile_after_cv.get('profile_picture_url') else 'gagal'}")
    print(f"  cv_file_url     : {'tersimpan' if profile_after_cv.get('cv_file_url') else 'gagal'}")

    print("\n  ✅ Walkthrough verifikasi selesai")


if __name__ == "__main__":
    run()
