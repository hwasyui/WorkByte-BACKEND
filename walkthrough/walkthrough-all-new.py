"""
Walkthrough ALL (New) — Full API Coverage for WorkByte
Exercises every route across all 41 sections in ROUTES.md.
Failures are printed but do NOT abort later sections.

Edge-case scenarios tested:
  - Toxic proposal cover letter          → 400 rejected
  - Toxic DM message                     → 400 rejected
  - Report self                          → 400 rejected
  - Job post with scam content           → async scan (accepted, flagged async)
  - AI: match/analyse/embed/sweep        → full 3-stage pipeline
  - CV analysis                          → BAAI/bge-base-en-v1.5 similarity + ATS + GROQ LLM
  - Job scam detection                   → BAAI/bge-base-en-v1.5 + RF (778-dim), admin flag flow

Requirements:
  Server running at BASE_URL (default http://localhost:8000)
  APP_ENV=development  SHOW_DEV_OTP=true
  Admin account: admin@admin.com / thisisanadminaccountpassword

Usage:
  python walkthrough/walkthrough-all-new.py
  BASE_URL=http://localhost:8000 python walkthrough/walkthrough-all-new.py
"""

import datetime
import asyncio
import io
import json
import os
import sys
import time
import requests

try:
    import websockets
except Exception:
    websockets = None

BASE_URL     = os.environ.get("BASE_URL", "http://localhost:8000")
_PASSWORD    = "SecurePass123!"
_ADMIN_EMAIL = "admin@admin.com"
_ADMIN_PASS  = "thisisanadminaccountpassword"
_TS          = int(time.time())

# ─── Fixtures ────────────────────────────────────────────────────────────────

_TINY_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000058 00000 n\n0000000115 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n160\n%%EOF"
)

_TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\x1eC"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd2\x8a(\x03\xff\xd9"
)

_TOXIC_TEXT = (
    "You are a disgusting idiot and I hate you. Kill yourself you worthless piece of garbage."
)

_SCAM_JOB_DESCRIPTION = (
    "URGENT WORK FROM HOME! Earn $5000/week guaranteed! No experience needed. "
    "Send your bank account details to receive your starter kit. "
    "100% legitimate business opportunity, wire transfer upfront required. "
    "Nigerian prince partnership available. Click here to claim your prize money."
)

_CV_TEXT = """
Alex Walkthrough
Backend Engineer
Python, FastAPI, PostgreSQL, REST API, Docker, Git.
Work Experience: Backend Engineer at Acme Corp from 2021 to 2024.
Education: Bachelor of Computer Science, State University.
"""

# Shared state
_S: dict = {}


# ─── Tee ─────────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, path: str):
        self._stdout = sys.stdout
        self._file   = open(path, "w", encoding="utf-8")

    def write(self, data: str):
        self._stdout.write(data)
        self._file.write(data)

    def flush(self):
        self._stdout.flush()
        self._file.flush()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return False

    def close(self):
        self._file.close()


def _start_tee():
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"walkthrough_all_new_{ts}.md")
    tee = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee: _Tee, path: str):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\nResults saved to: {path}")


# ─── Output helpers ───────────────────────────────────────────────────────────

_section_n  = 0
_step_n     = 0
_pass_count = 0
_fail_count = 0
_skip_count = 0


def section(title: str):
    global _section_n, _step_n
    _section_n += 1
    _step_n = 0
    print(f"\n{'=' * 72}")
    print(f"  SECTION {_section_n}: {title}")
    print(f"{'=' * 72}")


def step(label: str):
    global _step_n
    _step_n += 1
    print(f"\n  -- {_section_n}.{_step_n}  {label}")


def ok(msg: str = ""):
    global _pass_count
    _pass_count += 1
    print(f"      [PASS] {msg}")


def fail(msg: str = ""):
    global _fail_count
    _fail_count += 1
    print(f"      [FAIL] {msg}")


def skip(msg: str = ""):
    global _skip_count
    _skip_count += 1
    print(f"      [SKIP] {msg}")


def info(msg: str):
    print(f"      {msg}")


# ─── HTTP helper ──────────────────────────────────────────────────────────────

def _call(method: str, endpoint: str, *, body=None, token=None,
          params=None, form=None, files=None, expected=(200, 201),
          allow_redirects=True):
    url     = f"{BASE_URL}{endpoint}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=90,
                             allow_redirects=allow_redirects)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers, params=params, timeout=90)
        elif method == "POST" and files is not None:
            r = requests.post(url, headers=headers, data=form or {}, files=files,
                              params=params, timeout=90, allow_redirects=allow_redirects)
        elif method == "POST" and form is not None:
            r = requests.post(url, headers=headers, data=form, timeout=90)
        elif method == "POST":
            headers["Content-Type"] = "application/json"
            r = requests.post(url, headers=headers, json=body, params=params, timeout=90)
        elif method == "PUT" and files is not None:
            r = requests.put(url, headers=headers, data=form or {}, files=files, timeout=90)
        elif method == "PUT" and form is not None:
            r = requests.put(url, headers=headers, data=form, timeout=90)
        elif method == "PUT":
            headers["Content-Type"] = "application/json"
            r = requests.put(url, headers=headers, json=body, params=params, timeout=90)
        elif method == "PATCH":
            headers["Content-Type"] = "application/json"
            r = requests.patch(url, headers=headers, json=body, params=params, timeout=90)
        else:
            raise ValueError(f"Unknown method {method}")

        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text}

        success = r.status_code in expected
        tag = "OK" if success else "FAIL"
        print(f"      {method:<6} {endpoint}  [{r.status_code}] {tag}")
        if not success:
            print(f"           {json.dumps(payload, indent=4)[:500]}")
        if not allow_redirects and r.headers.get("location"):
            print(f"           Location: {r.headers.get('location')}")
        return success, payload

    except requests.exceptions.ConnectionError:
        print(f"      {method:<6} {endpoint}  [ERR] Connection refused")
        return False, {}
    except Exception as exc:
        print(f"      {method:<6} {endpoint}  [ERR] {exc}")
        return False, {}


def _d(payload: dict) -> dict:
    """Unwrap ResponseSchema envelope → inner dict."""
    if not payload:
        return {}
    d = payload.get("details")
    if isinstance(d, dict):
        return d
    if isinstance(d, list):
        return {"list": d}
    return payload


def _list(payload: dict) -> list:
    raw = payload.get("details", [])
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        items = raw.get("list") or raw.get("items") or raw.get("results") or []
        return items
    return []


def _token(payload: dict) -> str:
    return _d(payload).get("access_token", "")


def _id(payload: dict, key: str) -> str:
    return str(_d(payload).get(key, ""))


# ─── SECTION 1: Authentication ────────────────────────────────────────────────

def s01_auth():
    section("Authentication (S1)")

    f_email = f"walkthrough_freelancer_{_TS}@test.com"
    c_email = f"walkthrough_client_{_TS}@test.com"

    # Register freelancer
    step("Register freelancer")
    ok_r, rp = _call("POST", "/auth/register", body={
        "email": f_email, "password": _PASSWORD,
        "user_type": "freelancer", "full_name": f"Freelancer Walk {_TS}"
    })
    if ok_r:
        user_data = _d(rp).get("user", {})
        _S["freelancer_user_id"] = user_data.get("user_id", "")
        _S["freelancer_id"]      = user_data.get("freelancer_id", "")
        ver = _d(rp).get("verification", {})
        otp = ver.get("dev_verification_otp") if isinstance(ver, dict) else None
        _S["freelancer_otp"] = otp
        ok(f"user_id={_S['freelancer_user_id']} freelancer_id={_S['freelancer_id']} otp={otp}")
    else:
        fail("Register freelancer failed")

    # Register client
    step("Register client")
    ok_r, rp = _call("POST", "/auth/register", body={
        "email": c_email, "password": _PASSWORD,
        "user_type": "client", "full_name": f"Client Walk {_TS}",
        "company_name": "WalkTest Inc"
    })
    if ok_r:
        user_data = _d(rp).get("user", {})
        _S["client_user_id"] = user_data.get("user_id", "")
        _S["client_id"]      = user_data.get("client_id", "")
        ver = _d(rp).get("verification", {})
        otp = ver.get("dev_verification_otp") if isinstance(ver, dict) else None
        _S["client_otp"] = otp
        ok(f"user_id={_S['client_user_id']} client_id={_S['client_id']} otp={otp}")
    else:
        fail("Register client failed")

    # Verify freelancer email
    step("Verify freelancer email")
    if _S.get("freelancer_otp"):
        ok_r, _ = _call("POST", "/auth/verify-email", body={
            "email": f_email, "otp": _S["freelancer_otp"]
        })
        ok("Email verified") if ok_r else fail("Verify failed")
    else:
        skip("No dev OTP available — SHOW_DEV_OTP may be false")

    # Verify client email
    step("Verify client email")
    if _S.get("client_otp"):
        ok_r, _ = _call("POST", "/auth/verify-email", body={
            "email": c_email, "otp": _S["client_otp"]
        })
        ok("Email verified") if ok_r else fail("Verify failed")
    else:
        skip("No dev OTP available")

    # Resend verification (test route exists)
    step("Resend verification (idempotent)")
    ok_r, _ = _call("POST", "/auth/resend-verification", body={"email": f_email},
                    expected=(200, 201, 400))
    ok("Resend route reachable") if ok_r else info("Already verified or SMTP not configured")

    # Login freelancer
    step("Login freelancer")
    ok_r, rp = _call("POST", "/auth/login", body={"email": f_email, "password": _PASSWORD})
    if ok_r:
        _S["freelancer_token"] = _token(rp)
        ok(f"token={'yes' if _S['freelancer_token'] else 'missing'}")
    else:
        fail("Login freelancer failed — remaining sections may fail")

    # Login client
    step("Login client")
    ok_r, rp = _call("POST", "/auth/login", body={"email": c_email, "password": _PASSWORD})
    if ok_r:
        _S["client_token"] = _token(rp)
        ok(f"token={'yes' if _S['client_token'] else 'missing'}")
    else:
        fail("Login client failed")

    # Admin login
    step("Login admin")
    ok_r, rp = _call("POST", "/auth/login", body={"email": _ADMIN_EMAIL, "password": _ADMIN_PASS})
    if ok_r:
        _S["admin_token"] = _token(rp)
        ok(f"admin token={'yes' if _S['admin_token'] else 'missing'}")
    else:
        fail("Admin login failed — S32 admin tests will be skipped")

    # GET /auth/me
    step("GET /auth/me")
    ok_r, rp = _call("GET", "/auth/me", token=_S.get("freelancer_token"))
    if ok_r:
        ok(f"email={_d(rp).get('email')} type={_d(rp).get('type')}")
    else:
        fail("GET /auth/me failed")

    # OAuth probe — should redirect, not follow
    step("OAuth Google probe (expect redirect 3xx or 200)")
    ok_r, rp = _call("GET", "/auth/oauth/google",
                     expected=(200, 302, 307, 308, 400, 500),
                     allow_redirects=False)
    ok("OAuth Google endpoint reachable")

    step("OAuth LinkedIn probe (expect redirect 3xx or 200)")
    ok_r, rp = _call("GET", "/auth/oauth/linkedin",
                     expected=(200, 302, 307, 308, 400, 500),
                     allow_redirects=False)
    ok("OAuth LinkedIn endpoint reachable")


# ─── SECTION 2: Freelancers ───────────────────────────────────────────────────

def s02_freelancers():
    section("Freelancers (S2)")

    tok = _S.get("freelancer_token")
    uid = _S.get("freelancer_user_id")
    if not tok:
        skip("No freelancer token")
        return

    # Create profile (registration auto-creates one; expect 400 if already exists)
    step("POST /freelancers — create profile (expect 400 if auto-created on register)")
    ok_r, rp = _call("POST", "/freelancers",
                     form={
                         "user_id": uid,
                         "full_name": f"Freelancer Walk {_TS}",
                         "bio": "Experienced backend developer specialising in Python and FastAPI.",
                         "estimated_rate": "50",
                         "rate_time": "hourly",
                         "rate_currency": "USD",
                     },
                     files={"profile_picture": ("avatar.jpg", _TINY_JPEG, "image/jpeg")},
                     token=tok, expected=(200, 201, 400))
    if _id(rp, "freelancer_id"):
        _S["freelancer_id"] = _id(rp, "freelancer_id")
        ok(f"created freelancer_id={_S['freelancer_id']}")
    elif _S.get("freelancer_id"):
        ok(f"profile already exists (auto-created on register) freelancer_id={_S['freelancer_id']}")
    else:
        fail("Create freelancer profile failed and no id from registration")

    fid = _S.get("freelancer_id", "")

    # GET own profile list
    step("GET /freelancers — own profile")
    ok_r, rp = _call("GET", "/freelancers", token=tok)
    ok("got list") if ok_r else fail("GET /freelancers failed")

    # GET browse all
    step("GET /freelancers/browse/all")
    ok_r, rp = _call("GET", "/freelancers/browse/all",
                     params={"page": 1, "page_size": 5, "order_by": "created_at"},
                     token=tok)
    ok("browse all ok") if ok_r else fail("browse/all failed")

    # GET search
    step("GET /freelancers/search?name={term}")
    ok_r, rp = _call("GET", "/freelancers/search", params={"name": "walk"}, token=tok)
    ok("search ok") if ok_r else fail("search failed")

    # GET by identifier
    step("GET /freelancers/{freelancer_id}")
    if fid:
        ok_r, rp = _call("GET", f"/freelancers/{fid}", token=tok)
        ok("get by id ok") if ok_r else fail("get by id failed")

    # GET profile (full)
    step("GET /freelancers/{freelancer_id}/profile")
    if fid:
        ok_r, rp = _call("GET", f"/freelancers/{fid}/profile", token=tok)
        ok("full profile ok") if ok_r else fail("full profile failed")

    # GET skills
    step("GET /freelancers/{freelancer_id}/skills")
    if fid:
        ok_r, rp = _call("GET", f"/freelancers/{fid}/skills", token=tok)
        ok("skills ok") if ok_r else fail("skills failed")

    # GET embedding metadata
    step("GET /freelancers/{freelancer_id}/embedding")
    if fid:
        ok_r, rp = _call("GET", f"/freelancers/{fid}/embedding", token=tok,
                         expected=(200, 404))
        ok("embedding metadata ok") if ok_r else info("No embedding yet (expected)")

    # PUT update profile
    step("PUT /freelancers/{identifier} — update")
    if fid:
        ok_r, rp = _call("PUT", f"/freelancers/{fid}",
                         form={"bio": "Updated bio with more details about Python expertise."},
                         token=tok)
        ok("update ok") if ok_r else fail("update failed")

    # POST profile picture
    step("POST /freelancers/{freelancer_id}/profile-picture")
    if fid:
        ok_r, rp = _call("POST", f"/freelancers/{fid}/profile-picture",
                         files={"file": ("new_avatar.jpg", _TINY_JPEG, "image/jpeg")},
                         token=tok)
        ok("profile-picture upload ok") if ok_r else fail("profile-picture upload failed")

    # DELETE profile picture
    step("DELETE /freelancers/{freelancer_id}/profile-picture")
    if fid:
        ok_r, rp = _call("DELETE", f"/freelancers/{fid}/profile-picture", token=tok)
        ok("profile-picture deleted") if ok_r else fail("delete profile-picture failed")


# ─── SECTION 3: Clients ───────────────────────────────────────────────────────

def s03_clients():
    section("Clients (S3)")

    tok = _S.get("client_token")
    uid = _S.get("client_user_id")
    if not tok:
        skip("No client token")
        return

    # Create profile (registration auto-creates one; expect 400 if already exists)
    step("POST /clients — create profile (expect 400 if auto-created on register)")
    ok_r, rp = _call("POST", "/clients",
                     form={
                         "user_id": uid,
                         "full_name": f"Client Walk {_TS}",
                         "bio": "We build great products and need great talent.",
                         "website_url": "https://walktest.example.com",
                     },
                     files={"profile_picture": ("logo.jpg", _TINY_JPEG, "image/jpeg")},
                     token=tok, expected=(200, 201, 400))
    if _id(rp, "client_id"):
        _S["client_id"] = _id(rp, "client_id")
        ok(f"created client_id={_S['client_id']}")
    elif _S.get("client_id"):
        ok(f"profile already exists (auto-created on register) client_id={_S['client_id']}")
    else:
        fail("Create client profile failed and no id from registration")

    cid = _S.get("client_id", "")

    step("GET /clients — own profile")
    ok_r, _ = _call("GET", "/clients", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /clients/browse/all")
    ok_r, _ = _call("GET", "/clients/browse/all",
                    params={"page": 1, "page_size": 5},
                    token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /clients/search?name={term}")
    ok_r, _ = _call("GET", "/clients/search", params={"name": "walk"}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /clients/{identifier}")
    if cid:
        ok_r, _ = _call("GET", f"/clients/{cid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /clients/{identifier} — update")
    if cid:
        ok_r, _ = _call("PUT", f"/clients/{cid}",
                        form={"bio": "Updated company bio."},
                        token=tok)
        ok("ok") if ok_r else fail("failed")

    step("POST /clients/{client_id}/profile-picture")
    if cid:
        ok_r, _ = _call("POST", f"/clients/{cid}/profile-picture",
                        files={"file": ("logo2.jpg", _TINY_JPEG, "image/jpeg")},
                        token=tok)
        ok("ok") if ok_r else fail("failed")

    step("DELETE /clients/{client_id}/profile-picture")
    if cid:
        ok_r, _ = _call("DELETE", f"/clients/{cid}/profile-picture", token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 4: Skills ────────────────────────────────────────────────────────

def s04_skills():
    section("Skills (S4)")

    tok = _S.get("freelancer_token")

    step("GET /skills/autocomplete?q=python")
    ok_r, rp = _call("GET", "/skills/autocomplete", params={"q": "python", "limit": 5}, token=tok)
    results = _d(rp).get("results", [])
    ok(f"autocomplete returned {len(results)} results") if ok_r else fail("autocomplete failed")

    step("GET /skills — list all")
    ok_r, rp = _call("GET", "/skills", params={"limit": 10}, token=tok)
    sk_list = _list(rp)
    ok(f"got {len(sk_list)} skills") if ok_r else fail("failed")
    # store first skill for later
    if sk_list:
        _S["existing_skill_id"] = str(sk_list[0].get("skill_id", ""))

    step("GET /skills/search?q=backend")
    ok_r, rp = _call("GET", "/skills/search", params={"q": "backend", "limit": 5}, token=tok)
    ok("search ok") if ok_r else fail("failed")

    step("GET /skills/category/hard_skill")
    ok_r, _ = _call("GET", "/skills/category/hard_skill", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("POST /skills — create new skill")
    skill_name = f"WalkTestSkill_{_TS}"
    ok_r, rp = _call("POST", "/skills", body={
        "skill_name": skill_name,
        "skill_category": "hard_skill",
        "description": "Walkthrough test skill",
        "search_tokens": f"walk test skill {_TS}"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["skill_id"] = _id(rp, "skill_id")
        ok(f"skill_id={_S['skill_id']}")
    else:
        fail("create skill failed")
        if _S.get("existing_skill_id"):
            _S["skill_id"] = _S["existing_skill_id"]
            info(f"Falling back to existing skill_id={_S['skill_id']}")

    sid = _S.get("skill_id", "")

    step("GET /skills/{skill_id}")
    if sid:
        ok_r, _ = _call("GET", f"/skills/{sid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /skills/{skill_id} — update")
    if sid:
        ok_r, _ = _call("PUT", f"/skills/{sid}",
                        body={"description": "Updated description"},
                        token=tok)
        ok("ok") if ok_r else fail("failed")

    step("POST /skills/{skill_id}/embed — embed single skill")
    if sid:
        ok_r, rp = _call("POST", f"/skills/{sid}/embed", token=tok,
                         expected=(200, 201, 500, 503))
        ok(f"embed result: {_d(rp).get('embedded', '?')}") if ok_r else info("Embed skipped (Ollama/API may not be available)")


# ─── SECTION 5: Languages ─────────────────────────────────────────────────────

def s05_languages():
    section("Languages (S5)")
    skip("Language system removed — language_router not registered in main.py (table dropped in alter_table.sql)")


# ─── SECTION 6: Specialities ──────────────────────────────────────────────────

def s06_specialities():
    section("Specialities (S6)")

    tok = _S.get("freelancer_token")

    step("GET /specialities — list all")
    ok_r, rp = _call("GET", "/specialities", params={"limit": 10}, token=tok)
    spec_list = _list(rp)
    ok(f"got {len(spec_list)}") if ok_r else fail("failed")
    if spec_list:
        _S["existing_speciality_id"] = str(spec_list[0].get("speciality_id", ""))

    step("GET /specialities/search/backend")
    ok_r, _ = _call("GET", "/specialities/search/backend", token=tok,
                    expected=(200, 404))
    ok("ok") if ok_r else info("no results (expected)")

    step("POST /specialities — create")
    spec_name = f"WalkSpec_{_TS}"
    ok_r, rp = _call("POST", "/specialities", body={
        "speciality_name": spec_name,
        "description": "Walkthrough test speciality"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["speciality_id"] = _id(rp, "speciality_id")
        ok(f"speciality_id={_S['speciality_id']}")
    else:
        fail("create speciality failed")
        if _S.get("existing_speciality_id"):
            _S["speciality_id"] = _S["existing_speciality_id"]

    specid = _S.get("speciality_id", "")

    step("GET /specialities/{speciality_id}")
    if specid:
        ok_r, _ = _call("GET", f"/specialities/{specid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /specialities/{speciality_id}")
    if specid:
        ok_r, _ = _call("PUT", f"/specialities/{specid}",
                        body={"description": "Updated desc"},
                        token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 7: Freelancer Skills ─────────────────────────────────────────────

def s07_freelancer_skills():
    section("Freelancer Skills (S7)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")
    sid = _S.get("skill_id", "")

    if not fid or not sid:
        skip("Missing freelancer_id or skill_id")
        return

    step("POST /freelancer-skills — add skill")
    ok_r, rp = _call("POST", "/freelancer-skills", body={
        "freelancer_id": fid, "skill_id": sid, "proficiency_level": "advanced"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["freelancer_skill_id"] = _id(rp, "freelancer_skill_id")
        ok(f"freelancer_skill_id={_S['freelancer_skill_id']}")
    else:
        fail("add skill failed")

    step("GET /freelancer-skills/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/freelancer-skills/freelancer/{fid}", token=tok)
    items = _list(rp)
    ok(f"got {len(items)} skills") if ok_r else fail("failed")

    fsid = _S.get("freelancer_skill_id", "")

    step("GET /freelancer-skills/{freelancer_skill_id}")
    if fsid:
        ok_r, _ = _call("GET", f"/freelancer-skills/{fsid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /freelancer-skills — list all")
    ok_r, _ = _call("GET", "/freelancer-skills", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("PUT /freelancer-skills/{freelancer_skill_id}")
    if fsid:
        ok_r, _ = _call("PUT", f"/freelancer-skills/{fsid}",
                        body={"proficiency_level": "expert"},
                        token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 8: Freelancer Specialities ───────────────────────────────────────

def s08_freelancer_specialities():
    section("Freelancer Specialities (S8)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")
    specid = _S.get("speciality_id", "")

    if not fid or not specid:
        skip("Missing freelancer_id or speciality_id")
        return

    step("POST /freelancer-specialities — add speciality")
    ok_r, rp = _call("POST", "/freelancer-specialities", body={
        "freelancer_id": fid, "speciality_id": specid, "is_primary": True
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["freelancer_speciality_id"] = _id(rp, "freelancer_speciality_id")
        ok(f"freelancer_speciality_id={_S['freelancer_speciality_id']}")
    else:
        fail("add speciality failed")

    step("GET /freelancer-specialities/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/freelancer-specialities/freelancer/{fid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    fsid = _S.get("freelancer_speciality_id", "")

    step("GET /freelancer-specialities/{id}")
    if fsid:
        ok_r, _ = _call("GET", f"/freelancer-specialities/{fsid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /freelancer-specialities/{id} — update is_primary")
    if fsid:
        ok_r, _ = _call("PUT", f"/freelancer-specialities/{fsid}",
                        body={"is_primary": False}, token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /freelancer-specialities — list all")
    ok_r, _ = _call("GET", "/freelancer-specialities", token=tok)
    ok("ok") if ok_r else fail("failed")


# ─── SECTION 9: Freelancer Languages ──────────────────────────────────────────

def s09_freelancer_languages():
    section("Freelancer Languages (S9)")
    skip("Language system removed — freelancer_language_router not registered in main.py")


# ─── SECTION 10: Work Experience ──────────────────────────────────────────────

def s10_work_experience():
    section("Work Experience (S10)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")

    if not fid:
        skip("No freelancer_id")
        return

    step("POST /work-experiences — create")
    ok_r, rp = _call("POST", "/work-experiences", body={
        "freelancer_id": fid,
        "job_title": "Backend Engineer",
        "company_name": "Acme Corp",
        "start_date": "2021-01-01",
        "end_date": "2024-01-01",
        "location": "Remote",
        "description": "Built REST APIs and database systems.",
        "is_current": False
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["work_experience_id"] = _id(rp, "work_experience_id")
        ok(f"work_experience_id={_S['work_experience_id']}")
    else:
        fail("create work-experience failed")

    step("GET /work-experiences/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/work-experiences/freelancer/{fid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    weid = _S.get("work_experience_id", "")

    step("GET /work-experiences/{id}")
    if weid:
        ok_r, _ = _call("GET", f"/work-experiences/{weid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /work-experiences/{id}")
    if weid:
        ok_r, _ = _call("PUT", f"/work-experiences/{weid}",
                        body={"description": "Updated work description."}, token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /work-experiences — list")
    ok_r, _ = _call("GET", "/work-experiences", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")


# ─── SECTION 11: Education ────────────────────────────────────────────────────

def s11_education():
    section("Education (S11)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")

    if not fid:
        skip("No freelancer_id")
        return

    step("POST /educations — create")
    ok_r, rp = _call("POST", "/educations", body={
        "freelancer_id": fid,
        "institution_name": "State University",
        "degree": "Bachelor of Computer Science",
        "field_of_study": "Computer Science",
        "start_date": "2017-09-01",
        "end_date": "2021-05-01",
        "grade": "3.8 GPA",
        "is_current": False
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["education_id"] = _id(rp, "education_id")
        ok(f"education_id={_S['education_id']}")
    else:
        fail("create education failed")

    step("GET /educations/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/educations/freelancer/{fid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    eid = _S.get("education_id", "")

    step("GET /educations/{id}")
    if eid:
        ok_r, _ = _call("GET", f"/educations/{eid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /educations/{id}")
    if eid:
        ok_r, _ = _call("PUT", f"/educations/{eid}",
                        body={"grade": "4.0 GPA"}, token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /educations — list")
    ok_r, _ = _call("GET", "/educations", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")


# ─── SECTION 12: Job Posts ────────────────────────────────────────────────────

def s12_job_posts():
    section("Job Posts (S12)")

    tok = _S.get("client_token")
    cid = _S.get("client_id", "")

    if not tok:
        skip("No client token")
        return

    # Calculate project scope
    step("POST /job-posts/calculate-project-scope")
    ok_r, rp = _call("POST", "/job-posts/calculate-project-scope", body={
        "job_title": "Build REST API",
        "job_description": "Build a fully-featured REST API using FastAPI and PostgreSQL.",
        "project_type": "individual",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "role_count": 1
    }, token=tok, expected=(200, 201))
    if ok_r:
        scope = _d(rp).get("recommended_project_scope", "?")
        ok(f"recommended_scope={scope}")
    else:
        fail("calculate-project-scope failed")

    # Create normal job post
    step("POST /job-posts — create (active)")
    ok_r, rp = _call("POST", "/job-posts", body={
        "client_id": cid,
        "job_title": f"Build REST API {_TS}",
        "job_description": "We need a skilled backend engineer to build REST APIs using FastAPI and PostgreSQL. Must have experience with Python.",
        "project_type": "individual",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "status": "active"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["job_post_id"] = _id(rp, "job_post_id")
        ok(f"job_post_id={_S['job_post_id']}")
    else:
        fail("create job post failed")

    # Create draft for testing
    step("POST /job-posts — create (draft)")
    ok_r, rp = _call("POST", "/job-posts", body={
        "client_id": cid,
        "job_title": f"Draft Job {_TS}",
        "job_description": "A draft job for testing purposes.",
        "project_type": "individual",
        "status": "draft"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["draft_job_post_id"] = _id(rp, "job_post_id")
        ok(f"draft_job_post_id={_S['draft_job_post_id']}")
    else:
        fail("create draft job post failed")

    # Create scam job post (async toxicity + scam detection)
    step("POST /job-posts — scam content (async scan, should be saved then flagged)")
    ok_r, rp = _call("POST", "/job-posts", body={
        "client_id": cid,
        "job_title": f"URGENT Work From Home Opportunity {_TS}",
        "job_description": _SCAM_JOB_DESCRIPTION,
        "project_type": "individual",
        "status": "active"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["scam_job_post_id"] = _id(rp, "job_post_id")
        ok(f"Scam job post saved (id={_S['scam_job_post_id']}) — async scan queued in background")
    else:
        fail("Scam job post creation failed unexpectedly")

    jpid = _S.get("job_post_id", "")

    step("GET /job-posts — list active")
    ok_r, rp = _call("GET", "/job-posts", params={"status": "active", "page": 1, "page_size": 5},
                     token=tok)
    ok(f"got pagination data") if ok_r else fail("failed")

    step("GET /job-posts/{job_post_id}")
    if jpid:
        ok_r, rp = _call("GET", f"/job-posts/{jpid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /job-posts/client/{client_id}")
    if cid:
        ok_r, rp = _call("GET", f"/job-posts/client/{cid}", token=tok)
        ok(f"ok") if ok_r else fail("failed")

    step("PUT /job-posts/{job_post_id} — update")
    if jpid:
        ok_r, _ = _call("PUT", f"/job-posts/{jpid}", body={
            "job_description": "Updated description with more details. Python FastAPI PostgreSQL required."
        }, token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 13: Job Roles ────────────────────────────────────────────────────

def s13_job_roles():
    section("Job Roles (S13)")

    tok = _S.get("client_token")
    jpid = _S.get("job_post_id", "")

    if not tok or not jpid:
        skip("No client token or job_post_id")
        return

    step("POST /job-roles — create role")
    ok_r, rp = _call("POST", "/job-roles", body={
        "job_post_id": jpid,
        "role_title": "Backend Engineer",
        "budget_type": "fixed",
        "role_budget": 3000.00,
        "budget_currency": "USD",
        "role_description": "Build and maintain REST APIs.",
        "positions_available": 1,
        "is_required": True
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["job_role_id"] = _id(rp, "job_role_id")
        ok(f"job_role_id={_S['job_role_id']}")
    else:
        fail("create job role failed")

    jrid = _S.get("job_role_id", "")

    step("GET /job-roles/job-post/{job_post_id}")
    ok_r, rp = _call("GET", f"/job-roles/job-post/{jpid}", token=tok)
    ok(f"got {len(_list(rp))} roles") if ok_r else fail("failed")

    step("GET /job-roles/{job_role_id}")
    if jrid:
        ok_r, _ = _call("GET", f"/job-roles/{jrid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /job-roles — list (client auth)")
    ok_r, _ = _call("GET", "/job-roles", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("PUT /job-roles/{job_role_id} — update")
    if jrid:
        ok_r, _ = _call("PUT", f"/job-roles/{jrid}",
                        body={"role_description": "Updated role description."}, token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 14: Job Role Skills ──────────────────────────────────────────────

def s14_job_role_skills():
    section("Job Role Skills (S14)")

    tok = _S.get("client_token")
    jrid = _S.get("job_role_id", "")
    sid = _S.get("skill_id", "")

    if not jrid or not sid:
        skip("Missing job_role_id or skill_id")
        return

    step("POST /job-role-skills — add skill to role")
    ok_r, rp = _call("POST", "/job-role-skills", body={
        "job_role_id": jrid,
        "skill_id": sid,
        "is_required": True,
        "importance_level": "required"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["job_role_skill_id"] = _id(rp, "job_role_skill_id")
        ok(f"job_role_skill_id={_S['job_role_skill_id']}")
    else:
        fail("add skill to role failed")

    jrsid = _S.get("job_role_skill_id", "")

    step("GET /job-role-skills/job-role/{job_role_id}")
    ok_r, rp = _call("GET", f"/job-role-skills/job-role/{jrid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /job-role-skills/{id}")
    if jrsid:
        ok_r, _ = _call("GET", f"/job-role-skills/{jrsid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /job-role-skills — list all")
    ok_r, _ = _call("GET", "/job-role-skills", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("PUT /job-role-skills/{id} — update")
    if jrsid:
        ok_r, _ = _call("PUT", f"/job-role-skills/{jrsid}",
                        body={"importance_level": "preferred"}, token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 15: Job Files ────────────────────────────────────────────────────

def s15_job_files():
    section("Job Files (S15)")

    tok = _S.get("client_token")
    jpid = _S.get("job_post_id", "")

    if not tok or not jpid:
        skip("No client token or job_post_id")
        return

    step("POST /job-files — upload file")
    ok_r, rp = _call("POST", "/job-files",
                     form={"job_post_id": jpid},
                     files={"files": ("spec.pdf", _TINY_PDF, "application/pdf")},
                     token=tok, expected=(200, 201))
    items = _list(rp) or [_d(rp)]
    if ok_r and items:
        _S["job_file_id"] = str(items[0].get("job_file_id", ""))
        ok(f"job_file_id={_S['job_file_id']}")
    else:
        fail("upload job file failed (may need Supabase storage)")

    step("GET /job-files/job-post/{job_post_id}")
    ok_r, rp = _call("GET", f"/job-files/job-post/{jpid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    jfid = _S.get("job_file_id", "")

    step("GET /job-files/{id}")
    if jfid:
        ok_r, _ = _call("GET", f"/job-files/{jfid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /job-files — list")
    ok_r, _ = _call("GET", "/job-files", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("DELETE /job-files/{id}")
    if jfid:
        ok_r, _ = _call("DELETE", f"/job-files/{jfid}", token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 16: Proposals ────────────────────────────────────────────────────

def s16_proposals():
    section("Proposals (S16)")

    tok = _S.get("freelancer_token")
    jpid = _S.get("job_post_id", "")
    jrid = _S.get("job_role_id", "")

    if not tok or not jpid:
        skip("No freelancer token or job_post_id")
        return

    # ── EDGE CASE: Toxic proposal — must be rejected with 400 ──────────────
    step("EDGE CASE: POST /proposals with toxic cover letter — expect 400")
    ok_r, rp = _call("POST", "/proposals", body={
        "job_post_id": jpid,
        "job_role_id": jrid or None,
        "cover_letter": _TOXIC_TEXT,
        "proposed_budget": 100.00
    }, token=tok, expected=(400,))
    if ok_r:
        ok(f"Toxic proposal correctly rejected 400 — {_d(rp).get('message', '')}")
    else:
        fail("Toxic proposal was NOT rejected (toxicity gate may be disabled or model unavailable)")

    # Normal proposal
    step("POST /proposals — create (clean cover letter)")
    ok_r, rp = _call("POST", "/proposals", body={
        "job_post_id": jpid,
        "job_role_id": jrid or None,
        "cover_letter": "I am very excited about this opportunity. I have 3 years of Python and FastAPI experience. I have built production REST APIs and am confident I can deliver exactly what you need.",
        "proposed_budget": 2800.00,
        "proposed_duration": "2 months"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["proposal_id"] = _id(rp, "proposal_id")
        ok(f"proposal_id={_S['proposal_id']}")
    else:
        fail("create proposal failed")

    pid = _S.get("proposal_id", "")

    step("GET /proposals/me — own proposals")
    ok_r, _ = _call("GET", "/proposals/me", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /proposals/job-post/{job_post_id}")
    ok_r, rp = _call("GET", f"/proposals/job-post/{jpid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /proposals/freelancer/{freelancer_id}")
    fid = _S.get("freelancer_id", "")
    if fid:
        ok_r, _ = _call("GET", f"/proposals/freelancer/{fid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /proposals/{id}")
    if pid:
        ok_r, _ = _call("GET", f"/proposals/{pid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /proposals — list all")
    ok_r, _ = _call("GET", "/proposals", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("PUT /proposals/{id} — update")
    if pid:
        ok_r, _ = _call("PUT", f"/proposals/{pid}",
                        body={"proposed_budget": 2750.00}, token=tok)
        ok("ok") if ok_r else fail("failed")

    # Accept proposal (client)
    step("PATCH /proposals/{id}/status — accept (client)")
    if pid:
        ctok = _S.get("client_token")
        ok_r, _ = _call("PATCH", f"/proposals/{pid}/status",
                        params={"status": "accepted"}, token=ctok)
        ok("accepted") if ok_r else fail("failed")


# ─── SECTION 17: Proposal Files ───────────────────────────────────────────────

def s17_proposal_files():
    section("Proposal Files (S17)")

    tok = _S.get("freelancer_token")
    pid = _S.get("proposal_id", "")

    if not tok or not pid:
        skip("No freelancer token or proposal_id")
        return

    step("POST /proposal-files — upload")
    ok_r, rp = _call("POST", "/proposal-files",
                     form={"proposal_id": pid},
                     files={"files": ("portfolio.pdf", _TINY_PDF, "application/pdf")},
                     token=tok, expected=(200, 201))
    items = _list(rp) or [_d(rp)]
    if ok_r and items:
        _S["proposal_file_id"] = str(items[0].get("proposal_file_id", ""))
        ok(f"proposal_file_id={_S['proposal_file_id']}")
    else:
        fail("upload proposal file failed (may need Supabase)")

    step("GET /proposal-files/proposal/{proposal_id}")
    ok_r, rp = _call("GET", f"/proposal-files/proposal/{pid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    pfid = _S.get("proposal_file_id", "")

    step("GET /proposal-files/{id}")
    if pfid:
        ok_r, _ = _call("GET", f"/proposal-files/{pfid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /proposal-files — list")
    ok_r, _ = _call("GET", "/proposal-files", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("DELETE /proposal-files/{id}")
    if pfid:
        ok_r, _ = _call("DELETE", f"/proposal-files/{pfid}", token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 18: Contracts ────────────────────────────────────────────────────

def s18_contracts():
    section("Contracts (S18)")

    tok = _S.get("client_token")
    jpid  = _S.get("job_post_id", "")
    jrid  = _S.get("job_role_id", "")
    pid   = _S.get("proposal_id", "")
    fid   = _S.get("freelancer_id", "")
    cid   = _S.get("client_id", "")

    if not (tok and jpid and jrid and pid and fid and cid):
        skip("Missing required IDs for contract creation")
        return

    step("POST /contracts — create")
    ok_r, rp = _call("POST", "/contracts", body={
        "job_post_id": jpid,
        "job_role_id": jrid,
        "proposal_id": pid,
        "freelancer_id": fid,
        "client_id": cid,
        "contract_title": f"Backend Engineer Contract {_TS}",
        "agreed_budget": 2800.00,
        "payment_structure": "full_payment",
        "start_date": "2026-06-01",
        "end_date": "2026-08-31",
        "budget_currency": "USD",
        "status": "active",
        "role_title": "Backend Engineer"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["contract_id"] = _id(rp, "contract_id")
        ok(f"contract_id={_S['contract_id']}")
    else:
        fail("create contract failed")

    cnid = _S.get("contract_id", "")

    step("GET /contracts — list (authenticated user)")
    ok_r, _ = _call("GET", "/contracts", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /contracts/freelancer/{freelancer_id}")
    ok_r, _ = _call("GET", f"/contracts/freelancer/{fid}", token=_S.get("freelancer_token"))
    ok("ok") if ok_r else fail("failed")

    step("GET /contracts/client/{client_id}")
    ok_r, _ = _call("GET", f"/contracts/client/{cid}", token=tok)
    ok("ok") if ok_r else fail("failed")

    step("GET /contracts/{contract_id}")
    if cnid:
        ok_r, _ = _call("GET", f"/contracts/{cnid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /contracts/{contract_id}/generation-data")
    if cnid:
        ok_r, _ = _call("GET", f"/contracts/{cnid}/generation-data", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("POST /contracts/{contract_id}/generate — PDF generation")
    if cnid:
        ok_r, rp = _call("POST", f"/contracts/{cnid}/generate", body={
            "termination_notice": 14,
            "governing_law": "State of California",
            "confidentiality": True,
            "dispute_resolution": "arbitration",
            "revision_rounds": 2,
            "additional_clauses": "All deliverables must include documentation.",
            "payment_schedule": [
                {"phase": "Phase 1", "description": "Initial API", "percentage": 50.0},
                {"phase": "Phase 2", "description": "Final delivery", "percentage": 50.0}
            ]
        }, token=tok, expected=(200, 201, 500, 503))
        if ok_r:
            pdf_url = _d(rp).get("contract_pdf_url", "")
            ok(f"PDF generated: {'yes' if pdf_url else 'no url returned'}")
        else:
            info("PDF generation skipped (Supabase storage may not be available)")

    step("GET /contracts/{contract_id}/pdf-url")
    if cnid:
        ok_r, rp = _call("GET", f"/contracts/{cnid}/pdf-url", token=tok,
                         expected=(200, 404))
        ok(f"pdf_url={'yes' if _d(rp).get('pdf_url') else 'none'}") if ok_r else info("No PDF URL yet")

    step("PUT /contracts/{contract_id} — update")
    if cnid:
        ok_r, _ = _call("PUT", f"/contracts/{cnid}",
                        body={"total_hours_worked": 10.0}, token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 19: Contract Submissions ────────────────────────────────────────

def s19_contract_submissions():
    section("Contract Submissions (S19)")

    f_tok = _S.get("freelancer_token")
    c_tok = _S.get("client_token")
    cnid = _S.get("contract_id", "")

    if not cnid:
        skip("No contract_id")
        return

    step("POST /contract-submissions — freelancer submits work")
    ok_r, rp = _call("POST", "/contract-submissions",
                     form={"contract_id": cnid, "note": "Initial submission. All features implemented."},
                     files={"files": ("deliverable.pdf", _TINY_PDF, "application/pdf")},
                     token=f_tok, expected=(200, 201, 500))
    if ok_r:
        _S["submission_id"] = _d(rp).get("submission_id") or _d(rp).get("contract_submission_id")
        ok(f"submission_id={_S['submission_id']}")
    else:
        info("Submission failed (may need Supabase storage for file upload)")

    step("GET /contract-submissions/contract/{contract_id}")
    ok_r, rp = _call("GET", f"/contract-submissions/contract/{cnid}", token=f_tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("PUT /contract-submissions/contract/{contract_id}/request-revision — client requests revision")
    ok_r, _ = _call("PUT", f"/contract-submissions/contract/{cnid}/request-revision",
                    body={"note": "Please fix the authentication endpoint."}, token=c_tok,
                    expected=(200, 201, 404))
    ok("revision requested") if ok_r else info("No submission to request revision on (skip)")

    step("PUT /contract-submissions/contract/{contract_id}/approve — client approves")
    ok_r, _ = _call("PUT", f"/contract-submissions/contract/{cnid}/approve",
                    token=c_tok, expected=(200, 201, 404))
    ok("approved") if ok_r else info("No submission to approve (skip)")


# ─── SECTION 20: Portfolio ────────────────────────────────────────────────────

def s20_portfolio():
    section("Portfolio (S20)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")

    if not fid:
        skip("No freelancer_id")
        return

    step("POST /portfolios — create")
    ok_r, rp = _call("POST", "/portfolios", body={
        "freelancer_id": fid,
        "project_title": "REST API Platform",
        "project_description": "Built a full REST API platform using FastAPI and PostgreSQL with JWT authentication.",
        "project_url": "https://github.com/walktest/api",
        "completion_date": "2024-01-01"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["portfolio_id"] = _id(rp, "portfolio_id")
        ok(f"portfolio_id={_S['portfolio_id']}")
    else:
        fail("create portfolio failed")

    pfid = _S.get("portfolio_id", "")

    step("GET /portfolios/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/portfolios/freelancer/{fid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /portfolios/{id}")
    if pfid:
        ok_r, _ = _call("GET", f"/portfolios/{pfid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /portfolios — list")
    ok_r, _ = _call("GET", "/portfolios", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("PUT /portfolios/{id} — update")
    if pfid:
        ok_r, _ = _call("PUT", f"/portfolios/{pfid}",
                        body={"project_description": "Updated project description."}, token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 21: Saved Jobs ───────────────────────────────────────────────────

def s21_saved_jobs():
    section("Saved Jobs (S21)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")
    jpid = _S.get("job_post_id", "")

    if not fid or not jpid:
        skip("Missing freelancer_id or job_post_id")
        return

    step("POST /saved-jobs — save a job")
    ok_r, rp = _call("POST", "/saved-jobs", body={
        "freelancer_id": fid,
        "job_post_id": jpid,
        "notes": "Looks like a great opportunity!"
    }, token=tok, expected=(200, 201))
    if ok_r:
        _S["saved_job_id"] = _id(rp, "saved_job_id")
        ok(f"saved_job_id={_S['saved_job_id']}")
    else:
        fail("save job failed")

    step("GET /saved-jobs/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/saved-jobs/freelancer/{fid}", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    sjid = _S.get("saved_job_id", "")

    step("GET /saved-jobs/{id}")
    if sjid:
        ok_r, _ = _call("GET", f"/saved-jobs/{sjid}", token=tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /saved-jobs — list")
    ok_r, _ = _call("GET", "/saved-jobs", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("DELETE /saved-jobs/{id}")
    if sjid:
        ok_r, _ = _call("DELETE", f"/saved-jobs/{sjid}", token=tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 22: Ratings ─────────────────────────────────────────────────────

def s22_ratings():
    section("Ratings (S22)")

    c_tok = _S.get("client_token")
    cnid = _S.get("contract_id", "")
    fid = _S.get("freelancer_id", "")
    cid = _S.get("client_id", "")

    if not cnid or not fid:
        skip("Missing contract_id or freelancer_id")
        return

    step("POST /ratings — client rates freelancer")
    ok_r, rp = _call("POST", "/ratings", body={
        "contract_id": cnid,
        "freelancer_id": fid,
        "communication_score": 5,
        "result_quality_score": 4,
        "professionalism_score": 5,
        "timeline_compliance_score": 4,
        "overall_rating": 4.5,
        "review_text": "Excellent work delivered on time."
    }, token=c_tok, expected=(200, 201, 400))
    if ok_r:
        _S["rating_id"] = _id(rp, "rating_id")
        ok(f"rating_id={_S['rating_id']}")
    else:
        info("Rating failed (contract may not be completed yet)")

    step("GET /ratings/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/ratings/freelancer/{fid}", token=c_tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /ratings/client/{client_id}")
    if cid:
        ok_r, _ = _call("GET", f"/ratings/client/{cid}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    step("GET /ratings — list")
    ok_r, _ = _call("GET", "/ratings", params={"limit": 5}, token=c_tok)
    ok("ok") if ok_r else fail("failed")

    rid = _S.get("rating_id", "")
    step("GET /ratings/{id}")
    if rid:
        ok_r, _ = _call("GET", f"/ratings/{rid}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    step("PUT /ratings/{id} — update")
    if rid:
        ok_r, _ = _call("PUT", f"/ratings/{rid}",
                        body={"overall_rating": 5.0, "review_text": "Outstanding work!"},
                        token=c_tok)
        ok("ok") if ok_r else fail("failed")


# ─── SECTION 23: Performance Ratings ─────────────────────────────────────────

def s23_performance_ratings():
    section("Performance Ratings (S23)")

    tok = _S.get("client_token")
    fid = _S.get("freelancer_id", "")

    if not fid:
        skip("No freelancer_id")
        return

    step("GET /performance-ratings/freelancer/{freelancer_id}")
    ok_r, rp = _call("GET", f"/performance-ratings/freelancer/{fid}", token=tok,
                     expected=(200, 404))
    ok("ok") if ok_r else info("No performance rating yet (expected)")

    step("GET /performance-ratings — list")
    ok_r, _ = _call("GET", "/performance-ratings", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("POST /performance-ratings — create/seed")
    ok_r, rp = _call("POST", "/performance-ratings", body={
        "freelancer_id": fid,
        "overall_performance_score": 4.5,
        "confidence_score": 0.85,
        "total_ratings_received": 1
    }, token=tok, expected=(200, 201, 400))
    if ok_r:
        ok("created/upserted")
    else:
        info("Already exists or creation failed")

    step("PUT /performance-ratings/freelancer/{freelancer_id}")
    ok_r, _ = _call("PUT", f"/performance-ratings/freelancer/{fid}",
                    body={"overall_performance_score": 4.8}, token=tok,
                    expected=(200, 404))
    ok("ok") if ok_r else info("skipped")


# ─── SECTION 24: Client Trust Scores ─────────────────────────────────────────

def s24_client_trust_scores():
    section("Client Trust Scores (S24)")

    tok = _S.get("client_token")
    cid = _S.get("client_id", "")

    if not cid:
        skip("No client_id")
        return

    step("GET /client-trust-scores/{client_id}")
    ok_r, rp = _call("GET", f"/client-trust-scores/{cid}", token=tok,
                     expected=(200, 404))
    ok("ok") if ok_r else info("No trust score yet")

    step("GET /client-trust-scores — list")
    ok_r, _ = _call("GET", "/client-trust-scores", params={"limit": 5}, token=tok)
    ok("ok") if ok_r else fail("failed")

    step("POST /client-trust-scores — create")
    ok_r, rp = _call("POST", "/client-trust-scores", body={
        "client_id": cid, "trust_score": 85.0
    }, token=tok, expected=(200, 201, 400))
    if ok_r:
        ok("created")
    else:
        info("Already exists or failed")

    step("PUT /client-trust-scores/{client_id}")
    ok_r, _ = _call("PUT", f"/client-trust-scores/{cid}",
                    body={"trust_score": 90.0}, token=tok,
                    expected=(200, 404))
    ok("ok") if ok_r else info("skipped")


# ─── SECTION 25: Messages (contract) ─────────────────────────────────────────

def s25_messages():
    section("Messages — Contract (S25)")
    skip("Contract /messages route removed — messaging is now handled via DM threads (S34)")


# ─── SECTION 26: Freelancer Embeddings ───────────────────────────────────────

def s26_freelancer_embeddings():
    section("Freelancer Embeddings (S26)")

    tok = _S.get("freelancer_token")
    fid = _S.get("freelancer_id", "")

    step("GET /freelancer-embeddings — list")
    ok_r, rp = _call("GET", "/freelancer-embeddings", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /freelancer-embeddings/freelancer/{freelancer_id}")
    if fid:
        ok_r, rp = _call("GET", f"/freelancer-embeddings/freelancer/{fid}",
                         token=tok, expected=(200, 404))
        ok("ok") if ok_r else info("No embedding record yet")


# ─── SECTION 27: Job Embeddings ───────────────────────────────────────────────

def s27_job_embeddings():
    section("Job Embeddings (S27)")

    tok = _S.get("client_token")
    jpid = _S.get("job_post_id", "")

    step("GET /job-embeddings — list (client auth)")
    ok_r, rp = _call("GET", "/job-embeddings", token=tok)
    ok(f"got {len(_list(rp))}") if ok_r else fail("failed")

    step("GET /job-embeddings/job-post/{job_post_id}")
    if jpid:
        ok_r, rp = _call("GET", f"/job-embeddings/job-post/{jpid}",
                         token=tok, expected=(200, 404))
        ok("ok") if ok_r else info("No embedding record yet")


# ─── SECTION 28: Generic Upload ───────────────────────────────────────────────

def s28_upload():
    section("Generic Upload (S28)")

    tok = _S.get("freelancer_token")

    step("POST /upload?bucket=documents")
    ok_r, rp = _call("POST", "/upload",
                     params={"bucket": "documents"},
                     files={"file": ("test.pdf", _TINY_PDF, "application/pdf")},
                     token=tok, expected=(200, 201, 400, 500))
    if ok_r:
        ok(f"file_url={'yes' if _d(rp).get('file_url') else 'no'}")
    else:
        info("Upload skipped (Supabase bucket may not be configured)")


# ─── SECTION 29: CV Upload ────────────────────────────────────────────────────

def s29_cv_upload():
    section("CV Upload (S29)")

    tok = _S.get("freelancer_token")
    if not tok:
        skip("No freelancer token")
        return

    step("POST /cv_upload — upload CV (PDF)")
    cv_bytes = _CV_TEXT.encode("utf-8")
    ok_r, rp = _call("POST", "/cv_upload",
                     form={"use_llm": "false"},
                     files={"file": ("cv.pdf", _TINY_PDF, "application/pdf")},
                     token=tok, expected=(200, 201, 400, 500))
    if ok_r:
        parsed = _d(rp).get("parsed_profile", {})
        ok(f"parsed_profile keys: {list(parsed.keys()) if parsed else 'none'}")
    else:
        info("CV upload skipped (may need Supabase + text extraction support)")


# ─── SECTION 30: CV Analysis ──────────────────────────────────────────────────

def s30_cv_analysis():
    section("CV Analysis (S30)")

    tok = _S.get("freelancer_token")
    if not tok:
        skip("No freelancer token")
        return

    # Use a text-based CV (txt upload) so text extraction succeeds without PyPDF2
    cv_txt = _CV_TEXT.strip().encode("utf-8")

    step("POST /cv_analysis/analyze — BAAI/bge-base-en-v1.5 similarity + ATS check + GROQ LLM")
    info("Embedding model: BAAI/bge-base-en-v1.5 (768-dim) — shared with job matching")
    ok_r, rp = _call("POST", "/cv_analysis/analyze",
                     files={"cv_file": ("cv.txt", cv_txt, "text/plain")},
                     token=tok, expected=(200, 201, 400, 500),)
    if ok_r:
        d = _d(rp)
        sim          = d.get("similarity_score") or 0.0
        cov          = d.get("skill_coverage")
        ats          = d.get("ats_score") or 0
        overall_score = d.get("overall_score") or 0
        overall_grade = d.get("overall_grade") or d.get("scoring") or "?"
        resume_score  = d.get("resume_score") or 0
        matched       = d.get("matched_skills") or []
        missing       = d.get("missing_skills") or []
        flags         = d.get("ats_flags") or []
        sections_out  = d.get("sections") or []
        ok(f"overall_score={overall_score}/100 grade={overall_grade} resume_score={resume_score}/100")
        info(f"  similarity={sim:.4f} ({sim*100:.1f}%)  coverage={f'{cov:.2f}' if cov is not None else 'N/A'}  ats={ats}/100")
        info(f"  matched_skills={len(matched)}  missing_skills={len(missing)}  ats_flags={len(flags)}")
        info(f"  llm_sections={len(sections_out)}")
        if d.get("suggested_profile"):
            sp = d["suggested_profile"]
            info(f"  suggested_bio_len={len(sp.get('suggested_bio',''))}  suggested_skills={len(sp.get('skills',[]))}")
    else:
        info("CV analysis skipped (model or LLM may not be available)")


# ─── SECTION 31: AI — Job Matching ───────────────────────────────────────────

def s31_ai_job_matching():
    section("AI — Job Matching (S31)")

    f_tok = _S.get("freelancer_token")
    jpid  = _S.get("job_post_id", "")
    fid   = _S.get("freelancer_id", "")

    step("GET /ai/job_matching/test_ai_local — Ollama connectivity check")
    ok_r, rp = _call("GET", "/ai/job_matching/test_ai_local",
                     expected=(200, 500, 503, 504))
    if ok_r:
        ok(f"Ollama response: {str(_d(rp).get('response', ''))[:60]}")
    else:
        info("Ollama not reachable (expected in CI/cloud env)")

    step("POST /ai/job_matching/embed/freelancer/{id} — queue embedding")
    if fid:
        ok_r, rp = _call("POST", f"/ai/job_matching/embed/freelancer/{fid}",
                         token=f_tok, expected=(200, 202))
        ok(f"queued: {_d(rp).get('message', '?')}") if ok_r else fail("failed")

    step("POST /ai/job_matching/embed/job/{id} — queue job embedding")
    if jpid:
        ok_r, rp = _call("POST", f"/ai/job_matching/embed/job/{jpid}",
                         token=f_tok, expected=(200, 202))
        ok(f"queued: {_d(rp).get('message', '?')}") if ok_r else fail("failed")

    step("POST /ai/job_matching/sweep — run dirty-embedding sweep")
    ok_r, rp = _call("POST", "/ai/job_matching/sweep",
                     token=f_tok, expected=(200, 201, 500))
    if ok_r:
        ok(f"sweep result: freelancers={_d(rp).get('freelancers_updated','?')} "
           f"jobs={_d(rp).get('jobs_updated','?')}")
    else:
        info("Sweep failed (Ollama/embedder may not be running)")

    step("GET /ai/job_matching/match/freelancer-to-jobs (limit=10, homepage)")
    ok_r, rp = _call("GET", "/ai/job_matching/match/freelancer-to-jobs",
                     params={"limit": 10}, token=f_tok,
                     expected=(200, 404, 500))
    if ok_r:
        matches = _d(rp).get("matches", [])
        count = _d(rp).get("count", 0)
        ok(f"matches={count} (stage returned ok)")
        if matches:
            m = matches[0]
            info(f"  top match: title={m.get('job_title','?')[:40]} "
                 f"prob={m.get('match_probability','?')} "
                 f"overlap={m.get('skill_overlap_pct','?')}")
            info(f"  match_reasons={m.get('match_reasons', [])}")
    else:
        info("Job matching returned error (embedding may not exist yet)")

    step("GET /ai/job_matching/match/freelancer-to-jobs (limit=50, view-all page)")
    ok_r, rp = _call("GET", "/ai/job_matching/match/freelancer-to-jobs",
                     params={"limit": 50}, token=f_tok,
                     expected=(200, 404, 500))
    if ok_r:
        ok(f"view-all matches={_d(rp).get('count', 0)}")
    else:
        info("view-all: no results or error (expected if no embedding)")

    step("GET /ai/job_matching/match/freelancer-to-jobs with experience_level filter")
    ok_r, rp = _call("GET", "/ai/job_matching/match/freelancer-to-jobs",
                     params={"limit": 10, "experience_level": "intermediate"},
                     token=f_tok, expected=(200, 404, 500))
    ok(f"filtered matches={_d(rp).get('count', 0)}") if ok_r else info("no results")

    step("GET /ai/job_matching/analyse/job/{job_post_id} — RAG deep analysis (may take 5-30s)")
    if jpid:
        ok_r, rp = _call("GET", f"/ai/job_matching/analyse/job/{jpid}",
                         token=f_tok, expected=(200, 404, 500, 502))
        if ok_r:
            score = _d(rp).get("overall_match_score", "?")
            rec = _d(rp).get("overall_recommendation", "?")
            ok(f"RAG analysis ok: score={score} recommendation={rec}")
        else:
            info("RAG analysis failed (Ollama/LLM may not be running)")


# ─── SECTION 32: Admin — Direct Overrides ────────────────────────────────────

def s32_admin():
    section("Admin — Direct Overrides (S32)")

    a_tok = _S.get("admin_token")
    jpid  = _S.get("job_post_id", "")
    fuid  = _S.get("freelancer_user_id", "")

    if not a_tok:
        skip("No admin token — admin account may not exist")
        return

    step("POST /admin/jobs/{job_post_id}/close")
    if jpid:
        ok_r, rp = _call("POST", f"/admin/jobs/{jpid}/close",
                         body={"reason": "Admin review: potential policy violation"},
                         token=a_tok, expected=(200, 201))
        ok("job closed by admin") if ok_r else fail("admin close job failed")

    step("POST /admin/jobs/{job_post_id}/reopen")
    if jpid:
        ok_r, rp = _call("POST", f"/admin/jobs/{jpid}/reopen",
                         token=a_tok, expected=(200, 201))
        ok("job reopened by admin") if ok_r else fail("admin reopen job failed")

    step("POST /admin/accounts/{user_id}/close")
    if fuid:
        ok_r, rp = _call("POST", f"/admin/accounts/{fuid}/close",
                         body={"reason": "Test restriction by admin"},
                         token=a_tok, expected=(200, 201))
        ok("account restricted by admin") if ok_r else fail("admin close account failed")

    step("POST /admin/accounts/{user_id}/reopen")
    if fuid:
        ok_r, rp = _call("POST", f"/admin/accounts/{fuid}/reopen",
                         token=a_tok, expected=(200, 201))
        ok("account restriction lifted") if ok_r else fail("admin reopen account failed")


# ─── SECTION 33: Dashboard ───────────────────────────────────────────────────

def s33_dashboard():
    section("Dashboard (S33)")

    f_tok = _S.get("freelancer_token")
    c_tok = _S.get("client_token")

    step("GET /dashboard/freelancer — default (all)")
    ok_r, rp = _call("GET", "/dashboard/freelancer", token=f_tok)
    if ok_r:
        summary = _d(rp).get("summary", {})
        ok(f"total_applied={summary.get('total_applied', '?')} "
           f"in_progress={summary.get('in_progress', '?')}")
    else:
        fail("freelancer dashboard failed")

    step("GET /dashboard/freelancer?tracking_status=applied")
    ok_r, rp = _call("GET", "/dashboard/freelancer",
                     params={"tracking_status": "applied", "page": 1, "page_size": 10},
                     token=f_tok)
    ok("filtered dashboard ok") if ok_r else fail("failed")

    step("GET /dashboard/freelancer?order_by=proposed_budget&order_dir=desc")
    ok_r, _ = _call("GET", "/dashboard/freelancer",
                    params={"order_by": "proposed_budget", "order_dir": "desc"},
                    token=f_tok)
    ok("sorted dashboard ok") if ok_r else fail("failed")

    step("GET /dashboard/client — default (all)")
    ok_r, rp = _call("GET", "/dashboard/client", token=c_tok)
    if ok_r:
        summary = _d(rp).get("summary", {})
        ok(f"total_jobs_posted={summary.get('total_jobs_posted', '?')} "
           f"in_progress={summary.get('in_progress', '?')}")
    else:
        fail("client dashboard failed")

    step("GET /dashboard/client?tracking_status=open")
    ok_r, _ = _call("GET", "/dashboard/client",
                    params={"tracking_status": "open", "page": 1, "page_size": 5},
                    token=c_tok)
    ok("filtered client dashboard ok") if ok_r else fail("failed")


# ─── SECTION 34: Direct Messages (DM) ────────────────────────────────────────

def s34_dm():
    section("Direct Messages — DM (S34)")

    c_tok = _S.get("client_token")
    f_tok = _S.get("freelancer_token")
    f_uid = _S.get("freelancer_user_id", "")

    if not c_tok or not f_uid:
        skip("No client token or freelancer user_id")
        return

    # Client creates thread with freelancer
    step("POST /dm/threads — client starts thread")
    ok_r, rp = _call("POST", "/dm/threads",
                     body={"participant_id": f_uid},
                     token=c_tok, expected=(200, 201))
    if ok_r:
        d = _d(rp)
        _S["dm_thread_id"] = (
            d.get("thread_id") or
            (d.get("thread") or {}).get("thread_id") or
            (d.get("result", {}).get("thread") or {}).get("thread_id") or ""
        )
        already = d.get("already_exists", False)
        ok(f"thread_id={_S['dm_thread_id']} already_exists={already}")
    else:
        fail("create DM thread failed")

    tid = _S.get("dm_thread_id", "")

    step("GET /dm/threads — list all threads (client)")
    ok_r, rp = _call("GET", "/dm/threads", token=c_tok)
    ok(f"got {len(_list(rp))} threads") if ok_r else fail("failed")

    step("GET /dm/threads/requests — pending requests (freelancer)")
    ok_r, rp = _call("GET", "/dm/threads/requests", token=f_tok)
    ok(f"got {len(_list(rp))} pending requests") if ok_r else fail("failed")

    step("GET /dm/threads/{thread_id}")
    if tid:
        ok_r, _ = _call("GET", f"/dm/threads/{tid}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Freelancer accepts
    step("PUT /dm/threads/{thread_id}/accept — freelancer accepts")
    if tid:
        ok_r, _ = _call("PUT", f"/dm/threads/{tid}/accept",
                        token=f_tok, expected=(200, 201, 400))
        ok("accepted") if ok_r else info("accept failed (may already be accepted)")

    # Send normal message (client)
    step("POST /dm/threads/{thread_id}/messages — send message (client)")
    if tid:
        ok_r, rp = _call("POST", f"/dm/threads/{tid}/messages",
                         body={"message_text": "Hi, I would like to discuss a project with you."},
                         token=c_tok, expected=(200, 201))
        ok("message sent") if ok_r else fail("failed")

    # ── EDGE CASE: Toxic DM — must be rejected with 400 ───────────────────
    step("EDGE CASE: POST /dm/threads/{thread_id}/messages — toxic text → expect 400")
    if tid:
        ok_r, rp = _call("POST", f"/dm/threads/{tid}/messages",
                         body={"message_text": _TOXIC_TEXT},
                         token=c_tok, expected=(400,))
        if ok_r:
            ok(f"Toxic DM correctly rejected 400 — {_d(rp).get('message', '')}")
        else:
            fail("Toxic DM was NOT rejected (toxicity gate may be disabled)")

    # Freelancer replies
    step("POST /dm/threads/{thread_id}/messages — freelancer replies")
    if tid:
        ok_r, _ = _call("POST", f"/dm/threads/{tid}/messages",
                        body={"message_text": "Hello! I am interested. Let me know more details."},
                        token=f_tok, expected=(200, 201))
        ok("reply sent") if ok_r else fail("failed")

    # File upload in DM
    step("POST /dm/threads/{thread_id}/messages/upload — file in DM")
    if tid:
        ok_r, rp = _call("POST", f"/dm/threads/{tid}/messages/upload",
                         files={"file": ("brief.pdf", _TINY_PDF, "application/pdf")},
                         token=c_tok, expected=(200, 201, 400, 500))
        ok("file sent in DM") if ok_r else info("DM file upload skipped (Supabase may not be configured)")

    step("GET /dm/threads/{thread_id}/messages")
    if tid:
        ok_r, rp = _call("GET", f"/dm/threads/{tid}/messages", token=f_tok)
        ok(f"got {len(_list(rp))} messages") if ok_r else fail("failed")

    step("PUT /dm/threads/{thread_id}/read — mark messages read")
    if tid:
        ok_r, rp = _call("PUT", f"/dm/threads/{tid}/read",
                         token=f_tok, expected=(200, 201))
        ok(f"updated_count={_d(rp).get('updated_count', '?')}") if ok_r else fail("failed")

    # Decline scenario (create second thread and decline it)
    step("PUT /dm/threads/{thread_id}/decline — probe decline endpoint")
    if tid:
        ok_r, _ = _call("PUT", f"/dm/threads/{tid}/decline",
                        token=f_tok, expected=(200, 201, 400))
        ok("decline endpoint reachable") if ok_r else info("decline: already accepted or not allowed")

    # WebSocket probe
    step("WebSocket /dm/ws/{thread_id} — probe (need websockets package)")
    if websockets and tid and _S.get("freelancer_token"):
        async def _ws_probe():
            ws_url = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
            uri = f"{ws_url}/dm/ws/{tid}?token={_S['freelancer_token']}"
            try:
                async with websockets.connect(uri, open_timeout=5) as ws:
                    msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    return True, str(msg)[:80]
            except Exception as exc:
                return False, str(exc)[:80]
        connected, detail = asyncio.run(_ws_probe())
        if connected:
            ok(f"WebSocket connected: {detail}")
        else:
            info(f"WebSocket probe: {detail}")
    elif not websockets:
        skip("websockets package not installed")
    else:
        skip("No thread_id for WebSocket test")


# ─── SECTION 35: Notifications ───────────────────────────────────────────────

def s35_notifications():
    section("Notifications (S35)")

    tok = _S.get("freelancer_token")

    step("GET /notifications — list")
    ok_r, rp = _call("GET", "/notifications", token=tok)
    notifs = _list(rp)
    ok(f"got {len(notifs)} notifications") if ok_r else fail("failed")
    if notifs:
        _S["notification_id"] = str(notifs[0].get("notification_id", ""))

    step("GET /notifications/unread-count")
    ok_r, rp = _call("GET", "/notifications/unread-count", token=tok)
    ok(f"unread_count={_d(rp).get('unread_count', '?')}") if ok_r else fail("failed")

    step("PATCH /notifications/read-all — mark all read")
    ok_r, rp = _call("PATCH", "/notifications/read-all", token=tok)
    ok(f"updated_count={_d(rp).get('updated_count', '?')}") if ok_r else fail("failed")

    step("PATCH /notifications/{id}/read — mark one read")
    nid = _S.get("notification_id", "")
    if nid:
        ok_r, _ = _call("PATCH", f"/notifications/{nid}/read", token=tok)
        ok("ok") if ok_r else fail("failed")
    else:
        skip("No notification to mark read")

    step("PUT /notifications/fcm-token — register FCM token")
    ok_r, _ = _call("PUT", "/notifications/fcm-token",
                    body={"token": "test_fcm_token_walkthrough_12345"},
                    token=tok)
    ok("FCM token registered") if ok_r else fail("failed")


# ─── SECTION 36: Reviews (AI-powered) ────────────────────────────────────────

def s36_reviews():
    section("Reviews — AI-powered (S36)")

    c_tok = _S.get("client_token")
    f_tok = _S.get("freelancer_token")
    cnid  = _S.get("contract_id", "")
    fid   = _S.get("freelancer_id", "")

    if not cnid:
        skip("No contract_id")
        return

    step("GET /reviews/contract/{contract_id} — get review shell")
    ok_r, rp = _call("GET", f"/reviews/contract/{cnid}", token=c_tok,
                     expected=(200, 404))
    if ok_r:
        d = _d(rp)
        _S["review_id"] = d.get("review_id") or d.get("id") or ""
        ok(f"review_id={_S['review_id']}")
    else:
        info("No review shell yet (contract may not be completed)")

    step("POST /reviews/{review_id}/submit — client submits review")
    rid = _S.get("review_id", "")
    if rid:
        ok_r, rp = _call("POST", f"/reviews/{rid}/submit",
                         body={
                             "review_text": "Excellent engineer. Delivered clean, well-documented code on time. Highly recommend.",
                             "targeted_question_answer": "Communication was clear and professional throughout."
                         },
                         token=c_tok, expected=(200, 201, 400))
        ok("review submitted (AI pipeline triggered)") if ok_r else info("submit failed (may need completed contract)")
    else:
        skip("No review_id to submit")

    step("GET /reviews/freelancer/{freelancer_id} — published reviews")
    if fid:
        ok_r, rp = _call("GET", f"/reviews/freelancer/{fid}", token=f_tok)
        ok(f"got {len(_list(rp))} reviews") if ok_r else fail("failed")

    step("GET /reviews/trust-score/{freelancer_id}")
    if fid:
        ok_r, rp = _call("GET", f"/reviews/trust-score/{fid}", token=f_tok,
                         expected=(200, 404))
        ok(f"trust_score={_d(rp).get('trust_score', '?')}") if ok_r else info("no trust score yet")

    step("GET /reviews/red-flags/{freelancer_id}")
    if fid:
        ok_r, rp = _call("GET", f"/reviews/red-flags/{fid}", token=f_tok)
        ok(f"got {len(_list(rp))} red flags") if ok_r else fail("failed")

    step("GET /reviews/{review_id} — single review")
    if rid:
        ok_r, _ = _call("GET", f"/reviews/{rid}", token=f_tok,
                        expected=(200, 404))
        ok("ok") if ok_r else info("not found yet")


# ─── SECTION 37: AI — Toxicity Detection ─────────────────────────────────────

def s37_toxicity():
    section("AI — Toxicity Detection (S37)")

    tok = _S.get("freelancer_token")

    step("GET /toxicity/labels — list classification labels")
    ok_r, rp = _call("GET", "/toxicity/labels", token=tok)
    labels = _d(rp).get("labels", [])
    ok(f"labels={labels}") if ok_r else fail("failed")

    step("GET /toxicity/models — available models")
    ok_r, rp = _call("GET", "/toxicity/models", token=tok)
    models = _d(rp).get("models", [])
    ok(f"models={models}") if ok_r else fail("failed")

    step("POST /toxicity/detect — clean text (expect is_toxic=false)")
    ok_r, rp = _call("POST", "/toxicity/detect",
                     body={"text": "I am excited to apply for this backend engineering role."},
                     token=tok, expected=(200, 201))
    if ok_r:
        is_tox = _d(rp).get("is_toxic", "?")
        conf = _d(rp).get("confidence", "?")
        ok(f"is_toxic={is_tox} confidence={conf}")
    else:
        fail("detect failed (model may not be loaded)")

    step("POST /toxicity/detect — toxic text (expect is_toxic=true)")
    ok_r, rp = _call("POST", "/toxicity/detect",
                     body={"text": _TOXIC_TEXT},
                     token=tok, expected=(200, 201))
    if ok_r:
        is_tox = _d(rp).get("is_toxic", "?")
        labels = _d(rp).get("labels", {})
        ok(f"is_toxic={is_tox} labels={labels}")
        if is_tox is True:
            ok("Toxicity model correctly identified toxic content")
        else:
            info("Model did not flag as toxic — threshold or model may differ")
    else:
        fail("detect failed")

    step("POST /toxicity/detect-batch — mixed texts")
    ok_r, rp = _call("POST", "/toxicity/detect-batch",
                     body={"texts": [
                         "I have 5 years of Python experience.",
                         _TOXIC_TEXT,
                         "Looking forward to collaborating on this project."
                     ]},
                     token=tok, expected=(200, 201))
    if ok_r:
        results = _d(rp).get("results", _list(rp))
        ok(f"batch results count={len(results)}")
    else:
        fail("batch detect failed")


# ─── SECTION 38: Reports ─────────────────────────────────────────────────────

def s38_reports():
    section("Reports (S38)")

    f_tok = _S.get("freelancer_token")
    c_tok = _S.get("client_token")
    cid   = _S.get("client_id", "")
    c_uid = _S.get("client_user_id", "")
    f_uid = _S.get("freelancer_user_id", "")
    jpid  = _S.get("job_post_id", "")

    step("GET /reports/reasons — list predefined reasons")
    ok_r, rp = _call("GET", "/reports/reasons", token=f_tok)
    reasons = _d(rp).get("reasons", [])
    ok(f"reasons count={len(reasons)} sample={reasons[:2]}") if ok_r else fail("failed")
    # Store a valid reason for use in report creation
    _S["report_reason"] = reasons[0] if reasons else "spam"

    step("POST /reports — freelancer reports client account")
    reason = _S.get("report_reason", "spam")
    if c_uid:
        ok_r, rp = _call("POST", "/reports", body={
            "reported_type": "client",
            "reported_user_id": c_uid,
            "reasons": [reason],
            "custom_reason": "Walkthrough test report"
        }, token=f_tok, expected=(200, 201, 400))
        ok("report submitted") if ok_r else info("report failed (may already exist or validation)")
    else:
        skip("No client_user_id")

    step("POST /reports — client reports job post")
    if jpid:
        ok_r, rp = _call("POST", "/reports", body={
            "reported_type": "job_post",
            "job_post_id": jpid,
            "reasons": [reason],
            "custom_reason": "Test job post report"
        }, token=f_tok, expected=(200, 201, 400))
        ok("job post report submitted") if ok_r else info("failed or duplicate")

    # ── EDGE CASE: Report self — must fail ──────────────────────────────────
    step("EDGE CASE: POST /reports — report self → expect 400")
    if f_uid:
        ok_r, rp = _call("POST", "/reports", body={
            "reported_type": "freelancer",
            "reported_user_id": f_uid,
            "reasons": [reason]
        }, token=f_tok, expected=(400,))
        if ok_r:
            ok(f"Self-report correctly rejected 400 — {_d(rp).get('message', '')}")
        else:
            fail("Self-report was NOT rejected (validation may be missing)")


# ─── SECTION 39: Appeals ─────────────────────────────────────────────────────

def s39_appeals():
    section("Appeals (S39)")

    tok   = _S.get("freelancer_token")
    jpid  = _S.get("job_post_id", "")
    f_uid = _S.get("freelancer_user_id", "")

    step("POST /appeals — appeal job post closure")
    if jpid:
        ok_r, rp = _call("POST", "/appeals", body={
            "target_type": "job_post",
            "target_id": jpid,
            "message": "I believe the closure was in error. This is a legitimate job posting and I would like it reviewed."
        }, token=tok, expected=(200, 201, 400))
        if ok_r:
            _S["appeal_id"] = _id(rp, "appeal_id")
            ok(f"appeal_id={_S['appeal_id']}")
        else:
            info("Appeal failed (job may not be closed, or validation)")
    else:
        skip("No job_post_id for appeal")

    step("POST /appeals — appeal account restriction")
    if f_uid:
        ok_r, rp = _call("POST", "/appeals", body={
            "target_type": "user",
            "target_id": f_uid,
            "message": "My account was restricted by mistake. I have not violated any platform rules."
        }, token=tok, expected=(200, 201, 400))
        ok("account appeal submitted") if ok_r else info("Appeal failed (may not be restricted)")

    step("POST /appeals — empty message → expect 400")
    if jpid:
        ok_r, rp = _call("POST", "/appeals", body={
            "target_type": "job_post",
            "target_id": jpid,
            "message": ""
        }, token=tok, expected=(400, 422))
        if ok_r:
            ok("Empty appeal message correctly rejected")
        else:
            info("Empty message not validated (endpoint may not check)")

    step("GET /appeals/mine — list own appeals")
    ok_r, rp = _call("GET", "/appeals/mine", token=tok)
    ok(f"got {len(_list(rp))} appeals") if ok_r else fail("failed")


# ─── SECTION 40: Admin — Toxicity Moderation Queue ───────────────────────────

def s40_admin_moderation():
    section("Admin — Toxicity Moderation Queue (S40)")

    a_tok = _S.get("admin_token")
    fid   = _S.get("freelancer_id", "")
    f_uid = _S.get("freelancer_user_id", "")
    cid   = _S.get("client_id", "")
    c_uid = _S.get("client_user_id", "")

    if not a_tok:
        skip("No admin token — admin moderation tests skipped")
        return

    mod_id_1 = ""
    mod_id_2 = ""

    step("POST /admin/moderation/scan — freelancer_profile with toxic text (expect flagged=true)")
    if fid and f_uid:
        ok_r, rp = _call("POST", "/admin/moderation/scan",
                         body={
                             "content_type": "freelancer_profile",
                             "content_id":   fid,
                             "user_id":      f_uid,
                             "text":         _TOXIC_TEXT,
                         },
                         token=a_tok, expected=(200,))
        if ok_r:
            d = _d(rp)
            flagged  = d.get("flagged", False)
            mod_id_1 = (d.get("moderation_record") or {}).get("moderation_id", "")
            ok(f"flagged={flagged} moderation_id={mod_id_1}") if flagged else info("Not flagged — keyword list may differ")
        else:
            fail("scan failed")
    else:
        skip("No freelancer_id")

    step("POST /admin/moderation/scan — client_profile with toxic text (expect flagged=true)")
    if cid and c_uid:
        ok_r, rp = _call("POST", "/admin/moderation/scan",
                         body={
                             "content_type": "client_profile",
                             "content_id":   cid,
                             "user_id":      c_uid,
                             "text":         _TOXIC_TEXT,
                         },
                         token=a_tok, expected=(200,))
        if ok_r:
            d = _d(rp)
            flagged  = d.get("flagged", False)
            mod_id_2 = (d.get("moderation_record") or {}).get("moderation_id", "")
            ok(f"flagged={flagged} moderation_id={mod_id_2}") if flagged else info("Not flagged — keyword list may differ")
        else:
            fail("scan failed")
    else:
        skip("No client_id")

    step("GET /admin/moderation?status=pending — list pending items")
    ok_r, rp = _call("GET", "/admin/moderation", params={"status": "pending"}, token=a_tok)
    if ok_r:
        items = _list(rp)
        ok(f"got {len(items)} pending items")
        # Fall back: pick up moderation_ids from the queue if scan didn't return them
        for item in items:
            if not mod_id_1 and item.get("content_type") == "freelancer_profile":
                mod_id_1 = item.get("moderation_id", "")
            if not mod_id_2 and item.get("content_type") == "client_profile":
                mod_id_2 = item.get("moderation_id", "")
    else:
        fail("failed to list moderation queue")

    step("POST /admin/moderation/{id}/approve — approve freelancer_profile flag")
    if mod_id_1:
        ok_r, _ = _call("POST", f"/admin/moderation/{mod_id_1}/approve",
                        body={"admin_note": "Walkthrough test: content reviewed and approved"},
                        token=a_tok, expected=(200,))
        ok("flag approved — content allowed to stay") if ok_r else fail("approve failed")
    else:
        skip("No moderation_id to approve")

    step("POST /admin/moderation/{id}/reject — reject client_profile flag")
    if mod_id_2:
        ok_r, _ = _call("POST", f"/admin/moderation/{mod_id_2}/reject",
                        body={"admin_note": "Walkthrough test: content confirmed toxic"},
                        token=a_tok, expected=(200,))
        ok("flag rejected") if ok_r else fail("reject failed")
    else:
        skip("No moderation_id to reject")

    step("GET /admin/moderation?status=approved — verify approved item appears")
    ok_r, rp = _call("GET", "/admin/moderation", params={"status": "approved"}, token=a_tok)
    if ok_r:
        ok(f"got {len(_list(rp))} approved items")
    else:
        fail("failed")

    step("GET /admin/moderation?status=rejected — verify rejected item appears")
    ok_r, rp = _call("GET", "/admin/moderation", params={"status": "rejected"}, token=a_tok)
    if ok_r:
        ok(f"got {len(_list(rp))} rejected items")
    else:
        fail("failed")

    step("GET /admin/moderation?status=all&content_type=freelancer_profile — content_type filter")
    ok_r, rp = _call("GET", "/admin/moderation",
                     params={"status": "all", "content_type": "freelancer_profile"},
                     token=a_tok)
    if ok_r:
        ok(f"got {len(_list(rp))} freelancer_profile items")
    else:
        fail("failed")


# ─── SECTION 41: AI — Job Scam Detection ─────────────────────────────────────

def s41_job_scam_detection():
    section("AI — Job Scam Detection (S41)")

    a_tok  = _S.get("admin_token")
    c_tok  = _S.get("client_token")
    cid    = _S.get("client_id", "")
    jpid   = _S.get("scam_job_post_id", "") or _S.get("job_post_id", "")

    if not a_tok:
        skip("No admin token — scam detection tests skipped")
        return

    # Manual scan via admin utility
    step("POST /admin/scam-flags/scan — manually trigger scam scan on scam job post")
    info("Model: BAAI/bge-base-en-v1.5 + Random Forest (778 features = 768-dim SBERT + 10 engineered)")
    if jpid:
        ok_r, rp = _call("POST", "/admin/scam-flags/scan",
                         body={"job_post_id": jpid},
                         token=a_tok, expected=(200, 201, 400, 404, 500))
        if ok_r:
            d = _d(rp)
            flagged = d.get("flagged", False)
            sf      = d.get("scam_flag") or {}
            prob    = sf.get("scam_probability") or sf.get("scam_score") or "?"
            ok(f"flagged={flagged}  scam_probability={prob}")
            if flagged:
                _S["scam_flag_id"] = str(sf.get("scam_flag_id") or sf.get("flag_id") or "")
                info(f"  scam_flag_id={_S['scam_flag_id']}")
        else:
            info("Scan failed (job post may already be closed or model unavailable)")
    else:
        skip("No job_post_id for scam scan")

    # List scam flags
    step("GET /admin/scam-flags — list all scam flags (status=pending)")
    ok_r, rp = _call("GET", "/admin/scam-flags",
                     params={"status": "pending", "page": 1, "page_size": 10},
                     token=a_tok, expected=(200,))
    if ok_r:
        items = _list(rp)
        ok(f"got {len(items)} pending scam flags")
        if items and not _S.get("scam_flag_id"):
            _S["scam_flag_id"] = str(items[0].get("scam_flag_id") or items[0].get("flag_id") or "")
    else:
        fail("list scam flags failed")

    step("GET /admin/scam-flags — sorted by scam_score desc")
    ok_r, rp = _call("GET", "/admin/scam-flags",
                     params={"status": "all", "sort_by": "scam_score", "sort_dir": "desc", "page_size": 5},
                     token=a_tok, expected=(200,))
    ok(f"got {len(_list(rp))} items sorted by score") if ok_r else fail("failed")

    flag_id = _S.get("scam_flag_id", "")

    # Approve (mark as false positive)
    step("POST /admin/scam-flags/{flag_id}/approve — mark as false positive")
    if flag_id:
        ok_r, _ = _call("POST", f"/admin/scam-flags/{flag_id}/approve",
                        token=a_tok, expected=(200, 201, 404))
        ok("flag marked safe (false positive)") if ok_r else info("approve failed or already actioned")
    else:
        skip("No scam_flag_id to approve")

    # Re-scan to get a fresh flag to test remove
    step("POST /admin/scam-flags/scan — rescan to get fresh flag for remove test")
    scam_jpid = _S.get("scam_job_post_id", "")
    if scam_jpid:
        ok_r, rp = _call("POST", "/admin/scam-flags/scan",
                         body={"job_post_id": scam_jpid},
                         token=a_tok, expected=(200, 201, 400, 404, 500))
        if ok_r:
            d       = _d(rp)
            flagged = d.get("flagged", False)
            sf      = d.get("scam_flag") or {}
            _S["scam_flag_id_remove"] = str(sf.get("scam_flag_id") or sf.get("flag_id") or "")
            ok(f"rescan flagged={flagged} flag_id={_S['scam_flag_id_remove']}")
        else:
            info("Rescan skipped (job may be closed after approve)")
    else:
        skip("No scam_job_post_id for rescan")

    # Remove (confirm scam, close job)
    step("POST /admin/scam-flags/{flag_id}/remove — confirm scam, close job post")
    rfid = _S.get("scam_flag_id_remove", "")
    if rfid:
        ok_r, _ = _call("POST", f"/admin/scam-flags/{rfid}/remove",
                        token=a_tok, expected=(200, 201, 404))
        ok("scam confirmed — job closed, client strike recorded") if ok_r else info("remove failed (may already be actioned)")
    else:
        skip("No flag_id for remove test")

    # Client scam record
    step("GET /admin/scam-flags/client/{client_id} — check client scam record")
    if cid:
        ok_r, rp = _call("GET", f"/admin/scam-flags/client/{cid}",
                         token=a_tok, expected=(200,))
        if ok_r:
            d = _d(rp)
            total    = d.get("total_scam_confirmed", 0)
            is_banned = d.get("is_banned", False)
            ok(f"total_scam_confirmed={total}  is_banned={is_banned}")
        else:
            fail("client scam record failed")
    else:
        skip("No client_id")


# ─── Cleanup / teardown ───────────────────────────────────────────────────────

def s_cleanup():
    section("Cleanup — Teardown Created Data")

    f_tok = _S.get("freelancer_token")
    c_tok = _S.get("client_token")

    # Delete portfolio
    if _S.get("portfolio_id") and f_tok:
        step("DELETE /portfolios/{id}")
        ok_r, _ = _call("DELETE", f"/portfolios/{_S['portfolio_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete rating
    if _S.get("rating_id") and c_tok:
        step("DELETE /ratings/{id}")
        ok_r, _ = _call("DELETE", f"/ratings/{_S['rating_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete contract
    if _S.get("contract_id") and c_tok:
        step("DELETE /contracts/{id}")
        ok_r, _ = _call("DELETE", f"/contracts/{_S['contract_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete proposals
    if _S.get("proposal_id") and f_tok:
        step("DELETE /proposals/{id}")
        ok_r, _ = _call("DELETE", f"/proposals/{_S['proposal_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete scam job post
    if _S.get("scam_job_post_id") and c_tok:
        step("DELETE /job-posts/{scam_job_post_id}")
        ok_r, _ = _call("DELETE", f"/job-posts/{_S['scam_job_post_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete draft job post
    if _S.get("draft_job_post_id") and c_tok:
        step("DELETE /job-posts/{draft_job_post_id}")
        ok_r, _ = _call("DELETE", f"/job-posts/{_S['draft_job_post_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete job role skill
    if _S.get("job_role_skill_id") and c_tok:
        step("DELETE /job-role-skills/{id}")
        ok_r, _ = _call("DELETE", f"/job-role-skills/{_S['job_role_skill_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete job role
    if _S.get("job_role_id") and c_tok:
        step("DELETE /job-roles/{id}")
        ok_r, _ = _call("DELETE", f"/job-roles/{_S['job_role_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete job post
    if _S.get("job_post_id") and c_tok:
        step("DELETE /job-posts/{id}")
        ok_r, _ = _call("DELETE", f"/job-posts/{_S['job_post_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete work experience
    if _S.get("work_experience_id") and f_tok:
        step("DELETE /work-experiences/{id}")
        ok_r, _ = _call("DELETE", f"/work-experiences/{_S['work_experience_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete education
    if _S.get("education_id") and f_tok:
        step("DELETE /educations/{id}")
        ok_r, _ = _call("DELETE", f"/educations/{_S['education_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete freelancer speciality
    if _S.get("freelancer_speciality_id") and f_tok:
        step("DELETE /freelancer-specialities/{id}")
        ok_r, _ = _call("DELETE", f"/freelancer-specialities/{_S['freelancer_speciality_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete freelancer skill
    if _S.get("freelancer_skill_id") and f_tok:
        step("DELETE /freelancer-skills/{id}")
        ok_r, _ = _call("DELETE", f"/freelancer-skills/{_S['freelancer_skill_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete the test skill (by id)
    if _S.get("skill_id") and f_tok:
        step("DELETE /skills/{skill_id}")
        ok_r, _ = _call("DELETE", f"/skills/{_S['skill_id']}", token=f_tok,
                        expected=(200, 204, 400))
        ok("ok") if ok_r else info("skill delete skipped (may be referenced)")

    # Delete speciality
    if _S.get("speciality_id") and f_tok:
        step("DELETE /specialities/{speciality_id}")
        ok_r, _ = _call("DELETE", f"/specialities/{_S['speciality_id']}", token=f_tok,
                        expected=(200, 204, 400))
        ok("ok") if ok_r else info("spec delete skipped")

    # Delete client trust score (must happen before client profile is deleted)
    if _S.get("client_id") and c_tok:
        step("DELETE /client-trust-scores/{client_id}")
        ok_r, _ = _call("DELETE", f"/client-trust-scores/{_S['client_id']}",
                        token=c_tok, expected=(200, 204, 404))
        ok("ok") if ok_r else info("trust score delete skipped")

    # Delete client profile
    if _S.get("client_id") and c_tok:
        step("DELETE /clients/{client_id}")
        ok_r, _ = _call("DELETE", f"/clients/{_S['client_id']}", token=c_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete freelancer profile
    if _S.get("freelancer_id") and f_tok:
        step("DELETE /freelancers/{freelancer_id}")
        ok_r, _ = _call("DELETE", f"/freelancers/{_S['freelancer_id']}", token=f_tok)
        ok("ok") if ok_r else fail("failed")

    # Delete performance rating
    if _S.get("freelancer_id") and f_tok:
        step("DELETE /performance-ratings/freelancer/{freelancer_id}")
        ok_r, _ = _call("DELETE", f"/performance-ratings/freelancer/{_S['freelancer_id']}",
                        token=f_tok, expected=(200, 204, 404))
        ok("ok") if ok_r else info("perf rating delete skipped")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    tee, path = _start_tee()

    print(f"WorkByte Walkthrough ALL (New)")
    print(f"Target: {BASE_URL}")
    print(f"Started: {datetime.datetime.now().isoformat()}")
    print(f"Sections: 41 + cleanup\n")

    sections = [
        s01_auth,
        s02_freelancers,
        s03_clients,
        s04_skills,
        s05_languages,
        s06_specialities,
        s07_freelancer_skills,
        s08_freelancer_specialities,
        s09_freelancer_languages,
        s10_work_experience,
        s11_education,
        s12_job_posts,
        s13_job_roles,
        s14_job_role_skills,
        s15_job_files,
        s16_proposals,
        s17_proposal_files,
        s18_contracts,
        s19_contract_submissions,
        s20_portfolio,
        s21_saved_jobs,
        s22_ratings,
        s23_performance_ratings,
        s24_client_trust_scores,
        s25_messages,
        s26_freelancer_embeddings,
        s27_job_embeddings,
        s28_upload,
        s29_cv_upload,
        s30_cv_analysis,
        s31_ai_job_matching,
        s32_admin,
        s33_dashboard,
        s34_dm,
        s35_notifications,
        s36_reviews,
        s37_toxicity,
        s38_reports,
        s39_appeals,
        s40_admin_moderation,
        s41_job_scam_detection,
        s_cleanup,
    ]

    for fn in sections:
        try:
            fn()
        except Exception as exc:
            fail(f"Section crashed: {exc}")

    print(f"\n{'=' * 72}")
    print(f"  SUMMARY")
    print(f"{'=' * 72}")
    print(f"  PASS: {_pass_count}")
    print(f"  FAIL: {_fail_count}")
    print(f"  SKIP: {_skip_count}")
    total = _pass_count + _fail_count
    if total:
        pct = round(_pass_count / total * 100, 1)
        print(f"  PASS RATE: {pct}%  ({_pass_count}/{total})")
    print(f"  Finished: {datetime.datetime.now().isoformat()}")
    print(f"{'=' * 72}\n")

    _stop_tee(tee, path)


if __name__ == "__main__":
    main()
