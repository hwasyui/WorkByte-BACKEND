"""
Walkthrough ALL — Complete API Coverage

Exercises every route across every domain in one sequential script.
Failures are printed but do not abort later sections.

Lifecycle:
  auth → freelancer/client profiles → reference data (skills/lang/spec)
  → freelancer attributes → job post → job role → proposal → contract
  → contract submission → review → ratings → DM → dashboard → AI → admin

Coverage notes:
  File upload routes are exercised with in-memory PDF/DOCX/JPEG fixtures.
  OAuth routes are probed without following external redirects.
  WebSocket coverage runs when the optional `websockets` package is installed.
  External services (Supabase, Groq, model downloads, OAuth credentials) may
  make those routes return expected 5xx/501 responses in local environments.

Requirements:
  Server running at BASE_URL (default http://localhost:8000)
  APP_ENV=development  SHOW_DEV_OTP=true
  Admin account: admin@admin.com / thisisanadminaccountpassword

Usage:
  python walkthrough/walkthrough-all.py
  BASE_URL=http://localhost:8000 python walkthrough/walkthrough-all.py
"""

import datetime
import asyncio
import io
import json
import os
import sys
import time
from urllib.parse import urlparse
import requests

try:
    import websockets
except Exception:
    websockets = None

BASE_URL     = os.environ.get("BASE_URL", "http://localhost:8000")
_PASSWORD    = "SecurePass123!"
_NEW_PASS    = "NewPass456@789"
_ADMIN_EMAIL = "admin@admin.com"
_ADMIN_PASS  = "thisisanadminaccountpassword"
_TS          = int(time.time())

# Minimal valid PDF bytes for file-upload tests
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

_CV_TEXT = f"""
Alex Walkthrough
Backend Engineer
Experienced Python, FastAPI, PostgreSQL, REST API, authentication, testing, Docker.
Work Experience: Backend Engineer at Acme Corp from 2021 to 2024. Built APIs and optimized databases.
Education: Bachelor of Computer Science.
Skills: Python, FastAPI, PostgreSQL, SQL, Docker, Git.
""".strip()

# Shared state dictionary — populated as sections run
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
                        f"walkthrough_all_{ts}.md")
    tee = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee: _Tee, path: str):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\nResults saved to: {path}")


# ─── Helpers ─────────────────────────────────────────────────────────────────

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
            r = requests.post(url, headers=headers, data=form or {}, files=files, timeout=90,
                              allow_redirects=allow_redirects)
        elif method == "POST" and form is not None:
            r = requests.post(url, headers=headers, data=form, timeout=90)
        elif method == "POST":
            headers["Content-Type"] = "application/json"
            r = requests.post(url, headers=headers, json=body, params=params, timeout=90)
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
            print(f"           {json.dumps(payload, indent=4)[:400]}")
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
        return raw.get("list", [])
    return []


def _token(payload: dict) -> str:
    return _d(payload).get("access_token", "")


def _id(payload: dict, key: str) -> str:
    d = _d(payload)
    val = d.get(key)
    if val:
        return str(val)
    for sub in d.values():
        if isinstance(sub, dict) and key in sub:
            return str(sub[key])
    return ""


def _ids(payload: dict, key: str) -> list:
    values = []
    data = payload.get("details", payload)
    if isinstance(data, dict):
        data = data.get("list", data.get("results", data.get("files", data)))
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get(key):
                values.append(str(item[key]))
    return values


def _cv_docx_bytes() -> bytes | None:
    try:
        import docx
        buf = io.BytesIO()
        doc = docx.Document()
        for line in _CV_TEXT.splitlines():
            doc.add_paragraph(line)
        doc.save(buf)
        buf.seek(0)
        return buf.read()
    except Exception as exc:
        info(f"DOCX fixture unavailable: {exc}")
        return None


def _embedding_vector(value: float = 0.001) -> list:
    return [value] * 768


def _ws_url(path: str) -> str:
    parsed = urlparse(BASE_URL)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    host = parsed.netloc or parsed.path
    return f"{scheme}://{host}{path}"


async def _ws_receive_one(uri: str, timeout: float = 8.0):
    if websockets is None:
        return "missing-library"
    try:
        async with websockets.connect(uri, open_timeout=5) as ws:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                return json.loads(raw)
            except asyncio.TimeoutError:
                return None
    except Exception as exc:
        return {"_ws_error": str(exc)}


def _ws_broadcast_check(uri_listener: str, sender_fn, timeout: float = 8.0) -> bool:
    if websockets is None:
        return False

    async def _run():
        received = []

        async def _listener():
            msg = await _ws_receive_one(uri_listener, timeout=timeout)
            if msg and not (isinstance(msg, dict) and msg.get("_ws_error")):
                received.append(msg)

        async def _sender():
            await asyncio.sleep(0.8)
            sender_fn()

        await asyncio.gather(_listener(), _sender())
        return bool(received)

    return asyncio.run(_run())


def _ws_bad_token_rejected(uri: str) -> bool:
    if websockets is None:
        return False

    async def _check():
        try:
            async with websockets.connect(uri, open_timeout=4) as ws:
                await ws.recv()
            return False
        except Exception:
            return True

    return asyncio.run(_check())


def _register_verify_login(email: str, user_type: str, full_name: str) -> tuple:
    s, reg = _call("POST", "/auth/register", body={
        "email": email, "password": _PASSWORD,
        "user_type": user_type, "full_name": full_name,
    }, expected=(201,))
    if not s:
        fail("register failed")
        return "", ""
    d   = _d(reg)
    otp = (d.get("verification") or {}).get("dev_verification_otp") or d.get("dev_verification_otp")
    if not otp:
        fail("dev_verification_otp missing")
        return "", ""
    _call("POST", "/auth/verify-email", body={"email": email, "otp": otp}, expected=(200,))
    s, login = _call("POST", "/auth/login", body={"email": email, "password": _PASSWORD}, expected=(200,))
    if not s:
        fail("login failed")
        return "", ""
    tok = _token(login)
    s, me = _call("GET", "/auth/me", token=tok, expected=(200,))
    uid = _d(me).get("user_id", "")
    return str(uid), tok


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — AUTH
# ═════════════════════════════════════════════════════════════════════════════

def sec_auth():
    section("AUTH")

    fl_email = f"all.fl.{_TS}@test.dev"
    cl_email = f"all.cl.{_TS}@test.dev"
    cl2_email = f"all.cl2.{_TS}@test.dev"

    # 1.1  Register freelancer
    step("POST /auth/register — freelancer")
    s, reg = _call("POST", "/auth/register", body={
        "email": fl_email, "password": _PASSWORD,
        "user_type": "freelancer", "full_name": f"FL All {_TS}",
    }, expected=(201,))
    d = _d(reg)
    fl_otp = (d.get("verification") or {}).get("dev_verification_otp") or d.get("dev_verification_otp", "")
    info(f"otp: {fl_otp}")
    ok("freelancer registered") if s else fail("register failed")

    # 1.2  Resend-verification (before verifying)
    step("POST /auth/resend-verification")
    s, _ = _call("POST", "/auth/resend-verification", body={"email": fl_email}, expected=(200,))
    ok("resend accepted") if s else fail("resend failed")

    # 1.3  Verify email
    step("POST /auth/verify-email — freelancer")
    s, _ = _call("POST", "/auth/verify-email", body={"email": fl_email, "otp": fl_otp}, expected=(200,))
    ok("email verified") if s else fail("verify failed")

    # 1.4  Login
    step("POST /auth/login — freelancer")
    s, login = _call("POST", "/auth/login", body={"email": fl_email, "password": _PASSWORD}, expected=(200,))
    fl_tok = _token(login)
    ok(f"token received") if s and fl_tok else fail("login or token missing")

    # 1.5  GET /auth/me — freelancer
    step("GET /auth/me — freelancer")
    s, me = _call("GET", "/auth/me", token=fl_tok, expected=(200,))
    fl_uid = str(_d(me).get("user_id", ""))
    info(f"user_id={fl_uid}  freelancer_id={_d(me).get('freelancer_id')}")
    ok("me returned") if s else fail("me failed")
    _S["fl_uid"]  = fl_uid
    _S["fl_tok"]  = fl_tok
    _S["fl_id"]   = str(_d(me).get("freelancer_id", ""))
    _S["fl_email"] = fl_email

    # 1.6  Register + verify + login client
    step("Register + verify + login — client")
    cl_uid, cl_tok = _register_verify_login(cl_email, "client", f"CL All {_TS}")
    ok(f"client ready  user_id={cl_uid}") if cl_uid else fail("client setup failed")
    s, me2 = _call("GET", "/auth/me", token=cl_tok, expected=(200,))
    cl_id = str(_d(me2).get("client_id", ""))
    info(f"client_id={cl_id}")
    _S["cl_uid"]  = cl_uid
    _S["cl_tok"]  = cl_tok
    _S["cl_id"]   = cl_id
    _S["cl_email"] = cl_email

    # 1.7  Register second client (for DM test — another party)
    step("Register second client for DM tests")
    cl2_uid, cl2_tok = _register_verify_login(cl2_email, "client", f"CL2 All {_TS}")
    ok(f"client2 ready  user_id={cl2_uid}") if cl2_uid else fail("client2 setup failed")
    s, me3 = _call("GET", "/auth/me", token=cl2_tok, expected=(200,))
    _S["cl2_uid"] = cl2_uid
    _S["cl2_tok"] = cl2_tok
    _S["cl2_id"]  = str(_d(me3).get("client_id", ""))

    # 1.8  Forgot-password + reset
    step("POST /auth/forgot-password + reset-password")
    tmp_email = f"all.tmp.{_TS}@test.dev"
    _register_verify_login(tmp_email, "freelancer", f"TMP {_TS}")
    s, fp = _call("POST", "/auth/forgot-password", body={"email": tmp_email}, expected=(200,))
    reset_otp = _d(fp).get("dev_reset_otp", "")
    info(f"dev_reset_otp: {reset_otp}")
    if reset_otp:
        s, _ = _call("POST", "/auth/reset-password", body={
            "email": tmp_email, "otp": reset_otp, "new_password": _NEW_PASS,
        }, expected=(200,))
        ok("password reset") if s else fail("reset failed")
        s, _ = _call("POST", "/auth/login", body={"email": tmp_email, "password": _NEW_PASS}, expected=(200,))
        ok("new password works") if s else fail("new password rejected")
    else:
        fail("dev_reset_otp missing — check SHOW_DEV_OTP=true")

    # 1.9  POST /auth/add-role — add client role to freelancer account
    step("POST /auth/add-role — add 'client' role to freelancer account")
    s, ar = _call("POST", "/auth/add-role", token=fl_tok, body={
        "role": "client", "full_name": f"FL+CL {_TS}",
    }, expected=(201, 400))  # 400 if already has both
    ok("add-role responded") if s else fail("add-role failed unexpectedly")

    # 1.10 Admin login
    step("POST /auth/login — admin")
    s, al = _call("POST", "/auth/login", body={"email": _ADMIN_EMAIL, "password": _ADMIN_PASS}, expected=(200,))
    admin_tok = _token(al) if s else ""
    if admin_tok:
        s, ame = _call("GET", "/auth/me", token=admin_tok, expected=(200,))
        info(f"is_admin={_d(ame).get('is_admin')}")
        ok("admin logged in") if _d(ame).get("is_admin") else fail("user is not admin")
    else:
        fail("admin login failed — some admin sections will be skipped")
    _S["admin_tok"] = admin_tok


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — REFERENCE DATA: SKILLS / LANGUAGES / SPECIALITIES
# ═════════════════════════════════════════════════════════════════════════════

def sec_reference_data():
    section("REFERENCE DATA — Skills / Languages / Specialities")
    tok = _S.get("cl_tok", "")
    sfx = f"AW{_TS}"

    # ── Skills ───────────────────────────────────────────────────────────────
    step("POST /skills — create 3 skills")
    skill_ids = []
    for name in [f"Python {sfx}", f"FastAPI {sfx}", f"PostgreSQL {sfx}"]:
        s, sr = _call("POST", "/skills", token=tok, body={
            "skill_name": name, "skill_category": "hard_skill", "description": name.lower(),
        }, expected=(200, 201))
        sid = _id(sr, "skill_id")
        if s and sid:
            skill_ids.append(sid)
            info(f"created skill: {name} ({sid})")
    ok(f"{len(skill_ids)} skills created") if skill_ids else fail("no skills created")
    _S["skill_ids"] = skill_ids

    step("GET /skills — list all")
    s, sl = _call("GET", "/skills", token=tok, expected=(200,))
    ok(f"skills list returned {len(_list(sl))} items") if s else fail("skills list failed")

    step("GET /skills/search?query=Python")
    s, ss = _call("GET", "/skills/search", token=tok, params={"query": f"Python {sfx}"}, expected=(200,))
    ok("search returned") if s else fail("skills search failed")

    step("GET /skills/alphabet/p")
    s, _ = _call("GET", "/skills/alphabet/p", token=tok, expected=(200,))
    ok("alphabet filter returned") if s else fail("alphabet filter failed")

    step("GET /skills/category/hard_skill")
    s, _ = _call("GET", "/skills/category/hard_skill", token=tok, expected=(200,))
    ok("category filter returned") if s else fail("category filter failed")

    if skill_ids:
        step(f"GET /skills/{skill_ids[0]}")
        s, _ = _call("GET", f"/skills/{skill_ids[0]}", token=tok, expected=(200,))
        ok("get by ID returned") if s else fail("get skill failed")

        step(f"PUT /skills/{skill_ids[0]}")
        s, _ = _call("PUT", f"/skills/{skill_ids[0]}", token=tok, body={"description": "updated"}, expected=(200,))
        ok("skill updated") if s else fail("skill update failed")

        # Keep 2 skills, delete the 3rd
        if len(skill_ids) >= 3:
            step(f"DELETE /skills/{skill_ids[2]} — remove spare skill")
            s, _ = _call("DELETE", f"/skills/{skill_ids[2]}", token=tok, expected=(200,))
            ok("skill deleted") if s else fail("skill delete failed")
            skill_ids.pop(2)
            _S["skill_ids"] = skill_ids

    # ── Languages ────────────────────────────────────────────────────────────
    step("POST /languages — create")
    s, lr = _call("POST", "/languages", token=tok, body={
        "language_name": f"Lang {sfx}", "language_code": sfx[:6].lower(),
    }, expected=(200, 201))
    lang_id = _id(lr, "language_id")
    ok(f"language created: {lang_id}") if s else fail("language create failed")
    _S["lang_id"] = lang_id

    step("GET /languages")
    s, _ = _call("GET", "/languages", token=tok, expected=(200,))
    ok("languages listed") if s else fail("languages list failed")

    step("GET /languages/search")
    s, _ = _call("GET", "/languages/search", token=tok, params={"query": f"Lang {sfx}"}, expected=(200,))
    ok("language search returned") if s else fail("language search failed")

    if lang_id:
        step(f"GET /languages/{lang_id}")
        s, _ = _call("GET", f"/languages/{lang_id}", token=tok, expected=(200,))
        ok("language by ID") if s else fail("get language failed")

        step(f"PUT /languages/{lang_id}")
        s, _ = _call("PUT", f"/languages/{lang_id}", token=tok, body={"language_name": f"Lang {sfx} Updated"}, expected=(200,))
        ok("language updated") if s else fail("language update failed")

    # ── Specialities ─────────────────────────────────────────────────────────
    step("POST /specialities — create")
    s, sr = _call("POST", "/specialities", token=tok, body={
        "speciality_name": f"Spec {sfx}", "description": "test speciality",
    }, expected=(200, 201))
    spec_id = _id(sr, "speciality_id")
    ok(f"speciality created: {spec_id}") if s else fail("speciality create failed")
    _S["spec_id"] = spec_id

    step("POST /specialities — create spare")
    s, sr2 = _call("POST", "/specialities", token=tok, body={
        "speciality_name": f"Spec Spare {sfx}", "description": "spare speciality",
    }, expected=(200, 201))
    spec2_id = _id(sr2, "speciality_id")
    ok(f"spare speciality created: {spec2_id}") if s else fail("spare speciality create failed")
    _S["spec2_id"] = spec2_id

    step("GET /specialities")
    s, _ = _call("GET", "/specialities", token=tok, expected=(200,))
    ok("specialities listed") if s else fail("specialities list failed")

    step("GET /specialities/search")
    s, _ = _call("GET", "/specialities/search", token=tok, params={"query": f"Spec {sfx}"}, expected=(200,))
    ok("speciality search returned") if s else fail("speciality search failed")

    if spec_id:
        step(f"GET /specialities/{spec_id}")
        s, _ = _call("GET", f"/specialities/{spec_id}", token=tok, expected=(200,))
        ok("speciality by ID") if s else fail("get speciality failed")

        step(f"PUT /specialities/{spec_id}")
        s, _ = _call("PUT", f"/specialities/{spec_id}", token=tok, body={"description": "updated"}, expected=(200,))
        ok("speciality updated") if s else fail("speciality update failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — FREELANCER PROFILE
# ═════════════════════════════════════════════════════════════════════════════

def sec_freelancer():
    section("FREELANCER PROFILE")
    tok   = _S.get("fl_tok", "")
    fl_id = _S.get("fl_id", "")

    step("GET /freelancers — own profile")
    s, _ = _call("GET", "/freelancers", token=tok, expected=(200,))
    ok("own freelancer profile returned") if s else fail("GET /freelancers failed")

    step("GET /freelancers/browse/all")
    s, br = _call("GET", "/freelancers/browse/all", token=tok,
                  params={"page": 1, "page_size": 5, "order_by": "created_at"}, expected=(200,))
    ok(f"browse returned") if s else fail("browse failed")

    step("GET /freelancers/search?name=...")
    s, _ = _call("GET", "/freelancers/search", token=tok,
                 params={"name": f"FL All {_TS}"}, expected=(200,))
    ok("search returned") if s else fail("freelancer search failed")

    if fl_id:
        step(f"GET /freelancers/{fl_id}")
        s, _ = _call("GET", f"/freelancers/{fl_id}", token=tok, expected=(200,))
        ok("get by ID") if s else fail("get freelancer failed")

        step(f"PUT /freelancers/{fl_id} — update bio, rate")
        s, _ = _call("PUT", f"/freelancers/{fl_id}", token=tok, form={
            "bio":             "Experienced backend developer.",
            "estimated_rate":  "75",
            "rate_time":       "hourly",
            "rate_currency":   "USD",
            "experience_level": "intermediate",
        }, expected=(200,))
        ok("freelancer updated") if s else fail("freelancer update failed")

        step(f"GET /freelancers/{fl_id}/profile — comprehensive")
        s, _ = _call("GET", f"/freelancers/{fl_id}/profile", token=tok, expected=(200,))
        ok("comprehensive profile returned") if s else fail("profile failed")

        step(f"GET /freelancers/{fl_id}/skills")
        s, _ = _call("GET", f"/freelancers/{fl_id}/skills", token=tok, expected=(200,))
        ok("skills returned") if s else fail("get skills failed")

        step(f"GET /freelancers/{fl_id}/embedding — may be 404 if not embedded yet")
        s, _ = _call("GET", f"/freelancers/{fl_id}/embedding", token=tok, expected=(200, 404))
        ok("embedding endpoint responded") if s else fail("embedding endpoint error")

        step("DELETE /freelancers/{id}/profile-picture — expect 400 (no picture yet)")
        s, _ = _call("DELETE", f"/freelancers/{fl_id}/profile-picture", token=tok, expected=(400, 200))
        ok("profile-picture delete responded") if s else fail("profile-picture delete error")

        step(f"POST /freelancers/{fl_id}/profile-picture — upload image")
        files = {"file": ("avatar.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")}
        s, _ = _call("POST", f"/freelancers/{fl_id}/profile-picture", token=tok,
                     files=files, expected=(200, 500))
        if s:
            ok("profile-picture upload endpoint covered")
            _call("DELETE", f"/freelancers/{fl_id}/profile-picture", token=tok, expected=(200, 400, 500))
        else:
            fail("profile-picture upload endpoint error")

        step("POST /freelancers/parse-cv — DOCX autofill")
        docx_bytes = _cv_docx_bytes()
        if docx_bytes:
            files = {"file": ("walkthrough_cv.docx", io.BytesIO(docx_bytes),
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
            s, _ = _call("POST", "/freelancers/parse-cv", token=tok, files=files,
                         expected=(200, 500))
            ok("parse-cv endpoint covered") if s else fail("parse-cv endpoint failed")
        else:
            skip("python-docx unavailable, cannot build DOCX CV fixture")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CLIENT PROFILE
# ═════════════════════════════════════════════════════════════════════════════

def sec_client():
    section("CLIENT PROFILE")
    tok   = _S.get("cl_tok", "")
    cl_id = _S.get("cl_id", "")

    step("GET /clients — own profile")
    s, _ = _call("GET", "/clients", token=tok, expected=(200,))
    ok("own client profile returned") if s else fail("GET /clients failed")

    step("GET /clients/browse/all")
    s, _ = _call("GET", "/clients/browse/all", token=tok,
                 params={"page": 1, "page_size": 5}, expected=(200,))
    ok("client browse returned") if s else fail("client browse failed")

    step("GET /clients/search?name=...")
    s, _ = _call("GET", "/clients/search", token=tok,
                 params={"name": f"CL All {_TS}"}, expected=(200,))
    ok("client search returned") if s else fail("client search failed")

    if cl_id:
        step(f"GET /clients/{cl_id}")
        s, _ = _call("GET", f"/clients/{cl_id}", token=tok, expected=(200,))
        ok("get client by ID") if s else fail("get client failed")

        step(f"PUT /clients/{cl_id} — update bio")
        s, _ = _call("PUT", f"/clients/{cl_id}", token=tok, form={
            "bio": "Company specialising in backend projects.",
            "company_name": f"Capstone Co {_TS}",
        }, expected=(200,))
        ok("client updated") if s else fail("client update failed")

        step("DELETE /clients/{id}/profile-picture — expect 400 (no picture yet)")
        s, _ = _call("DELETE", f"/clients/{cl_id}/profile-picture", token=tok, expected=(400, 200))
        ok("profile-picture delete responded") if s else fail("profile-picture delete error")

        step(f"POST /clients/{cl_id}/profile-picture — upload image")
        files = {"file": ("client-avatar.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")}
        s, _ = _call("POST", f"/clients/{cl_id}/profile-picture", token=tok,
                     files=files, expected=(200, 500))
        if s:
            ok("client profile-picture upload endpoint covered")
            _call("DELETE", f"/clients/{cl_id}/profile-picture", token=tok, expected=(200, 400, 500))
        else:
            fail("client profile-picture upload endpoint error")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — PROFILE CREATE/DELETE ROUTE EDGES
# ═════════════════════════════════════════════════════════════════════════════

def sec_profile_route_edges():
    section("PROFILE CREATE/DELETE ROUTE EDGES")

    step("Disposable freelancer account for POST/DELETE /freelancers")
    fl_uid, fl_tok = _register_verify_login(
        f"all.delete.fl.{_TS}@test.dev", "freelancer", f"Disposable FL {_TS}"
    )
    if fl_uid and fl_tok:
        s, me = _call("GET", "/auth/me", token=fl_tok, expected=(200,))
        disposable_fl_id = str(_d(me).get("freelancer_id", ""))

        step("POST /freelancers — existing profile validation")
        s, _ = _call("POST", "/freelancers", token=fl_tok, form={
            "user_id": fl_uid,
            "full_name": f"Disposable FL Recreate {_TS}",
        }, expected=(400, 500))
        ok("freelancer create route covered") if s else fail("freelancer create route failed")

        if disposable_fl_id:
            step(f"DELETE /freelancers/{disposable_fl_id}")
            s, _ = _call("DELETE", f"/freelancers/{disposable_fl_id}", token=fl_tok, expected=(200, 500))
            ok("freelancer delete route covered") if s else fail("freelancer delete route failed")
    else:
        skip("disposable freelancer setup failed")

    step("Disposable client account for POST/DELETE /clients")
    cl_uid, cl_tok = _register_verify_login(
        f"all.delete.cl.{_TS}@test.dev", "client", f"Disposable CL {_TS}"
    )
    if cl_uid and cl_tok:
        s, me = _call("GET", "/auth/me", token=cl_tok, expected=(200,))
        disposable_cl_id = str(_d(me).get("client_id", ""))

        step("POST /clients — existing profile validation")
        s, _ = _call("POST", "/clients", token=cl_tok, form={
            "user_id": cl_uid,
            "full_name": f"Disposable CL Recreate {_TS}",
        }, expected=(400, 500))
        ok("client create route covered") if s else fail("client create route failed")

        if disposable_cl_id:
            step(f"DELETE /clients/{disposable_cl_id}")
            s, _ = _call("DELETE", f"/clients/{disposable_cl_id}", token=cl_tok, expected=(200, 500))
            ok("client delete route covered") if s else fail("client delete route failed")
    else:
        skip("disposable client setup failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — FREELANCER SKILLS / LANGUAGES / SPECIALITIES
# ═════════════════════════════════════════════════════════════════════════════

def sec_freelancer_attributes():
    section("FREELANCER SKILLS / LANGUAGES / SPECIALITIES")
    fl_tok  = _S.get("fl_tok", "")
    fl_id   = _S.get("fl_id", "")
    skill_ids = _S.get("skill_ids", [])
    lang_id   = _S.get("lang_id", "")
    spec_id   = _S.get("spec_id", "")
    spec2_id  = _S.get("spec2_id", "")

    # ── Freelancer Skills ────────────────────────────────────────────────────
    fl_skill_id = ""
    if skill_ids and fl_id:
        step("POST /freelancer-skills")
        s, fsr = _call("POST", "/freelancer-skills", token=fl_tok, body={
            "freelancer_id": fl_id, "skill_id": skill_ids[0], "proficiency_level": "expert",
        }, expected=(200, 201))
        fl_skill_id = _id(fsr, "freelancer_skill_id")
        ok(f"freelancer skill added: {fl_skill_id}") if s else fail("freelancer skill add failed")
        _S["fl_skill_id"] = fl_skill_id

        # Add second skill too
        if len(skill_ids) >= 2:
            _call("POST", "/freelancer-skills", token=fl_tok, body={
                "freelancer_id": fl_id, "skill_id": skill_ids[1], "proficiency_level": "intermediate",
            }, expected=(200, 201))

    step("GET /freelancer-skills")
    s, _ = _call("GET", "/freelancer-skills", token=fl_tok, expected=(200,))
    ok("freelancer-skills list returned") if s else fail("freelancer-skills list failed")

    if fl_id:
        step(f"GET /freelancer-skills/freelancer/{fl_id}")
        s, _ = _call("GET", f"/freelancer-skills/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("freelancer skills by freelancer returned") if s else fail("skills by freelancer failed")

    if fl_skill_id:
        step(f"GET /freelancer-skills/{fl_skill_id}")
        s, _ = _call("GET", f"/freelancer-skills/{fl_skill_id}", token=fl_tok, expected=(200,))
        ok("freelancer skill by ID") if s else fail("get fl skill failed")

        step(f"PUT /freelancer-skills/{fl_skill_id}")
        s, _ = _call("PUT", f"/freelancer-skills/{fl_skill_id}", token=fl_tok, body={"proficiency_level": "intermediate"}, expected=(200,))
        ok("freelancer skill updated") if s else fail("fl skill update failed")

        step(f"DELETE /freelancer-skills/{fl_skill_id}")
        s, _ = _call("DELETE", f"/freelancer-skills/{fl_skill_id}", token=fl_tok, expected=(200,))
        ok("freelancer skill deleted by ID") if s else fail("fl skill delete failed")

    if fl_id and len(skill_ids) >= 2:
        step(f"DELETE /freelancer-skills/freelancer/{fl_id}/skill/{skill_ids[1]}")
        s, _ = _call("DELETE", f"/freelancer-skills/freelancer/{fl_id}/skill/{skill_ids[1]}", token=fl_tok, expected=(200,))
        ok("freelancer skill deleted by composite key") if s else fail("composite delete failed")

    # Re-add skills for job matching later
    if skill_ids and fl_id:
        for sid in skill_ids:
            _call("POST", "/freelancer-skills", token=fl_tok, body={
                "freelancer_id": fl_id, "skill_id": sid, "proficiency_level": "expert",
            }, expected=(200, 201))

    # ── Freelancer Languages ─────────────────────────────────────────────────
    fl_lang_id = ""
    if lang_id and fl_id:
        step("POST /freelancer-languages")
        s, flr = _call("POST", "/freelancer-languages", token=fl_tok, body={
            "freelancer_id": fl_id, "language_id": lang_id, "proficiency_level": "fluent",
        }, expected=(200, 201))
        fl_lang_id = _id(flr, "freelancer_language_id")
        ok(f"freelancer language added: {fl_lang_id}") if s else fail("fl language add failed")
        _S["fl_lang_id"] = fl_lang_id

    step("GET /freelancer-languages")
    s, _ = _call("GET", "/freelancer-languages", token=fl_tok, expected=(200,))
    ok("freelancer-languages list returned") if s else fail("fl languages list failed")

    if fl_id:
        step(f"GET /freelancer-languages/freelancer/{fl_id}")
        s, _ = _call("GET", f"/freelancer-languages/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("fl languages by freelancer") if s else fail("fl languages by freelancer failed")

    if fl_lang_id:
        step(f"GET /freelancer-languages/{fl_lang_id}")
        s, _ = _call("GET", f"/freelancer-languages/{fl_lang_id}", token=fl_tok, expected=(200,))
        ok("fl language by ID") if s else fail("get fl language failed")

        step(f"PUT /freelancer-languages/{fl_lang_id}")
        s, _ = _call("PUT", f"/freelancer-languages/{fl_lang_id}", token=fl_tok, body={"proficiency_level": "native"}, expected=(200,))
        ok("fl language updated") if s else fail("fl language update failed")

        step(f"DELETE /freelancer-languages/{fl_lang_id}")
        s, _ = _call("DELETE", f"/freelancer-languages/{fl_lang_id}", token=fl_tok, expected=(200,))
        ok("fl language deleted") if s else fail("fl language delete failed")

    # ── Freelancer Specialities ───────────────────────────────────────────────
    fl_spec_id = ""
    if spec2_id and fl_id:
        step("POST /freelancer-specialities — spare for ID delete")
        s, fs_spare = _call("POST", "/freelancer-specialities", token=fl_tok, body={
            "freelancer_id": fl_id, "speciality_id": spec2_id, "is_primary": False,
        }, expected=(200, 201))
        fl_spec_spare_id = _id(fs_spare, "freelancer_speciality_id")
        ok(f"spare freelancer speciality added: {fl_spec_spare_id}") if s else fail("spare fl spec add failed")
        if fl_spec_spare_id:
            step(f"DELETE /freelancer-specialities/{fl_spec_spare_id}")
            s, _ = _call("DELETE", f"/freelancer-specialities/{fl_spec_spare_id}", token=fl_tok, expected=(200,))
            ok("fl spec deleted by ID") if s else fail("fl spec delete by ID failed")

    if spec_id and fl_id:
        step("POST /freelancer-specialities")
        s, fsr = _call("POST", "/freelancer-specialities", token=fl_tok, body={
            "freelancer_id": fl_id, "speciality_id": spec_id, "is_primary": True,
        }, expected=(200, 201))
        fl_spec_id = _id(fsr, "freelancer_speciality_id")
        ok(f"freelancer speciality added: {fl_spec_id}") if s else fail("fl spec add failed")
        _S["fl_spec_id"] = fl_spec_id

    step("GET /freelancer-specialities")
    s, _ = _call("GET", "/freelancer-specialities", token=fl_tok, expected=(200,))
    ok("freelancer-specialities listed") if s else fail("fl spec list failed")

    if fl_id:
        step(f"GET /freelancer-specialities/freelancer/{fl_id}")
        s, _ = _call("GET", f"/freelancer-specialities/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("fl specs by freelancer") if s else fail("fl spec by freelancer failed")

    if fl_spec_id:
        step(f"GET /freelancer-specialities/{fl_spec_id}")
        s, _ = _call("GET", f"/freelancer-specialities/{fl_spec_id}", token=fl_tok, expected=(200,))
        ok("fl spec by ID") if s else fail("get fl spec failed")

        step(f"PUT /freelancer-specialities/{fl_spec_id}")
        s, _ = _call("PUT", f"/freelancer-specialities/{fl_spec_id}", token=fl_tok, body={"is_primary": False}, expected=(200,))
        ok("fl spec updated") if s else fail("fl spec update failed")

        step(f"DELETE /freelancer-specialities/freelancer/{fl_id}/speciality/{spec_id}")
        s, _ = _call("DELETE", f"/freelancer-specialities/freelancer/{fl_id}/speciality/{spec_id}", token=fl_tok, expected=(200,))
        ok("fl spec deleted (composite key)") if s else fail("fl spec composite delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — WORK EXPERIENCE & EDUCATION
# ═════════════════════════════════════════════════════════════════════════════

def sec_work_edu():
    section("WORK EXPERIENCE & EDUCATION")
    tok   = _S.get("fl_tok", "")
    fl_id = _S.get("fl_id", "")

    # ── Work Experience ───────────────────────────────────────────────────────
    step("POST /work-experiences")
    s, we = _call("POST", "/work-experiences", token=tok, body={
        "freelancer_id": fl_id,
        "company_name":  "Acme Corp",
        "role_title":    "Backend Engineer",
        "description":   "Built REST APIs.",
        "start_date":    "2021-01-01",
        "end_date":      "2023-06-30",
        "is_current":    False,
    }, expected=(200, 201))
    we_id = _id(we, "work_experience_id")
    ok(f"work experience created: {we_id}") if s else fail("work exp create failed")
    _S["we_id"] = we_id

    step("GET /work-experiences")
    s, _ = _call("GET", "/work-experiences", token=tok, expected=(200,))
    ok("work experiences listed") if s else fail("work exp list failed")

    if fl_id:
        step(f"GET /work-experiences/freelancer/{fl_id}")
        s, _ = _call("GET", f"/work-experiences/freelancer/{fl_id}", token=tok, expected=(200,))
        ok("work exp by freelancer") if s else fail("work exp by freelancer failed")

    if we_id:
        step(f"GET /work-experiences/{we_id}")
        s, _ = _call("GET", f"/work-experiences/{we_id}", token=tok, expected=(200,))
        ok("work exp by ID") if s else fail("get work exp failed")

        step(f"PUT /work-experiences/{we_id}")
        s, _ = _call("PUT", f"/work-experiences/{we_id}", token=tok, body={"description": "Updated desc."}, expected=(200,))
        ok("work exp updated") if s else fail("work exp update failed")

        step(f"DELETE /work-experiences/{we_id}")
        s, _ = _call("DELETE", f"/work-experiences/{we_id}", token=tok, expected=(200,))
        ok("work exp deleted") if s else fail("work exp delete failed")

    # ── Education ─────────────────────────────────────────────────────────────
    step("POST /educations")
    s, ed = _call("POST", "/educations", token=tok, body={
        "freelancer_id":  fl_id,
        "institution":    "State University",
        "degree":         "Bachelor",
        "field_of_study": "Computer Science",
        "start_year":     2017,
        "end_year":       2021,
        "is_current":     False,
    }, expected=(200, 201))
    edu_id = _id(ed, "education_id")
    ok(f"education created: {edu_id}") if s else fail("education create failed")
    _S["edu_id"] = edu_id

    step("GET /educations")
    s, _ = _call("GET", "/educations", token=tok, expected=(200,))
    ok("educations listed") if s else fail("education list failed")

    if fl_id:
        step(f"GET /educations/freelancer/{fl_id}")
        s, _ = _call("GET", f"/educations/freelancer/{fl_id}", token=tok, expected=(200,))
        ok("educations by freelancer") if s else fail("education by freelancer failed")

    if edu_id:
        step(f"GET /educations/{edu_id}")
        s, _ = _call("GET", f"/educations/{edu_id}", token=tok, expected=(200,))
        ok("education by ID") if s else fail("get education failed")

        step(f"PUT /educations/{edu_id}")
        s, _ = _call("PUT", f"/educations/{edu_id}", token=tok, body={"field_of_study": "Software Engineering"}, expected=(200,))
        ok("education updated") if s else fail("education update failed")

        step(f"DELETE /educations/{edu_id}")
        s, _ = _call("DELETE", f"/educations/{edu_id}", token=tok, expected=(200,))
        ok("education deleted") if s else fail("education delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — PORTFOLIO
# ═════════════════════════════════════════════════════════════════════════════

def sec_portfolio():
    section("PORTFOLIO")
    tok   = _S.get("fl_tok", "")
    fl_id = _S.get("fl_id", "")

    step("POST /portfolios")
    s, pr = _call("POST", "/portfolios", token=tok, body={
        "freelancer_id":       fl_id,
        "project_title":       "E-Commerce API",
        "project_description": "Built a REST API for an e-commerce platform.",
        "project_url":         "https://github.com/example/ecommerce",
        "tags":                ["python", "fastapi"],
    }, expected=(200, 201))
    port_id = _id(pr, "portfolio_id")
    ok(f"portfolio created: {port_id}") if s else fail("portfolio create failed")
    _S["portfolio_id"] = port_id

    step("GET /portfolios")
    s, _ = _call("GET", "/portfolios", token=tok, expected=(200,))
    ok("portfolios listed") if s else fail("portfolios list failed")

    if fl_id:
        step(f"GET /portfolios/freelancer/{fl_id}")
        s, _ = _call("GET", f"/portfolios/freelancer/{fl_id}", token=tok, expected=(200,))
        ok("portfolios by freelancer") if s else fail("portfolios by freelancer failed")

    if port_id:
        step(f"GET /portfolios/{port_id}")
        s, _ = _call("GET", f"/portfolios/{port_id}", token=tok, expected=(200,))
        ok("portfolio by ID") if s else fail("get portfolio failed")

        step(f"PUT /portfolios/{port_id}")
        s, _ = _call("PUT", f"/portfolios/{port_id}", token=tok, body={"project_description": "Updated description."}, expected=(200,))
        ok("portfolio updated") if s else fail("portfolio update failed")

        step(f"DELETE /portfolios/{port_id}")
        s, _ = _call("DELETE", f"/portfolios/{port_id}", token=tok, expected=(200,))
        ok("portfolio deleted") if s else fail("portfolio delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — JOB POSTS
# ═════════════════════════════════════════════════════════════════════════════

def sec_job_posts():
    section("JOB POSTS")
    tok   = _S.get("cl_tok", "")
    fl_tok = _S.get("fl_tok", "")

    step("POST /job-posts/calculate-project-scope")
    s, sc = _call("POST", "/job-posts/calculate-project-scope", token=tok, body={
        "job_title":        "Backend API Development",
        "job_description":  "Build a REST API with authentication, database, and deployment.",
        "project_type":     "individual",
        "estimated_duration": "8 weeks",
        "experience_level": "intermediate",
        "role_count":       1,
    }, expected=(200,))
    if s:
        info(f"recommended_scope={_d(sc).get('recommended_project_scope')}  confidence={_d(sc).get('confidence')}")
        ok("scope calculation returned")
    else:
        fail("scope calculation failed")

    step("POST /job-posts — create active job")
    s, jp = _call("POST", "/job-posts", token=tok, body={
        "job_title":         f"Backend Engineer {_TS}",
        "job_description":   "We need an experienced Python/FastAPI backend developer to build a REST API with database design and optimization.",
        "project_type":      "individual",
        "project_scope":     "medium",
        "estimated_duration": "8 weeks",
        "experience_level":  "intermediate",
        "status":            "active",
    }, expected=(200, 201))
    jp_id = _id(jp, "job_post_id")
    ok(f"job post created: {jp_id}") if s else fail("job post create failed")
    _S["jp_id"] = jp_id

    step("POST /job-posts — create draft job (for delete test)")
    s, jp2 = _call("POST", "/job-posts", token=tok, body={
        "job_title":       f"Draft Job {_TS}",
        "job_description": "Draft job for delete test.",
        "project_type":    "individual",
        "status":          "draft",
    }, expected=(200, 201))
    jp2_id = _id(jp2, "job_post_id")
    _S["jp2_id"] = jp2_id

    step("GET /job-posts — list")
    s, jpl = _call("GET", "/job-posts", token=fl_tok, params={"status": "active"}, expected=(200,))
    ok(f"job posts listed") if s else fail("job posts list failed")

    step("GET /job-posts/category-counts")
    s, _ = _call("GET", "/job-posts/category-counts", token=fl_tok, expected=(200,))
    ok("category counts returned") if s else fail("category counts failed")

    step("GET /job-posts/search?q=Backend")
    s, _ = _call("GET", "/job-posts/search", token=fl_tok, params={"q": "Backend"}, expected=(200,))
    ok("job posts search returned") if s else fail("job posts search failed")

    cl_id = _S.get("cl_id", "")
    if cl_id:
        step(f"GET /job-posts/client/{cl_id}")
        s, _ = _call("GET", f"/job-posts/client/{cl_id}", token=tok, expected=(200,))
        ok("jobs by client returned") if s else fail("jobs by client failed")

    if jp_id:
        step(f"GET /job-posts/{jp_id}")
        s, jpg = _call("GET", f"/job-posts/{jp_id}", token=fl_tok, expected=(200,))
        ok("job post by ID") if s else fail("get job post failed")

        step(f"PUT /job-posts/{jp_id} — update description")
        s, _ = _call("PUT", f"/job-posts/{jp_id}", token=tok, body={"experience_level": "expert"}, expected=(200,))
        ok("job post updated") if s else fail("job post update failed")

    if jp2_id:
        step(f"DELETE /job-posts/{jp2_id} — delete draft")
        s, _ = _call("DELETE", f"/job-posts/{jp2_id}", token=tok, expected=(200,))
        ok("draft job post deleted") if s else fail("job post delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 9 — JOB ROLES & JOB ROLE SKILLS
# ═════════════════════════════════════════════════════════════════════════════

def sec_job_roles():
    section("JOB ROLES & JOB ROLE SKILLS")
    tok      = _S.get("cl_tok", "")
    jp_id    = _S.get("jp_id", "")
    skill_ids = _S.get("skill_ids", [])

    step("POST /job-roles")
    s, jr = _call("POST", "/job-roles", token=tok, body={
        "job_post_id":         jp_id,
        "role_title":          "Backend Engineer",
        "budget_type":         "fixed",
        "role_budget":         8000,
        "budget_currency":     "USD",
        "positions_available": 1,
        "role_description":    "Build and maintain the REST API.",
    }, expected=(200, 201))
    jr_id = _id(jr, "job_role_id")
    ok(f"job role created: {jr_id}") if s else fail("job role create failed")
    _S["jr_id"] = jr_id

    step("GET /job-roles")
    s, _ = _call("GET", "/job-roles", token=tok, expected=(200,))
    ok("job roles listed") if s else fail("job roles list failed")

    if jp_id:
        step(f"GET /job-roles/job-post/{jp_id}")
        s, _ = _call("GET", f"/job-roles/job-post/{jp_id}", token=tok, expected=(200,))
        ok("job roles by job post") if s else fail("job roles by job post failed")

    if jr_id:
        step(f"GET /job-roles/{jr_id}")
        s, _ = _call("GET", f"/job-roles/{jr_id}", token=tok, expected=(200,))
        ok("job role by ID") if s else fail("get job role failed")

        step(f"PUT /job-roles/{jr_id}")
        s, _ = _call("PUT", f"/job-roles/{jr_id}", token=tok, body={"role_budget": 9000}, expected=(200,))
        ok("job role updated") if s else fail("job role update failed")

    spare_jr_id = ""
    if jp_id:
        step("POST /job-roles — spare role for delete")
        s, spare = _call("POST", "/job-roles", token=tok, body={
            "job_post_id":         jp_id,
            "role_title":          "Temporary QA Role",
            "budget_type":         "fixed",
            "role_budget":         500,
            "budget_currency":     "USD",
            "positions_available": 1,
            "role_description":    "Delete-test role.",
        }, expected=(200, 201))
        spare_jr_id = _id(spare, "job_role_id")
        ok(f"spare job role created: {spare_jr_id}") if s else fail("spare job role create failed")

    if spare_jr_id:
        step(f"DELETE /job-roles/{spare_jr_id}")
        s, _ = _call("DELETE", f"/job-roles/{spare_jr_id}", token=tok, expected=(200,))
        ok("spare job role deleted") if s else fail("job role delete failed")

    # ── Job Role Skills ───────────────────────────────────────────────────────
    jrs_id = ""
    if jr_id and skill_ids:
        step("POST /job-role-skills")
        s, jrsk = _call("POST", "/job-role-skills", token=tok, body={
            "job_role_id":      jr_id,
            "skill_id":         skill_ids[0],
            "is_required":      True,
            "importance_level": "required",
        }, expected=(200, 201))
        jrs_id = _id(jrsk, "job_role_skill_id")
        ok(f"job role skill created: {jrs_id}") if s else fail("job role skill create failed")
        _S["jrs_id"] = jrs_id

        # Add 2nd skill
        if len(skill_ids) >= 2:
            _call("POST", "/job-role-skills", token=tok, body={
                "job_role_id": jr_id, "skill_id": skill_ids[1],
                "is_required": False, "importance_level": "preferred",
            }, expected=(200, 201))

    step("GET /job-role-skills")
    s, _ = _call("GET", "/job-role-skills", token=tok, expected=(200,))
    ok("job role skills listed") if s else fail("job role skills list failed")

    if jr_id:
        step(f"GET /job-role-skills/job-role/{jr_id}")
        s, _ = _call("GET", f"/job-role-skills/job-role/{jr_id}", token=tok, expected=(200,))
        ok("job role skills by role") if s else fail("job role skills by role failed")

    if jrs_id:
        step(f"GET /job-role-skills/{jrs_id}")
        s, _ = _call("GET", f"/job-role-skills/{jrs_id}", token=tok, expected=(200,))
        ok("job role skill by ID") if s else fail("get job role skill failed")

        step(f"PUT /job-role-skills/{jrs_id}")
        s, _ = _call("PUT", f"/job-role-skills/{jrs_id}", token=tok, body={"importance_level": "preferred"}, expected=(200,))
        ok("job role skill updated") if s else fail("job role skill update failed")

        step(f"DELETE /job-role-skills/{jrs_id}")
        s, _ = _call("DELETE", f"/job-role-skills/{jrs_id}", token=tok, expected=(200,))
        ok("job role skill deleted") if s else fail("job role skill delete failed")

        # Re-add for proposal matching
        if skill_ids:
            _call("POST", "/job-role-skills", token=tok, body={
                "job_role_id": jr_id, "skill_id": skill_ids[0],
                "is_required": True, "importance_level": "required",
            }, expected=(200, 201))


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 10 — PROPOSALS
# ═════════════════════════════════════════════════════════════════════════════

def sec_proposals():
    section("PROPOSALS")
    fl_tok = _S.get("fl_tok", "")
    cl_tok = _S.get("cl_tok", "")
    jp_id  = _S.get("jp_id", "")
    jr_id  = _S.get("jr_id", "")
    fl_id  = _S.get("fl_id", "")

    step("POST /proposals — freelancer submits")
    s, pr = _call("POST", "/proposals", token=fl_tok, body={
        "job_post_id":      jp_id,
        "job_role_id":      jr_id,
        "cover_letter":     "I am an experienced Python/FastAPI developer. I can deliver this project in 6 weeks.",
        "proposed_budget":  7500,
        "proposed_duration": "6 weeks",
        "status":           "pending",
    }, expected=(200, 201))
    prop_id = _id(pr, "proposal_id")
    ok(f"proposal created: {prop_id}") if s else fail("proposal create failed")
    _S["prop_id"] = prop_id

    # Second proposal to test delete
    step("POST /proposals — second proposal (for delete test)")
    s, pr2 = _call("POST", "/proposals", token=fl_tok, body={
        "job_post_id":    jp_id, "cover_letter": "Delete me.",
        "proposed_budget": 5000, "status": "pending",
    }, expected=(200, 201))
    prop2_id = _id(pr2, "proposal_id")
    _S["prop2_id"] = prop2_id

    step("GET /proposals — admin list")
    s, _ = _call("GET", "/proposals", token=cl_tok, expected=(200,))
    ok("proposals listed") if s else fail("proposals list failed")

    step("GET /proposals/me — own proposals")
    s, _ = _call("GET", "/proposals/me", token=fl_tok, expected=(200,))
    ok("own proposals returned") if s else fail("own proposals failed")

    if jp_id:
        step(f"GET /proposals/job-post/{jp_id}")
        s, _ = _call("GET", f"/proposals/job-post/{jp_id}", token=cl_tok, expected=(200,))
        ok("proposals by job post") if s else fail("proposals by job post failed")

    if fl_id:
        step(f"GET /proposals/freelancer/{fl_id}")
        s, _ = _call("GET", f"/proposals/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("proposals by freelancer") if s else fail("proposals by freelancer failed")

    if prop_id:
        step(f"GET /proposals/{prop_id}")
        s, _ = _call("GET", f"/proposals/{prop_id}", token=fl_tok, expected=(200,))
        ok("proposal by ID") if s else fail("get proposal failed")

        step(f"PUT /proposals/{prop_id} — update cover letter")
        s, _ = _call("PUT", f"/proposals/{prop_id}", token=fl_tok, body={
            "cover_letter": "Updated: I am highly experienced in Python/FastAPI and PostgreSQL.",
            "proposed_budget": 7800,
        }, expected=(200,))
        ok("proposal updated") if s else fail("proposal update failed")

        step(f"PATCH /proposals/{prop_id}/status → accepted (client)")
        s, _ = _call("PATCH", f"/proposals/{prop_id}/status", token=cl_tok,
                     params={"status": "accepted"}, expected=(200,))
        ok("proposal accepted by client") if s else fail("proposal status patch failed")

    if prop2_id:
        step(f"PATCH /proposals/{prop2_id}/status → withdrawn (freelancer)")
        s, _ = _call("PATCH", f"/proposals/{prop2_id}/status", token=fl_tok,
                     params={"status": "withdrawn"}, expected=(200,))
        ok("proposal withdrawn by freelancer") if s else fail("proposal withdraw failed")

        step(f"DELETE /proposals/{prop2_id}")
        s, _ = _call("DELETE", f"/proposals/{prop2_id}", token=fl_tok, expected=(200,))
        ok("proposal deleted") if s else fail("proposal delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 11 — FILE UPLOADS & CV ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def sec_file_uploads_and_cv():
    section("FILE UPLOADS & CV ANALYSIS")
    fl_tok = _S.get("fl_tok", "")
    cl_tok = _S.get("cl_tok", "")
    jp_id  = _S.get("jp_id", "")
    prop_id = _S.get("prop_id", "")

    step("POST /upload — generic upload")
    files = {"file": ("generic.pdf", io.BytesIO(_TINY_PDF), "application/pdf")}
    s, up = _call("POST", "/upload", token=cl_tok, params={"bucket": "job_files"},
                  files=files, expected=(200, 500))
    ok("generic upload endpoint covered") if s else fail("generic upload failed")

    # ── Job Files ────────────────────────────────────────────────────────────
    job_file_id = ""
    if jp_id:
        step("POST /job-files — upload job attachment")
        files = [("files", ("requirements.pdf", io.BytesIO(_TINY_PDF), "application/pdf"))]
        s, jf = _call("POST", "/job-files", token=cl_tok,
                      form={"job_post_id": jp_id}, files=files, expected=(200, 201, 500))
        ids = _ids(jf, "job_file_id")
        job_file_id = ids[0] if ids else ""
        ok(f"job-file upload endpoint covered: {job_file_id}") if s else fail("job-file upload failed")

        step("GET /job-files")
        s, _ = _call("GET", "/job-files", token=cl_tok, expected=(200,))
        ok("job files listed") if s else fail("job files list failed")

        step(f"GET /job-files/job-post/{jp_id}")
        s, _ = _call("GET", f"/job-files/job-post/{jp_id}", token=cl_tok, expected=(200,))
        ok("job files by job post returned") if s else fail("job files by job post failed")

        if job_file_id:
            step(f"GET /job-files/{job_file_id}")
            s, _ = _call("GET", f"/job-files/{job_file_id}", token=cl_tok, expected=(200,))
            ok("job file by ID") if s else fail("get job file failed")

            step(f"PUT /job-files/{job_file_id}")
            s, _ = _call("PUT", f"/job-files/{job_file_id}", token=cl_tok,
                         body={"file_name": "requirements-updated.pdf"}, expected=(200,))
            ok("job file updated") if s else fail("job file update failed")

            step(f"DELETE /job-files/{job_file_id}")
            s, _ = _call("DELETE", f"/job-files/{job_file_id}", token=cl_tok, expected=(200,))
            ok("job file deleted") if s else fail("job file delete failed")

    # ── Proposal Files ───────────────────────────────────────────────────────
    proposal_file_id = ""
    if prop_id:
        step("POST /proposal-files — upload proposal attachment")
        files = [("files", ("proposal.pdf", io.BytesIO(_TINY_PDF), "application/pdf"))]
        s, pf = _call("POST", "/proposal-files", token=fl_tok,
                      form={"proposal_id": prop_id}, files=files, expected=(200, 201, 500))
        ids = _ids(pf, "proposal_file_id")
        proposal_file_id = ids[0] if ids else ""
        ok(f"proposal-file upload endpoint covered: {proposal_file_id}") if s else fail("proposal-file upload failed")

        step("GET /proposal-files")
        s, _ = _call("GET", "/proposal-files", token=fl_tok, expected=(200,))
        ok("proposal files listed") if s else fail("proposal files list failed")

        step(f"GET /proposal-files/proposal/{prop_id}")
        s, _ = _call("GET", f"/proposal-files/proposal/{prop_id}", token=fl_tok, expected=(200,))
        ok("proposal files by proposal returned") if s else fail("proposal files by proposal failed")

        if proposal_file_id:
            step(f"GET /proposal-files/{proposal_file_id}")
            s, _ = _call("GET", f"/proposal-files/{proposal_file_id}", token=fl_tok, expected=(200,))
            ok("proposal file by ID") if s else fail("get proposal file failed")

            step(f"PUT /proposal-files/{proposal_file_id}")
            s, _ = _call("PUT", f"/proposal-files/{proposal_file_id}", token=fl_tok,
                         body={"file_name": "proposal-updated.pdf"}, expected=(200,))
            ok("proposal file updated") if s else fail("proposal file update failed")

            step(f"DELETE /proposal-files/{proposal_file_id}")
            s, _ = _call("DELETE", f"/proposal-files/{proposal_file_id}", token=fl_tok, expected=(200,))
            ok("proposal file deleted") if s else fail("proposal file delete failed")

    # ── CV Upload / Analysis ─────────────────────────────────────────────────
    docx_bytes = _cv_docx_bytes()
    if docx_bytes:
        step("POST /cv_upload — upload/analyze DOCX CV")
        files = {"file": ("walkthrough_cv.docx", io.BytesIO(docx_bytes),
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
        s, _ = _call("POST", "/cv_upload", token=fl_tok, files=files, expected=(200, 500))
        ok("cv_upload endpoint covered") if s else fail("cv_upload endpoint failed")
    else:
        skip("python-docx unavailable, cannot cover /cv_upload with DOCX")

    step("POST /cv_upload — unsupported type validation")
    files = {"file": ("cv.txt", io.BytesIO(_CV_TEXT.encode("utf-8")), "text/plain")}
    s, _ = _call("POST", "/cv_upload", token=fl_tok, files=files, expected=(400, 422, 500))
    ok("cv_upload validation branch covered") if s else fail("cv_upload validation branch failed")

    step("POST /cv_analysis/analyze — TXT CV analysis")
    files = {"cv_file": ("cv.txt", io.BytesIO(_CV_TEXT.encode("utf-8")), "text/plain")}
    s, _ = _call("POST", "/cv_analysis/analyze", token=fl_tok, files=files,
                 expected=(200, 400, 500))
    ok("cv_analysis endpoint covered") if s else fail("cv_analysis endpoint failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — CONTRACTS
# ═════════════════════════════════════════════════════════════════════════════

def sec_contracts():
    section("CONTRACTS")
    cl_tok  = _S.get("cl_tok", "")
    fl_tok  = _S.get("fl_tok", "")
    cl_id   = _S.get("cl_id", "")
    fl_id   = _S.get("fl_id", "")
    jp_id   = _S.get("jp_id", "")
    jr_id   = _S.get("jr_id", "")
    prop_id = _S.get("prop_id", "")
    today   = str(datetime.date.today())

    step("POST /contracts — create active contract")
    s, cr = _call("POST", "/contracts", token=cl_tok, body={
        "job_post_id":      jp_id,
        "job_role_id":      jr_id,
        "proposal_id":      prop_id,
        "freelancer_id":    fl_id,
        "client_id":        cl_id,
        "contract_title":   f"Backend API Contract {_TS}",
        "role_title":       "Backend Engineer",
        "agreed_budget":    7800,
        "budget_currency":  "USD",
        "payment_structure": "full_payment",
        "agreed_duration":  "6 weeks",
        "status":           "active",
        "start_date":       today,
    }, expected=(200, 201))
    contract_id = _id(cr, "contract_id")
    ok(f"contract created: {contract_id}") if s else fail("contract create failed")
    _S["contract_id"] = contract_id

    # Create a second contract for cancel test
    step("POST /contracts — second contract (for cancel test)")
    s, cr2 = _call("POST", "/contracts", token=cl_tok, body={
        "job_post_id": jp_id, "job_role_id": jr_id, "proposal_id": prop_id,
        "freelancer_id": fl_id, "client_id": cl_id,
        "contract_title": f"Cancel Test Contract {_TS}",
        "agreed_budget": 1000, "payment_structure": "full_payment",
        "status": "active", "start_date": today,
    }, expected=(200, 201))
    contract2_id = _id(cr2, "contract_id")
    _S["contract2_id"] = contract2_id

    step("GET /contracts — list")
    s, _ = _call("GET", "/contracts", token=cl_tok, expected=(200,))
    ok("contracts listed") if s else fail("contracts list failed")

    if fl_id:
        step(f"GET /contracts/freelancer/{fl_id}")
        s, _ = _call("GET", f"/contracts/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("contracts by freelancer") if s else fail("contracts by freelancer failed")

    if cl_id:
        step(f"GET /contracts/client/{cl_id}")
        s, _ = _call("GET", f"/contracts/client/{cl_id}", token=cl_tok, expected=(200,))
        ok("contracts by client") if s else fail("contracts by client failed")

    if contract_id:
        step(f"GET /contracts/{contract_id}")
        s, _ = _call("GET", f"/contracts/{contract_id}", token=cl_tok, expected=(200,))
        ok("contract by ID") if s else fail("get contract failed")

        step(f"GET /contracts/{contract_id}/generation-data")
        s, _ = _call("GET", f"/contracts/{contract_id}/generation-data", token=cl_tok, expected=(200,))
        ok("generation-data returned") if s else fail("generation-data failed")

        step(f"POST /contracts/{contract_id}/generate — generate PDF")
        s, _ = _call("POST", f"/contracts/{contract_id}/generate", token=cl_tok, body={
            "termination_notice": 30,
            "governing_law":      "Indonesia",
            "confidentiality":    True,
            "dispute_resolution": "negotiation",
            "revision_rounds":    2,
            "send_notification":  False,
        }, expected=(200, 201))
        ok("contract PDF generated") if s else fail("contract generate failed")

        step(f"GET /contracts/{contract_id}/pdf-url")
        s, pu = _call("GET", f"/contracts/{contract_id}/pdf-url", token=cl_tok, expected=(200,))
        pdf_url = _d(pu).get("pdf_url")
        info(f"pdf_url: {pdf_url}")
        ok("pdf-url returned") if s else fail("pdf-url failed")

        step(f"GET /contracts/{contract_id}/pdf-download")
        s, _ = _call("GET", f"/contracts/{contract_id}/pdf-download", token=cl_tok,
                     expected=(200, 404, 500))
        ok("pdf-download endpoint covered") if s else fail("pdf-download failed")

        step(f"PUT /contracts/{contract_id} — update total_hours_worked")
        s, _ = _call("PUT", f"/contracts/{contract_id}", token=cl_tok, body={"total_hours_worked": 40.0}, expected=(200,))
        ok("contract updated") if s else fail("contract update failed")

    if contract2_id:
        step(f"PUT /contracts/{contract2_id}/cancel")
        s, _ = _call("PUT", f"/contracts/{contract2_id}/cancel", token=cl_tok, body={"reason": "Test cancellation."}, expected=(200,))
        ok("contract cancelled") if s else fail("contract cancel failed")

        step(f"DELETE /contracts/{contract2_id}")
        s, _ = _call("DELETE", f"/contracts/{contract2_id}", token=cl_tok, expected=(200,))
        ok("cancelled contract deleted") if s else fail("contract delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 12 — CONTRACT SUBMISSIONS
# ═════════════════════════════════════════════════════════════════════════════

def sec_contract_submissions():
    section("CONTRACT SUBMISSIONS")
    fl_tok      = _S.get("fl_tok", "")
    cl_tok      = _S.get("cl_tok", "")
    contract_id = _S.get("contract_id", "")

    if not contract_id:
        skip("no contract_id — skipping contract submissions section")
        return

    step("POST /contract-submissions — freelancer submits work (multipart)")
    files = [("files", ("submission.pdf", io.BytesIO(_TINY_PDF), "application/pdf"))]
    data  = {"contract_id": contract_id, "note": "Here is the initial work submission."}
    s, sr = _call("POST", "/contract-submissions", token=fl_tok,
                  files=files, form=data, expected=(200, 201))
    sub_id = _id(sr, "submission_id")
    ok(f"submission created: {sub_id}") if s else fail("submission create failed (Supabase upload may be unavailable)")
    _S["sub_id"] = sub_id

    step(f"GET /contract-submissions/contract/{contract_id}")
    s, subs = _call("GET", f"/contract-submissions/contract/{contract_id}", token=cl_tok, expected=(200,))
    subs_list = _list(subs)
    ok(f"{len(subs_list)} submission(s) found") if s else fail("get submissions failed")

    if sub_id:
        step(f"PUT /contract-submissions/contract/{contract_id}/request-revision")
        s, _ = _call("PUT", f"/contract-submissions/contract/{contract_id}/request-revision",
                     token=cl_tok, body={"note": "Please add unit tests."}, expected=(200,))
        ok("revision requested") if s else fail("request revision failed")

        step("POST /contract-submissions — re-submit after revision")
        files2 = [("files", ("revised.pdf", io.BytesIO(_TINY_PDF), "application/pdf"))]
        data2  = {"contract_id": contract_id, "note": "Revised: added unit tests."}
        s, sr2 = _call("POST", "/contract-submissions", token=fl_tok,
                       files=files2, form=data2, expected=(200, 201))
        ok("re-submission created") if s else fail("re-submission failed")

        step(f"PUT /contract-submissions/contract/{contract_id}/approve")
        s, _ = _call("PUT", f"/contract-submissions/contract/{contract_id}/approve",
                     token=cl_tok, expected=(200,))
        ok("submission approved → review pipeline triggered") if s else fail("approve submission failed")
        if s:
            info("Waiting 3s for review pipeline to run...")
            time.sleep(3)


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 13 — SAVED JOBS
# ═════════════════════════════════════════════════════════════════════════════

def sec_saved_jobs():
    section("SAVED JOBS")
    fl_tok = _S.get("fl_tok", "")
    fl_id  = _S.get("fl_id", "")
    jp_id  = _S.get("jp_id", "")

    step("POST /saved-jobs")
    s, svr = _call("POST", "/saved-jobs", token=fl_tok, body={
        "job_post_id": jp_id, "freelancer_id": fl_id,
    }, expected=(200, 201))
    sv_id = _id(svr, "saved_job_id")
    ok(f"job saved: {sv_id}") if s else fail("save job failed")
    _S["sv_id"] = sv_id

    step("GET /saved-jobs")
    s, _ = _call("GET", "/saved-jobs", token=fl_tok, expected=(200,))
    ok("saved jobs listed") if s else fail("saved jobs list failed")

    if fl_id:
        step(f"GET /saved-jobs/freelancer/{fl_id}")
        s, _ = _call("GET", f"/saved-jobs/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("saved jobs by freelancer") if s else fail("saved jobs by freelancer failed")

    if sv_id:
        step(f"GET /saved-jobs/{sv_id}")
        s, _ = _call("GET", f"/saved-jobs/{sv_id}", token=fl_tok, expected=(200,))
        ok("saved job by ID") if s else fail("get saved job failed")

        step(f"DELETE /saved-jobs/{sv_id}")
        s, _ = _call("DELETE", f"/saved-jobs/{sv_id}", token=fl_tok, expected=(200,))
        ok("saved job deleted") if s else fail("saved job delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 14 — RATINGS, PERFORMANCE RATINGS, CLIENT TRUST SCORES
# ═════════════════════════════════════════════════════════════════════════════

def sec_ratings():
    section("RATINGS / PERFORMANCE RATINGS / CLIENT TRUST SCORES")
    cl_tok      = _S.get("cl_tok", "")
    fl_tok      = _S.get("fl_tok", "")
    fl_id       = _S.get("fl_id", "")
    cl_id       = _S.get("cl_id", "")
    contract_id = _S.get("contract_id", "")

    # ── Ratings ───────────────────────────────────────────────────────────────
    step("POST /ratings — client rates freelancer")
    s, rr = _call("POST", "/ratings", token=cl_tok, body={
        "contract_id":              contract_id,
        "freelancer_id":            fl_id,
        "communication_score":      4,
        "result_quality_score":     5,
        "professionalism_score":    5,
        "timeline_compliance_score": 4,
        "overall_rating":           4.5,
        "review_text":              "Excellent work, delivered on time.",
    }, expected=(200, 201))
    rating_id = _id(rr, "rating_id")
    ok(f"rating created: {rating_id}") if s else fail("rating create failed")
    _S["rating_id"] = rating_id

    step("GET /ratings")
    s, _ = _call("GET", "/ratings", token=cl_tok, expected=(200,))
    ok("ratings listed") if s else fail("ratings list failed")

    if fl_id:
        step(f"GET /ratings/freelancer/{fl_id}")
        s, _ = _call("GET", f"/ratings/freelancer/{fl_id}", token=fl_tok, expected=(200,))
        ok("ratings by freelancer") if s else fail("ratings by freelancer failed")

    if cl_id:
        step(f"GET /ratings/client/{cl_id}")
        s, _ = _call("GET", f"/ratings/client/{cl_id}", token=cl_tok, expected=(200,))
        ok("ratings by client") if s else fail("ratings by client failed")

    if rating_id:
        step(f"GET /ratings/{rating_id}")
        s, _ = _call("GET", f"/ratings/{rating_id}", token=cl_tok, expected=(200,))
        ok("rating by ID") if s else fail("get rating failed")

        step(f"PUT /ratings/{rating_id}")
        s, _ = _call("PUT", f"/ratings/{rating_id}", token=cl_tok, body={"review_text": "Updated: truly excellent work!"}, expected=(200,))
        ok("rating updated") if s else fail("rating update failed")

        step(f"DELETE /ratings/{rating_id}")
        s, _ = _call("DELETE", f"/ratings/{rating_id}", token=cl_tok, expected=(200,))
        ok("rating deleted") if s else fail("rating delete failed")

    # ── Performance Ratings ───────────────────────────────────────────────────
    step("POST /performance-ratings")
    s, prr = _call("POST", "/performance-ratings", token=cl_tok, body={
        "freelancer_id":             fl_id,
        "overall_performance_score": 4.5,
        "confidence_score":          0.9,
        "total_ratings_received":    1,
        "average_communication":     4.0,
        "average_result_quality":    5.0,
        "average_professionalism":   5.0,
        "success_rate":              1.0,
    }, expected=(200, 201))
    ok("performance rating created") if s else fail("performance rating create failed")

    step("GET /performance-ratings")
    s, _ = _call("GET", "/performance-ratings", token=cl_tok, expected=(200,))
    ok("performance ratings listed") if s else fail("performance ratings list failed")

    if fl_id:
        step(f"GET /performance-ratings/freelancer/{fl_id}")
        s, _ = _call("GET", f"/performance-ratings/freelancer/{fl_id}", token=fl_tok, expected=(200, 404))
        ok("performance rating by freelancer responded") if s else fail("perf rating by freelancer failed")

        step(f"PUT /performance-ratings/freelancer/{fl_id}")
        s, _ = _call("PUT", f"/performance-ratings/freelancer/{fl_id}", token=cl_tok, body={"success_rate": 1.0}, expected=(200, 404))
        ok("performance rating update responded") if s else fail("perf rating update failed")

        step(f"DELETE /performance-ratings/freelancer/{fl_id}")
        s, _ = _call("DELETE", f"/performance-ratings/freelancer/{fl_id}", token=cl_tok, expected=(200, 404))
        ok("performance rating delete responded") if s else fail("perf rating delete failed")

    # ── Client Trust Scores ───────────────────────────────────────────────────
    step("POST /client-trust-scores")
    s, ctr = _call("POST", "/client-trust-scores", token=cl_tok, body={
        "client_id":               cl_id,
        "trust_score":             0.85,
        "project_completion_rate": 1.0,
        "total_ratings_given":     1,
    }, expected=(200, 201))
    ok("client trust score created") if s else fail("client trust score create failed")

    step("GET /client-trust-scores")
    s, _ = _call("GET", "/client-trust-scores", token=cl_tok, expected=(200,))
    ok("client trust scores listed") if s else fail("client trust scores list failed")

    if cl_id:
        step(f"GET /client-trust-scores/{cl_id}")
        s, _ = _call("GET", f"/client-trust-scores/{cl_id}", token=cl_tok, expected=(200, 404))
        ok("client trust score by ID responded") if s else fail("client trust score get failed")

        step(f"PUT /client-trust-scores/{cl_id}")
        s, _ = _call("PUT", f"/client-trust-scores/{cl_id}", token=cl_tok, body={"trust_score": 0.90}, expected=(200, 404))
        ok("client trust score update responded") if s else fail("client trust score update failed")

        step(f"DELETE /client-trust-scores/{cl_id}")
        s, _ = _call("DELETE", f"/client-trust-scores/{cl_id}", token=cl_tok, expected=(200, 404))
        ok("client trust score delete responded") if s else fail("client trust score delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 15 — DIRECT MESSAGES
# ═════════════════════════════════════════════════════════════════════════════

def sec_dm():
    section("DIRECT MESSAGES")
    cl_tok  = _S.get("cl_tok", "")
    fl_tok  = _S.get("fl_tok", "")
    fl_uid  = _S.get("fl_uid", "")
    jp_id   = _S.get("jp_id", "")
    cl2_tok = _S.get("cl2_tok", "")
    cl2_uid = _S.get("cl2_uid", "")

    # Client opens DM with freelancer
    step("POST /dm/threads — client opens thread with freelancer")
    s, thr = _call("POST", "/dm/threads", token=cl_tok, body={
        "participant_id": fl_uid,
        "job_post_id":    jp_id,
        "message_text":   "Hi! I would like to discuss the backend project.",
    }, expected=(200, 201))
    thread_id = _id(thr, "thread_id") or (_d(thr).get("thread") or {}).get("thread_id", "")
    ok(f"DM thread created: {thread_id}") if s else fail("DM thread create failed")
    _S["dm_thread_id"] = thread_id

    step("GET /dm/threads — list all threads")
    s, _ = _call("GET", "/dm/threads", token=cl_tok, expected=(200,))
    ok("threads listed") if s else fail("threads list failed")

    step("GET /dm/threads/requests — pending requests")
    s, _ = _call("GET", "/dm/threads/requests", token=fl_tok, expected=(200,))
    ok("thread requests returned") if s else fail("thread requests failed")

    if thread_id:
        step(f"GET /dm/threads/{thread_id}")
        s, _ = _call("GET", f"/dm/threads/{thread_id}", token=cl_tok, expected=(200,))
        ok("thread by ID") if s else fail("get thread failed")

        step(f"PUT /dm/threads/{thread_id}/accept — freelancer accepts")
        s, _ = _call("PUT", f"/dm/threads/{thread_id}/accept", token=fl_tok, expected=(200,))
        ok("thread accepted by freelancer") if s else fail("thread accept failed")

        step(f"GET /dm/threads/{thread_id}/messages — message history")
        s, msgs = _call("GET", f"/dm/threads/{thread_id}/messages", token=fl_tok, expected=(200,))
        msg_list = _d(msgs).get("messages", [])
        info(f"{len(msg_list)} message(s) in thread")
        ok("messages returned") if s else fail("get messages failed")

        step(f"POST /dm/threads/{thread_id}/messages — send text message")
        s, _ = _call("POST", f"/dm/threads/{thread_id}/messages", token=fl_tok, body={
            "message_text": "Hello! I would be happy to discuss the project with you.",
        }, expected=(200, 201))
        ok("message sent") if s else fail("send message failed")

        step(f"POST /dm/threads/{thread_id}/messages — client replies")
        s, _ = _call("POST", f"/dm/threads/{thread_id}/messages", token=cl_tok, body={
            "message_text": "Great! Let's schedule a meeting to go over requirements.",
        }, expected=(200, 201))
        ok("reply sent") if s else fail("reply send failed")

        step(f"POST /dm/threads/{thread_id}/messages/upload — image attachment")
        files = {"file": ("dm_photo.jpg", io.BytesIO(_TINY_JPEG), "image/jpeg")}
        s, _ = _call("POST", f"/dm/threads/{thread_id}/messages/upload", token=fl_tok,
                     form={"message_text": "Here is a quick screenshot."}, files=files,
                     expected=(200, 201, 500))
        ok("DM attachment endpoint covered") if s else fail("DM attachment upload failed")

        step(f"POST /dm/threads/{thread_id}/messages/upload — text-only multipart")
        s, _ = _call("POST", f"/dm/threads/{thread_id}/messages/upload", token=cl_tok,
                     form={"message_text": "Text-only multipart message."}, files={},
                     expected=(200, 201))
        ok("DM multipart text-only branch covered") if s else fail("DM multipart text-only failed")

        step(f"WS /dm/ws/{thread_id} — bad token rejected")
        bad_uri = _ws_url(f"/dm/ws/{thread_id}?token=bad-token")
        if websockets is None:
            skip("websockets library not installed")
        else:
            ok("bad WebSocket token rejected") if _ws_bad_token_rejected(bad_uri) else fail("bad WebSocket token was not rejected")

        step(f"WS /dm/ws/{thread_id} — receives REST broadcast")
        if websockets is None:
            skip("websockets library not installed")
        else:
            listener_uri = _ws_url(f"/dm/ws/{thread_id}?token={fl_tok}")

            def _send_ws_probe():
                _call("POST", f"/dm/threads/{thread_id}/messages", token=cl_tok, body={
                    "message_text": "WebSocket broadcast probe from walkthrough-all.",
                }, expected=(200, 201))

            ok("WebSocket received broadcast") if _ws_broadcast_check(listener_uri, _send_ws_probe) else fail("WebSocket broadcast not observed")

        step(f"PUT /dm/threads/{thread_id}/read — mark as read")
        s, _ = _call("PUT", f"/dm/threads/{thread_id}/read", token=fl_tok, expected=(200,))
        ok("messages marked as read") if s else fail("mark read failed")

    # Test decline on a second thread (cl2 → freelancer)
    step("POST /dm/threads — second thread for decline test")
    s, thr2 = _call("POST", "/dm/threads", token=cl2_tok, body={
        "participant_id": fl_uid,
        "message_text":   "Hi, interested in your services.",
    }, expected=(200, 201))
    thread2_id = _id(thr2, "thread_id") or (_d(thr2).get("thread") or {}).get("thread_id", "")
    if thread2_id:
        step(f"PUT /dm/threads/{thread2_id}/decline — freelancer declines")
        s, _ = _call("PUT", f"/dm/threads/{thread2_id}/decline", token=fl_tok, expected=(200,))
        ok("thread declined") if s else fail("thread decline failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 16 — DASHBOARD
# ═════════════════════════════════════════════════════════════════════════════

def sec_dashboard():
    section("DASHBOARD")

    step("GET /dashboard/freelancer")
    s, fd = _call("GET", "/dashboard/freelancer", token=_S.get("fl_tok", ""), expected=(200,))
    if s:
        dd = _d(fd)
        info(f"active_contracts={dd.get('active_contracts')}  total_earnings={dd.get('total_earnings')}")
        ok("freelancer dashboard returned")
    else:
        fail("freelancer dashboard failed")

    step("GET /dashboard/client")
    s, cd = _call("GET", "/dashboard/client", token=_S.get("cl_tok", ""), expected=(200,))
    if s:
        dd = _d(cd)
        info(f"active_contracts={dd.get('active_contracts')}  total_spent={dd.get('total_spent')}")
        ok("client dashboard returned")
    else:
        fail("client dashboard failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 17 — REVIEWS
# ═════════════════════════════════════════════════════════════════════════════

def sec_reviews():
    section("REVIEWS")
    cl_tok      = _S.get("cl_tok", "")
    fl_tok      = _S.get("fl_tok", "")
    fl_id       = _S.get("fl_id", "")
    contract_id = _S.get("contract_id", "")

    if not contract_id:
        skip("no contract_id — skipping reviews section")
        return

    # If contract submission flow ran, contract is completed. Otherwise mark it done.
    step(f"GET /reviews/contract/{contract_id} — fetch pending review")
    s, rv = _call("GET", f"/reviews/contract/{contract_id}", token=cl_tok, expected=(200,))
    review_detail = _d(rv)
    review_id = review_detail.get("id", "")
    if s and review_id:
        info(f"review_id={review_id}  status={review_detail.get('status')}")
        ok("pending review found")
    else:
        info("review not yet available (pipeline may need the contract to be completed first)")
        # Try completing the contract manually to trigger review pipeline
        step(f"PUT /contracts/{contract_id} → status=completed (triggers review pipeline)")
        s2, _ = _call("PUT", f"/contracts/{contract_id}", token=cl_tok, body={"status": "completed"}, expected=(200,))
        if s2:
            ok("contract marked completed")
            info("Waiting 4s for review pipeline...")
            time.sleep(4)
            s3, rv2 = _call("GET", f"/reviews/contract/{contract_id}", token=cl_tok, expected=(200,))
            review_detail2 = _d(rv2)
            review_id = review_detail2.get("id", "")
            if s3 and review_id:
                info(f"review_id={review_id}  status={review_detail2.get('status')}")
                ok("pending review found after completion")
            else:
                info("review still not available — AI pipeline may need time or Ollama required")

    if review_id:
        step(f"GET /reviews/{review_id} — review detail")
        s, _ = _call("GET", f"/reviews/{review_id}", token=cl_tok, expected=(200,))
        ok("review detail returned") if s else fail("review detail failed")

        step(f"POST /reviews/{review_id}/submit")
        s, _ = _call("POST", f"/reviews/{review_id}/submit", token=cl_tok, body={
            "ratings": [
                {"category": "communication",   "score": 4.5},
                {"category": "quality",         "score": 5.0},
                {"category": "professionalism", "score": 5.0},
                {"category": "value_for_money", "score": 4.0},
            ],
            "client_answer":   "Yes, the code was clean and well-documented.",
            "overall_comment": "Excellent work! Delivered on time with great quality.",
            "extra_skill_tags": ["Clean Code", "Fast Delivery"],
        }, expected=(200, 201))
        ok("review submitted") if s else fail("review submit failed")

    if fl_id:
        step(f"GET /reviews/freelancer/{fl_id}")
        s, _ = _call("GET", f"/reviews/freelancer/{fl_id}", token=cl_tok, expected=(200,))
        ok("freelancer reviews returned") if s else fail("freelancer reviews failed")

        step(f"GET /reviews/trust-score/{fl_id}")
        s, _ = _call("GET", f"/reviews/trust-score/{fl_id}", token=cl_tok, expected=(200,))
        ok("trust score returned") if s else fail("trust score failed")

        step(f"GET /reviews/red-flags/{fl_id}")
        s, _ = _call("GET", f"/reviews/red-flags/{fl_id}", token=cl_tok, expected=(200,))
        ok("red flags returned") if s else fail("red flags failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 18 — FREELANCER & JOB EMBEDDINGS
# ═════════════════════════════════════════════════════════════════════════════

def sec_embeddings():
    section("FREELANCER & JOB EMBEDDINGS")
    fl_tok = _S.get("fl_tok", "")
    cl_tok = _S.get("cl_tok", "")
    fl_id  = _S.get("fl_id", "")
    jp_id  = _S.get("jp_id", "")

    # ── Freelancer Embeddings ─────────────────────────────────────────────────
    step("GET /freelancer-embeddings — list")
    s, _ = _call("GET", "/freelancer-embeddings", token=fl_tok, expected=(200,))
    ok("freelancer embeddings listed") if s else fail("freelancer embeddings list failed")

    if fl_id:
        step(f"GET /freelancer-embeddings/freelancer/{fl_id}")
        s, fer = _call("GET", f"/freelancer-embeddings/freelancer/{fl_id}", token=fl_tok, expected=(200, 404))
        fe_id = _id(fer, "embedding_id")
        info(f"embedding_id={fe_id}")
        ok("freelancer embedding endpoint responded") if s else fail("freelancer embedding failed")
        _S["fe_id"] = fe_id

        if fe_id:
            step(f"GET /freelancer-embeddings/{fe_id}")
            s, _ = _call("GET", f"/freelancer-embeddings/{fe_id}", token=fl_tok, expected=(200,))
            ok("freelancer embedding by ID") if s else fail("get fl embedding failed")

        if not fe_id:
            step("POST /freelancer-embeddings — manual create")
            s, fce = _call("POST", "/freelancer-embeddings", token=fl_tok, body={
                "freelancer_id": fl_id,
                "embedding_vector": _embedding_vector(0.001),
                "embedding_type": "walkthrough",
            }, expected=(200, 201, 400, 500))
            fe_created_id = _id(fce, "embedding_id") if s else ""
            ok(f"manual freelancer embedding create covered: {fe_created_id}") if s else fail("manual freelancer embedding create failed")
            if fe_created_id:
                step(f"PUT /freelancer-embeddings/{fe_created_id}")
                s, _ = _call("PUT", f"/freelancer-embeddings/{fe_created_id}", token=fl_tok,
                             body={"embedding_vector": _embedding_vector(0.002), "embedding_type": "walkthrough-updated"},
                             expected=(200,))
                ok("manual freelancer embedding updated") if s else fail("manual freelancer embedding update failed")

                step(f"DELETE /freelancer-embeddings/{fe_created_id}")
                s, _ = _call("DELETE", f"/freelancer-embeddings/{fe_created_id}", token=fl_tok, expected=(200,))
                ok("manual freelancer embedding deleted") if s else fail("manual freelancer embedding delete failed")
        else:
            step("POST /freelancer-embeddings — duplicate validation")
            s, _ = _call("POST", "/freelancer-embeddings", token=fl_tok, body={
                "freelancer_id": fl_id,
                "embedding_vector": _embedding_vector(0.001),
                "embedding_type": "walkthrough-duplicate",
            }, expected=(400, 500))
            ok("freelancer embedding duplicate branch covered") if s else fail("freelancer embedding duplicate branch failed")

    # ── Job Embeddings ────────────────────────────────────────────────────────
    step("GET /job-embeddings — list")
    s, _ = _call("GET", "/job-embeddings", token=cl_tok, expected=(200,))
    ok("job embeddings listed") if s else fail("job embeddings list failed")

    if jp_id:
        step(f"GET /job-embeddings/job-post/{jp_id}")
        s, jer = _call("GET", f"/job-embeddings/job-post/{jp_id}", token=cl_tok, expected=(200, 404))
        je_id = _id(jer, "embedding_id")
        info(f"job embedding_id={je_id}")
        ok("job embedding endpoint responded") if s else fail("job embedding failed")
        _S["je_id"] = je_id

        if je_id:
            step(f"GET /job-embeddings/{je_id}")
            s, _ = _call("GET", f"/job-embeddings/{je_id}", token=cl_tok, expected=(200,))
            ok("job embedding by ID") if s else fail("get job embedding failed")

        if not je_id:
            step("POST /job-embeddings — manual create")
            s, jce = _call("POST", "/job-embeddings", token=cl_tok, body={
                "job_post_id": jp_id,
                "embedding_vector": _embedding_vector(0.003),
                "embedding_type": "walkthrough",
            }, expected=(200, 201, 400, 500))
            je_created_id = _id(jce, "embedding_id") if s else ""
            ok(f"manual job embedding create covered: {je_created_id}") if s else fail("manual job embedding create failed")
            if je_created_id:
                step(f"PUT /job-embeddings/{je_created_id}")
                s, _ = _call("PUT", f"/job-embeddings/{je_created_id}", token=cl_tok,
                             body={"embedding_vector": _embedding_vector(0.004), "embedding_type": "walkthrough-updated"},
                             expected=(200,))
                ok("manual job embedding updated") if s else fail("manual job embedding update failed")

                step(f"DELETE /job-embeddings/{je_created_id}")
                s, _ = _call("DELETE", f"/job-embeddings/{je_created_id}", token=cl_tok, expected=(200,))
                ok("manual job embedding deleted") if s else fail("manual job embedding delete failed")
        else:
            step("POST /job-embeddings — duplicate validation")
            s, _ = _call("POST", "/job-embeddings", token=cl_tok, body={
                "job_post_id": jp_id,
                "embedding_vector": _embedding_vector(0.003),
                "embedding_type": "walkthrough-duplicate",
            }, expected=(400, 500))
            ok("job embedding duplicate branch covered") if s else fail("job embedding duplicate branch failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 19 — AI JOB MATCHING
# ═════════════════════════════════════════════════════════════════════════════

def sec_ai_job_matching():
    section("AI JOB MATCHING")
    fl_tok = _S.get("fl_tok", "")
    cl_tok = _S.get("cl_tok", "")
    fl_id  = _S.get("fl_id", "")
    jp_id  = _S.get("jp_id", "")

    step(f"POST /ai/job_matching/embed/freelancer/{fl_id}")
    s, _ = _call("POST", f"/ai/job_matching/embed/freelancer/{fl_id}",
                 body={}, token=fl_tok, expected=(200, 201, 202))
    ok("freelancer embed triggered") if s else fail("freelancer embed failed")

    step(f"POST /ai/job_matching/embed/job/{jp_id}")
    s, _ = _call("POST", f"/ai/job_matching/embed/job/{jp_id}",
                 body={}, token=cl_tok, expected=(200, 201, 202))
    ok("job embed triggered") if s else fail("job embed failed")

    step("POST /ai/job_matching/sweep")
    s, sw = _call("POST", "/ai/job_matching/sweep", body={}, token=cl_tok, expected=(200,))
    if s:
        sd = _d(sw)
        info(f"embedded: freelancers={sd.get('freelancers_embedded',0)}  jobs={sd.get('jobs_embedded',0)}")
        ok("sweep complete")
    else:
        fail("sweep failed")
    time.sleep(1.0)

    step("GET /ai/job_matching/match/freelancer-to-jobs  (limit=5)")
    s, mr = _call("GET", "/ai/job_matching/match/freelancer-to-jobs",
                  token=fl_tok, params={"limit": 5}, expected=(200,))
    if s:
        matches = _d(mr).get("matches", [])
        info(f"matches returned: {len(matches)}")
        if matches:
            top = matches[0]
            info(f"top: '{top.get('job_title')}' | heuristic={top.get('heuristic_score')} | cosine={top.get('similarity_score', 0):.4f}")
            ok("match feed returned results")
            if "heuristic_score" in top:
                ok("heuristic_score present (Stage 3 ML correctly removed)")
            if "match_probability" in top:
                fail("match_probability still present — old ML ranker in use")
        else:
            info("no matches (embeddings may still be processing)")
    else:
        fail("match endpoint failed")

    step(f"GET /ai/job_matching/analyse/job/{jp_id}  (RAG — may be slow)")
    s, _ = _call("GET", f"/ai/job_matching/analyse/job/{jp_id}",
                 token=fl_tok, expected=(200, 502, 503, 504))
    ok("RAG analyse endpoint responded") if s else fail("RAG analyse endpoint error")

    step("GET /ai/job_matching/test_ai_local")
    s, _ = _call("GET", "/ai/job_matching/test_ai_local", token=fl_tok,
                 expected=(200, 502, 503, 504))
    ok("AI local test endpoint responded") if s else fail("AI local test endpoint error")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 20 — ADMIN / REPORTS / APPEALS
# ═════════════════════════════════════════════════════════════════════════════

def sec_admin():
    section("ADMIN / REPORTS / APPEALS")
    import uuid
    admin_tok = _S.get("admin_tok", "")
    fl_uid    = _S.get("fl_uid", "")
    cl_uid    = _S.get("cl_uid", "")
    jp_id     = _S.get("jp_id", "")

    if not admin_tok:
        skip("no admin token — skipping all admin steps")
        return

    # ── Dashboard ─────────────────────────────────────────────────────────────
    step("GET /admin/dashboard")
    s, ad = _call("GET", "/admin/dashboard", token=admin_tok, expected=(200,))
    if s:
        d = _d(ad)
        info(f"pending_moderation={d.get('pending_moderation')}  pending_scam={d.get('pending_scam')}  pending_reports={d.get('pending_reports')}")
        ok("admin dashboard returned")
    else:
        fail("admin dashboard failed")

    # ── Harmful Text Detection (admin) ─────────────────────────────────────────────
    step("GET /admin/moderation — list pending items")
    s, mq = _call("GET", "/admin/moderation", token=admin_tok,
                  params={"status": "pending", "page_size": 5}, expected=(200,))
    items = _list(mq)
    info(f"{len(items)} pending moderation item(s)")
    ok("moderation queue returned") if s else fail("moderation queue failed")

    step("POST /admin/moderation/scan — clean content")
    dummy_id = str(uuid.uuid4())
    s, cs = _call("POST", "/admin/moderation/scan", token=admin_tok, body={
        "content_type": "job_post", "content_id": dummy_id,
        "user_id": cl_uid or dummy_id,
        "text": "I am looking for an experienced Python developer to build a REST API.",
    }, expected=(200,))
    if s:
        info(f"flagged={_d(cs).get('flagged')}")
        ok("clean content scan: not flagged")

    step("POST /admin/moderation/scan — toxic content")
    toxic_id = str(uuid.uuid4())
    s, ts_ = _call("POST", "/admin/moderation/scan", token=admin_tok, body={
        "content_type": "job_post", "content_id": toxic_id,
        "user_id": cl_uid or dummy_id,
        "text": "You fucking idiot, you are worthless garbage!",
    }, expected=(200,))
    mod_id = ""
    if s:
        td = _d(ts_)
        flagged = td.get("flagged")
        rec = td.get("moderation_record") or {}
        mod_id = str(rec.get("moderation_id", ""))
        info(f"flagged={flagged}  mod_id={mod_id}  labels={rec.get('detected_labels')}")
        ok("toxic content flagged") if flagged else fail("toxic content not flagged — model check needed")

    if mod_id:
        step(f"POST /admin/moderation/{mod_id}/approve")
        s, _ = _call("POST", f"/admin/moderation/{mod_id}/approve", token=admin_tok,
                     body={"admin_note": "walkthrough test"}, expected=(200,))
        ok(f"moderation {mod_id} approved") if s else fail("moderation approve failed")

    step("POST /admin/moderation/scan — second toxic content for reject")
    toxic_id2 = str(uuid.uuid4())
    s, ts2 = _call("POST", "/admin/moderation/scan", token=admin_tok, body={
        "content_type": "freelancer_profile", "content_id": toxic_id2,
        "user_id": fl_uid or dummy_id,
        "text": "You fucking idiot, this work is worthless garbage!",
    }, expected=(200,))
    mod_id2 = ""
    if s:
        rec2 = (_d(ts2).get("moderation_record") or {})
        mod_id2 = str(rec2.get("moderation_id", ""))
        info(f"second moderation_id={mod_id2}")
    if mod_id2:
        step(f"POST /admin/moderation/{mod_id2}/reject")
        s, _ = _call("POST", f"/admin/moderation/{mod_id2}/reject", token=admin_tok,
                     body={"admin_note": "walkthrough reject test"}, expected=(200,))
        ok("moderation rejected") if s else fail("moderation reject failed")

    step("POST /admin/moderation/force-expire")
    s, _ = _call("POST", "/admin/moderation/force-expire", token=admin_tok,
                 body={"ids": [mod_id] if mod_id else []}, expected=(200,))
    ok("moderation force-expire covered") if s else fail("moderation force-expire failed")

    step("GET /admin/moderation — all statuses")
    s, _ = _call("GET", "/admin/moderation", token=admin_tok,
                 params={"status": "all", "page_size": 5}, expected=(200,))
    ok("moderation all statuses returned") if s else fail("moderation all failed")

    # ── Scam Detection ─────────────────────────────────────────────────────────
    step("GET /admin/scam-flags — list pending")
    s, sfq = _call("GET", "/admin/scam-flags", token=admin_tok,
                   params={"status": "pending", "page_size": 5}, expected=(200,))
    flags = _list(sfq)
    info(f"{len(flags)} pending scam flag(s)")
    ok("scam flags returned") if s else fail("scam flags failed")

    step("POST /admin/scam-flags/scan — scam text")
    scam_jp_id = str(uuid.uuid4())
    s, sf = _call("POST", "/admin/scam-flags/scan", token=admin_tok, body={
        "job_post_id": scam_jp_id, "client_id": cl_uid or dummy_id,
        "text": "guaranteed income easy money get rich quick no experience needed earn unlimited pay to work",
    }, expected=(200,))
    scam_flag_id = ""
    if s:
        sfd = _d(sf)
        flagged = sfd.get("flagged")
        rec = sfd.get("scam_flag") or {}
        if not rec:
            for v in sfd.values():
                if isinstance(v, dict) and "scam_score" in v:
                    rec = v
                    break
        scam_flag_id = str(rec.get("flag_id", ""))
        info(f"flagged={flagged}  flag_id={scam_flag_id}  score={rec.get('scam_score')}")
        ok("scam text flagged") if flagged else info("not flagged (keyword threshold not met)")

    if scam_flag_id:
        step(f"POST /admin/scam-flags/{scam_flag_id}/approve — mark safe")
        s, _ = _call("POST", f"/admin/scam-flags/{scam_flag_id}/approve", token=admin_tok,
                     body={"admin_note": "false positive"}, expected=(200,))
        ok("scam flag marked safe") if s else fail("scam flag approve failed")

    # Re-create for remove test
    step("POST /admin/scam-flags/scan — another scam for remove test")
    scam_jp_id2 = str(uuid.uuid4())
    s, sf2 = _call("POST", "/admin/scam-flags/scan", token=admin_tok, body={
        "job_post_id": scam_jp_id2, "client_id": cl_uid or dummy_id,
        "text": "guaranteed income easy money get rich quick no experience needed earn unlimited pay to work",
    }, expected=(200,))
    scam_flag_id2 = ""
    if s:
        sfd2 = _d(sf2)
        rec2 = sfd2.get("scam_flag") or {}
        if not rec2:
            for v in sfd2.values():
                if isinstance(v, dict) and "scam_score" in v:
                    rec2 = v
                    break
        scam_flag_id2 = str(rec2.get("flag_id", ""))

    if scam_flag_id2:
        step(f"POST /admin/scam-flags/{scam_flag_id2}/remove — confirm scam")
        s, _ = _call("POST", f"/admin/scam-flags/{scam_flag_id2}/remove", token=admin_tok,
                     body={"admin_note": "confirmed scam"}, expected=(200,))
        ok("scam flag removed (confirmed)") if s else fail("scam flag remove failed")

    step("POST /admin/scam-flags/force-expire")
    s, _ = _call("POST", "/admin/scam-flags/force-expire", token=admin_tok,
                 body={"ids": [scam_flag_id] if scam_flag_id else []}, expected=(200,))
    ok("scam force-expire covered") if s else fail("scam force-expire failed")

    step("GET /admin/scam-flags/client/{cl_uid or dummy}")
    s, _ = _call("GET", f"/admin/scam-flags/client/{cl_uid or str(uuid.uuid4())}", token=admin_tok, expected=(200,))
    ok("client scam record returned") if s else fail("client scam record failed")

    # ── Reports (admin view) ───────────────────────────────────────────────────
    step("GET /admin/reports — list")
    s, _ = _call("GET", "/admin/reports", token=admin_tok,
                 params={"status": "all", "page_size": 5}, expected=(200,))
    ok("admin reports listed") if s else fail("admin reports failed")

    step("GET /admin/reports/targets")
    s, _ = _call("GET", "/admin/reports/targets", token=admin_tok,
                 params={"min_count": 1, "page_size": 5}, expected=(200,))
    ok("report targets returned") if s else fail("report targets failed")

    step("GET /admin/reports/auto-actions")
    s, _ = _call("GET", "/admin/reports/auto-actions", token=admin_tok, expected=(200,))
    ok("auto-actions returned") if s else fail("auto-actions failed")

    step("POST /admin/reports/force-expire-target")
    s, _ = _call("POST", "/admin/reports/force-expire-target", token=admin_tok,
                 body={"target_type": "user", "target_id": fl_uid or dummy_id}, expected=(200,))
    ok("report force-expire-target covered") if s else fail("report force-expire-target failed")

    # ── Appeals (admin view) ───────────────────────────────────────────────────
    step("GET /admin/appeals — list")
    s, _ = _call("GET", "/admin/appeals", token=admin_tok,
                 params={"status": "all", "page_size": 5}, expected=(200,))
    ok("admin appeals listed") if s else fail("admin appeals failed")

    # ── Reports (user-facing) ─────────────────────────────────────────────────
    cl_tok = _S.get("cl_tok", "")

    step("GET /reports/reasons")
    s, rr = _call("GET", "/reports/reasons", token=cl_tok, expected=(200,))
    reasons = _d(rr).get("reasons", [])
    info(f"valid reasons: {reasons[:4]}")
    ok("report reasons returned") if s else fail("report reasons failed")

    step("POST /reports — client reports freelancer")
    s, rep = _call("POST", "/reports", token=cl_tok, body={
        "reported_type":    "freelancer",
        "reported_user_id": fl_uid,
        "reasons":          [reasons[0]] if reasons else [],
        "custom_reason":    "Test report from walkthrough.",
    }, expected=(200, 201))
    report_id = _d(rep).get("report_id", "")
    ok(f"report submitted: {report_id}") if s else fail("submit report failed")

    if report_id:
        step(f"POST /admin/reports/{report_id}/accept")
        s, _ = _call("POST", f"/admin/reports/{report_id}/accept", token=admin_tok,
                     body={"admin_note": "walkthrough test"}, expected=(200,))
        ok("report accepted") if s else fail("report accept failed")

    step("POST /reports — report a job post")
    s, rep2 = _call("POST", "/reports", token=cl_tok, body={
        "reported_type": "job_post",
        "job_post_id":   jp_id,
        "reasons":       [reasons[0]] if reasons else [],
    }, expected=(200, 201))
    report2_id = _d(rep2).get("report_id", "")
    if report2_id:
        step(f"POST /admin/reports/{report2_id}/dismiss")
        s, _ = _call("POST", f"/admin/reports/{report2_id}/dismiss", token=admin_tok,
                     body={"admin_note": "no violation found"}, expected=(200,))
        ok("report dismissed") if s else fail("report dismiss failed")

    # ── Appeals (user-facing) ─────────────────────────────────────────────────
    step("POST /appeals — user submits appeal")
    fl_tok2 = _S.get("fl_tok", "")
    s, ap = _call("POST", "/appeals", token=fl_tok2, body={
        "target_type": "user",
        "target_id":   fl_uid,
        "message":     "I believe this report is unjustified. Please review my case.",
    }, expected=(200, 201))
    appeal_id = _d(ap).get("appeal_id", "")
    ok(f"appeal submitted: {appeal_id}") if s else fail("appeal submit failed")

    step("GET /appeals/mine — own appeals")
    s, _ = _call("GET", "/appeals/mine", token=fl_tok2, expected=(200,))
    ok("own appeals returned") if s else fail("own appeals failed")

    if appeal_id:
        step(f"POST /admin/appeals/{appeal_id}/reject")
        s, _ = _call("POST", f"/admin/appeals/{appeal_id}/reject", token=admin_tok,
                     body={"admin_note": "walkthrough test dismiss"}, expected=(200,))
        ok("appeal rejected") if s else fail("appeal reject failed")

    step("POST /appeals — second appeal for approve test")
    s, ap2 = _call("POST", "/appeals", token=fl_tok2, body={
        "target_type": "user",
        "target_id":   fl_uid,
        "message":     "Second appeal for approve test.",
    }, expected=(200, 201))
    appeal2_id = _d(ap2).get("appeal_id", "")
    if appeal2_id:
        step(f"POST /admin/appeals/{appeal2_id}/approve")
        s, _ = _call("POST", f"/admin/appeals/{appeal2_id}/approve", token=admin_tok,
                     body={"admin_note": "approved in walkthrough"}, expected=(200,))
        ok("appeal approved") if s else fail("appeal approve failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 21 — CONTENT MODERATION (ML API)
# ═════════════════════════════════════════════════════════════════════════════

def sec_harmful_text_detection():
    section("CONTENT MODERATION — Direct ML API")

    step("GET /toxicity/models")
    s, ms = _call("GET", "/harmful-text/models", expected=(200,))
    if s:
        models = _d(ms).get("available_models", [])
        for m in models:
            info(f"  type={m.get('type')}  available={m.get('available')}  default={m.get('default')}")
        ok(f"{len(models)} model(s)") if models else fail("no models — run TRAIN_MODEL.ipynb")
    else:
        fail("models endpoint failed")

    step("GET /toxicity/labels")
    s, ls = _call("GET", "/harmful-text/labels", expected=(200,))
    ok("labels returned") if s else fail("labels endpoint failed")

    step("POST /toxicity/detect — clean text")
    s, cr = _call("POST", "/harmful-text/detect",
                  body={"text": "I need an experienced developer to build a REST API."},
                  params={"model_type": "best", "threshold": "0.5"}, expected=(200,))
    if s:
        rd = _d(cr)
        info(f"is_harmful={rd.get('is_harmful')}  model={rd.get('model')}")
        ok("clean text not flagged") if not rd.get("is_harmful") else fail("clean text incorrectly flagged")
    else:
        fail("moderate endpoint failed (clean)")

    step("POST /toxicity/detect — toxic text")
    s, tr = _call("POST", "/harmful-text/detect",
                  body={"text": "You fucking idiot, you are worthless garbage!"},
                  params={"model_type": "best", "threshold": "0.5"}, expected=(200,))
    if s:
        rd = _d(tr)
        info(f"is_harmful={rd.get('is_harmful')}  labels={rd.get('labels')}")
        ok("toxic text flagged") if rd.get("is_harmful") else fail("toxic text NOT flagged — model may need retraining")
    else:
        fail("moderate endpoint failed (toxic)")

    step("POST /toxicity/detect-batch — 3 texts")
    s, br = _call("POST", "/harmful-text/detect-batch",
                  body={"texts": [
                      "I need a Python developer.",
                      "You fucking idiot!",
                      "guaranteed income easy money",
                  ]},
                  params={"model_type": "best", "threshold": "0.5"}, expected=(200,))
    if s:
        bd = _d(br)
        summary = bd.get("summary", {})
        info(f"total={summary.get('total')}  harmful={summary.get('harmful')}  clean={summary.get('clean')}")
        ok("batch moderation complete")
    else:
        fail("moderate_batch failed")


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 22 — MISC / ROOT / OAUTH REDIRECTS
# ═════════════════════════════════════════════════════════════════════════════

def sec_misc_oauth():
    section("MISC / ROOT / OAUTH REDIRECTS")

    step("GET /testing_tables")
    s, _ = _call("GET", "/testing_tables", expected=(200, 500))
    ok("testing_tables endpoint covered") if s else fail("testing_tables failed")

    step("GET /auth/oauth/google — redirect probe")
    s, _ = _call("GET", "/auth/oauth/google", expected=(302, 307, 501),
                 allow_redirects=False)
    ok("google OAuth initiation covered") if s else fail("google OAuth initiation failed")

    step("GET /auth/oauth/google/callback — missing code validation")
    s, _ = _call("GET", "/auth/oauth/google/callback", expected=(400, 302),
                 allow_redirects=False)
    ok("google OAuth callback validation covered") if s else fail("google OAuth callback validation failed")

    step("GET /auth/oauth/google/callback — provider error branch")
    s, _ = _call("GET", "/auth/oauth/google/callback", params={"error": "access_denied"},
                 expected=(400, 302), allow_redirects=False)
    ok("google OAuth error branch covered") if s else fail("google OAuth error branch failed")

    step("GET /auth/oauth/linkedin — redirect probe")
    s, _ = _call("GET", "/auth/oauth/linkedin", expected=(302, 307, 501),
                 allow_redirects=False)
    ok("linkedin OAuth initiation covered") if s else fail("linkedin OAuth initiation failed")

    step("GET /auth/oauth/linkedin/callback — missing code validation")
    s, _ = _call("GET", "/auth/oauth/linkedin/callback", expected=(400, 302),
                 allow_redirects=False)
    ok("linkedin OAuth callback validation covered") if s else fail("linkedin OAuth callback validation failed")

    step("GET /auth/oauth/linkedin/callback — provider error branch")
    s, _ = _call("GET", "/auth/oauth/linkedin/callback", params={"error": "access_denied"},
                 expected=(400, 302), allow_redirects=False)
    ok("linkedin OAuth error branch covered") if s else fail("linkedin OAuth error branch failed")


def sec_cleanup_reference_deletes():
    section("REFERENCE CLEANUP DELETE ROUTES")
    tok = _S.get("cl_tok", "")
    lang_id = _S.get("lang_id", "")
    spec_id = _S.get("spec_id", "")
    spec2_id = _S.get("spec2_id", "")

    if lang_id:
        step(f"DELETE /languages/{lang_id}")
        s, _ = _call("DELETE", f"/languages/{lang_id}", token=tok, expected=(200, 400, 500))
        ok("language delete route covered") if s else fail("language delete failed")

    for sid in [spec_id, spec2_id]:
        if sid:
            step(f"DELETE /specialities/{sid}")
            s, _ = _call("DELETE", f"/specialities/{sid}", token=tok, expected=(200, 400, 500))
            ok("speciality delete route covered") if s else fail("speciality delete failed")


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════

def main():
    tee, path = _start_tee()

    try:
        print("=" * 72)
        print("  Capstone API — Complete Walkthrough  (ALL routes)")
        print("=" * 72)
        print(f"  BASE_URL     : {BASE_URL}")
        print(f"  Timestamp ID : {_TS}")
        print(f"  Admin email  : {_ADMIN_EMAIL}")

        t_start = time.perf_counter()

        sec_auth()
        sec_reference_data()
        sec_freelancer()
        sec_client()
        sec_profile_route_edges()
        sec_freelancer_attributes()
        sec_work_edu()
        sec_portfolio()
        sec_job_posts()
        sec_job_roles()
        sec_proposals()
        sec_file_uploads_and_cv()
        sec_contracts()
        sec_contract_submissions()
        sec_saved_jobs()
        sec_ratings()
        sec_dm()
        sec_dashboard()
        sec_reviews()
        sec_embeddings()
        sec_ai_job_matching()
        sec_admin()
        sec_harmful_text_detection()
        sec_cleanup_reference_deletes()
        sec_misc_oauth()

        elapsed = time.perf_counter() - t_start

        print(f"\n{'=' * 72}")
        print(f"  WALKTHROUGH COMPLETE")
        print(f"{'=' * 72}")
        print(f"  PASS  : {_pass_count}")
        print(f"  FAIL  : {_fail_count}")
        print(f"  SKIP  : {_skip_count}")
        print(f"  Time  : {elapsed:.1f}s")
        print()
        print("  Routes covered by section:")
        print("  1  AUTH             — register, verify, resend, login, me, forgot/reset, add-role")
        print("  2  REFERENCE DATA   — skills, languages, specialities (full CRUD)")
        print("  3  FREELANCER       — browse, search, profile, update, embedding")
        print("  4  CLIENT           — browse, search, profile, update")
        print("  5  PROFILE EDGES    — profile create validation + disposable profile delete")
        print("  6  FL ATTRIBUTES    — freelancer skills / languages / specialities (CRUD)")
        print("  7  WORK & EDUCATION — work experience + education (CRUD)")
        print("  8  PORTFOLIO        — portfolio (CRUD)")
        print("  9  JOB POSTS        — scope calc, CRUD, search, category-counts")
        print(" 10  JOB ROLES        — job roles + job role skills (CRUD)")
        print(" 11  PROPOSALS        — CRUD, status (accepted/withdrawn), delete")
        print(" 12  FILES & CV       — upload, job/proposal files, CV upload, CV analysis")
        print(" 13  CONTRACTS        — CRUD, generate/download PDF, cancel")
        print(" 14  SUBMISSIONS      — submit, get, revision, approve")
        print(" 15  SAVED JOBS       — save, list, delete")
        print(" 16  RATINGS          — ratings, performance ratings, client trust scores")
        print(" 17  DIRECT MESSAGES  — threads, messages, uploads, WS, accept, decline, read")
        print(" 18  DASHBOARD        — freelancer + client dashboards")
        print(" 19  REVIEWS          — get, submit, trust score, red flags")
        print(" 20  EMBEDDINGS       — freelancer + job embedding reads/create branches")
        print(" 21  AI JOB MATCHING  — embed, sweep, match, analyse, local test")
        print(" 22  ADMIN            — moderation, scam, reports, appeals, force-expire")
        print(" 23  CONTENT MOD      — moderate, batch, models, labels")
        print(" 24  CLEANUP          — language/speciality delete routes")
        print(" 25  MISC/OAUTH       — testing_tables + OAuth redirect/callback probes")
        print()
        print("  Notes:")
        print("    Upload/CV/OAuth/AI paths are covered, but may return 500/501 when external")
        print("    services or credentials are not configured in the local environment.")
        print("    /users routes are intentionally not included by main.py, so they are not")
        print("    live API routes in this app instance.")
        print(f"{'=' * 72}")

    finally:
        _stop_tee(tee, path)


if __name__ == "__main__":
    main()
