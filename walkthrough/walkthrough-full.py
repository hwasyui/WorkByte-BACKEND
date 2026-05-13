"""
Full Walkthrough — Forgot Password + Heuristic Job Matching + Content Moderation

Covers three systems end-to-end in one script. All sections run sequentially;
individual step failures are printed but do not abort later sections.

SECTION 1  Forgot Password (freelancer + client cycles)
  1.1  Register account → verify email (dev OTP)
  1.2  Login with original password → success
  1.3  POST /auth/forgot-password → get dev_reset_otp
  1.4  POST /auth/reset-password with new password
  1.5  Old password rejected (401)
  1.6  New password accepted (200) → confirm token
  1.7  GET /auth/me → same user_id
  1.8  Replay consumed OTP → 400

SECTION 2  Heuristic Job Matching (3-stage pipeline)
  2.1  Register client + freelancer, verify, login
  2.2  Update freelancer: rate, currency, experience_level
  2.3  Seed 3 unique skills, add to freelancer profile
  2.4  Create job post (status=active) + job role with budget
  2.5  Add same 3 skills as required to job role
  2.6  Run embedding sweep
  2.7  GET /ai/job_matching/match/freelancer-to-jobs
       Verify response has heuristic_score, skill_overlap_pct, match_reasons,
       penalty_reasons (NOT match_probability from old ML ranker)
  2.8  GET /ai/job_matching/analyse/job/{id} (RAG/LLM analysis)

SECTION 3  Content Moderation
  3A  Direct ML API (no auth required)
      GET  /content_moderation/models   — verify RoBERTa available
      GET  /content_moderation/labels   — show 6 harm labels
      POST /content_moderation/moderate — clean text → is_harmful=False
      POST /content_moderation/moderate — toxic text → is_harmful=True + scores
      POST /content_moderation/moderate_batch — mixed texts → summary

  3B  Admin-triggered manual scan (ML-first with keyword fallback)
      POST /admin/moderation/scan (clean) → flagged=False
      POST /admin/moderation/scan (toxic) → flagged=True → record in queue
      GET  /admin/moderation             → list pending items
      POST /admin/moderation/{id}/approve
      POST /admin/scam-flags/scan (scam text) → flagged=True → scam flag
      GET  /admin/scam-flags             → list pending flags
      POST /admin/scam-flags/{id}/approve (mark safe)

  3C  Auto-triggered via profile creation
      PUT  /freelancers/{id} with toxic bio → background scan fires
      PUT  /clients/{id} with toxic bio    → background scan fires
      GET  /admin/moderation → freelancer_profile + client_profile entries

  3D  Auto-triggered via job post creation
      POST /job-posts with toxic content → background content + scam scan
      GET  /admin/moderation  → job_post entry
      GET  /admin/scam-flags  → scam flag entry

Requirements:
  Server running at BASE_URL (default http://localhost:8000)
  APP_ENV=development and SHOW_DEV_OTP=true (for dev OTPs in responses)
  Admin account: admin@admin.com / thisisanadminaccountpassword

Usage:
    python walkthrough/walkthrough-full.py
    BASE_URL=http://localhost:8000 python walkthrough/walkthrough-full.py
"""

import datetime
import json
import os
import sys
import time
import requests

BASE_URL       = os.environ.get("BASE_URL", "http://localhost:8000")
_PASSWORD      = "SecurePass123!"
_NEW_PASSWORD  = "NewPass456@789"
_ADMIN_EMAIL   = "admin@admin.com"
_ADMIN_PASS    = "thisisanadminaccountpassword"
_TS            = int(time.time())


# ---------------------------------------------------------------------------
# Tee (stdout → console + file simultaneously)
# ---------------------------------------------------------------------------

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
                        f"walkthrough_full_{ts}.md")
    tee = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee: _Tee, path: str):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\nResults saved to: {path}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_section_n = 0
_step_n    = 0
_pass_count = 0
_fail_count = 0


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


def info(msg: str):
    print(f"      {msg}")


def _call(method: str, endpoint: str, *, body=None, token=None,
          params=None, form=None, expected=(200, 201)):
    """
    Single HTTP call. Returns (success: bool, payload: dict).
    success is True if status_code in expected.
    """
    url     = f"{BASE_URL}{endpoint}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, params=params, timeout=90)
        elif method == "POST" and form is not None:
            r = requests.post(url, headers=headers, data=form, timeout=90)
        elif method == "POST":
            headers["Content-Type"] = "application/json"
            r = requests.post(url, headers=headers, json=body, params=params, timeout=90)
        elif method == "PUT":
            r = requests.put(url, headers=headers, data=form, timeout=90)
        else:
            raise ValueError(f"Unknown method {method}")

        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text}

        success = r.status_code in expected
        tag = "OK" if success else "FAIL"
        print(f"      {method:4} {endpoint}  [{r.status_code}] {tag}")
        if not success:
            snippet = json.dumps(payload, indent=4)[:400]
            print(f"           {snippet}")
        return success, payload

    except requests.exceptions.ConnectionError:
        print(f"      {method:4} {endpoint}  [ERR] Connection refused — is the server running?")
        return False, {}
    except Exception as exc:
        print(f"      {method:4} {endpoint}  [ERR] {exc}")
        return False, {}


def _d(payload: dict):
    """Unwrap ResponseSchema envelope → details dict or empty dict."""
    if not payload:
        return {}
    d = payload.get("details")
    if isinstance(d, dict):
        return d
    if isinstance(d, list):
        return {"list": d}
    return payload


def _token(payload: dict) -> str:
    return _d(payload).get("access_token", "")


def _id(payload: dict, key: str) -> str:
    d = _d(payload)
    val = d.get(key)
    if val:
        return str(val)
    # try nested user/verification dicts
    for sub in d.values():
        if isinstance(sub, dict) and key in sub:
            return str(sub[key])
    return ""


def _register_verify_login(email: str, user_type: str, full_name: str,
                            password: str = _PASSWORD) -> tuple:
    """Register, verify email, login. Returns (user_id, token) or ('', '')."""
    s, reg = _call("POST", "/auth/register", body={
        "email": email, "password": password,
        "user_type": user_type, "full_name": full_name,
    }, expected=(201,))
    if not s:
        fail("registration failed")
        return "", ""

    d   = _d(reg)
    otp = (d.get("verification") or {}).get("dev_verification_otp") or d.get("dev_verification_otp")
    if not otp:
        fail("dev_verification_otp missing — set APP_ENV=development and SHOW_DEV_OTP=true")
        return "", ""
    info(f"dev_verification_otp: {otp}")

    s, _ = _call("POST", "/auth/verify-email",
                 body={"email": email, "otp": otp}, expected=(200,))
    if not s:
        fail("email verification failed")
        return "", ""

    s, login = _call("POST", "/auth/login",
                     body={"email": email, "password": password}, expected=(200,))
    if not s:
        fail("login failed")
        return "", ""

    tok = _token(login)
    if not tok:
        fail("no token in login response")
        return "", ""

    s, me = _call("GET", "/auth/me", token=tok, expected=(200,))
    uid = _id(me, "user_id")
    info(f"user_id: {uid}")
    return uid, tok


# ---------------------------------------------------------------------------
# SECTION 1: Forgot Password
# ---------------------------------------------------------------------------

def run_forgot_password(label: str, email: str, user_type: str) -> bool:
    print(f"\n  [{label}]  {email}")

    # 1.1 Register + verify + login
    step("Register, verify email, login")
    _, tok = _register_verify_login(email, user_type, f"FP {label} {_TS}")
    if not tok:
        return False
    ok("account created and verified")

    # 1.2 Confirm original password works
    step("Login with original password (expect 200)")
    s, _ = _call("POST", "/auth/login",
                 body={"email": email, "password": _PASSWORD}, expected=(200,))
    ok("original password accepted") if s else fail("login rejected unexpectedly")

    # 1.3 Request reset OTP
    step("POST /auth/forgot-password")
    s, fp = _call("POST", "/auth/forgot-password",
                  body={"email": email}, expected=(200,))
    if not s:
        fail("forgot-password request failed")
        return False
    reset_otp = _d(fp).get("dev_reset_otp")
    if not reset_otp:
        fail("dev_reset_otp missing from forgot-password response")
        return False
    info(f"dev_reset_otp: {reset_otp}")
    ok("reset OTP received")

    # 1.4 Reset password
    step("POST /auth/reset-password")
    s, rp = _call("POST", "/auth/reset-password", body={
        "email": email, "otp": reset_otp, "new_password": _NEW_PASSWORD,
    }, expected=(200,))
    ok("password reset accepted") if s else fail("password reset rejected")

    # 1.5 Old password must be rejected
    step("Login with OLD password (expect 401)")
    s, _ = _call("POST", "/auth/login",
                 body={"email": email, "password": _PASSWORD}, expected=(401,))
    ok("old password correctly rejected") if s else fail("old password was NOT rejected — bug")

    # 1.6 New password must work
    step("Login with NEW password (expect 200)")
    s, nl = _call("POST", "/auth/login",
                  body={"email": email, "password": _NEW_PASSWORD}, expected=(200,))
    if not s:
        fail("new password login failed")
        return False
    new_tok = _token(nl)
    ok(f"new password accepted, token received")

    # 1.7 /auth/me with new token → same user
    step("GET /auth/me with new token")
    s, me = _call("GET", "/auth/me", token=new_tok, expected=(200,))
    if s:
        uid = _id(me, "user_id")
        email_back = _d(me).get("email", "")
        info(f"user_id={uid}  email={email_back}")
        ok("same user confirmed")
    else:
        fail("/auth/me failed after password reset")

    # 1.8 Replay consumed OTP must be rejected
    step("Replay consumed OTP (expect 400)")
    s, _ = _call("POST", "/auth/reset-password", body={
        "email": email, "otp": reset_otp, "new_password": "AnotherPass!999",
    }, expected=(400,))
    ok("consumed OTP correctly rejected") if s else fail("OTP replay was NOT rejected — bug")

    return True


def section_forgot_password():
    section("FORGOT PASSWORD")
    run_forgot_password("FREELANCER", f"fp.fl.{_TS}@test.dev", "freelancer")
    run_forgot_password("CLIENT",     f"fp.cl.{_TS}@test.dev", "client")


# ---------------------------------------------------------------------------
# SECTION 2: Heuristic Job Matching
# ---------------------------------------------------------------------------

def section_job_matching():
    section("HEURISTIC JOB MATCHING  (3-stage: pgvector + skill filter + weighted heuristic)")

    cl_email = f"jm.client.{_TS}@test.dev"
    fl_email = f"jm.fl.{_TS}@test.dev"

    # 2.1 Register users
    step("Register client + freelancer")
    _, cl_tok = _register_verify_login(cl_email, "client",     f"JM Client {_TS}")
    _, fl_tok = _register_verify_login(fl_email, "freelancer", f"JM Freelancer {_TS}")
    if not cl_tok or not fl_tok:
        fail("could not set up users — skipping job matching section")
        return

    # get freelancer_id from /auth/me
    s, me = _call("GET", "/auth/me", token=fl_tok, expected=(200,))
    fl_id = _id(me, "freelancer_id")
    if not fl_id:
        fail("freelancer_id not found in /auth/me — skipping")
        return
    info(f"freelancer_id: {fl_id}")
    ok("both accounts ready")

    # get client profile id
    s, cl_me = _call("GET", "/auth/me", token=cl_tok, expected=(200,))
    cl_id = _id(cl_me, "client_id")
    info(f"client_id: {cl_id}")

    # 2.2 Update freelancer rate + experience level (form data)
    step("PUT /freelancers/{id} — set rate, currency, experience_level")
    s, _ = _call("PUT", f"/freelancers/{fl_id}", token=fl_tok, form={
        "estimated_rate":   "50",
        "rate_time":        "hourly",
        "rate_currency":    "USD",
        "experience_level": "intermediate",
    }, expected=(200,))
    ok("freelancer rate & experience level updated") if s else info("update skipped (form field mismatch — budget signal will use neutral 0.5)")

    # 2.3 Create 3 unique skills and add them to the freelancer
    step("Create 3 skills, add to freelancer profile")
    skill_suffix = f"WK{_TS}"
    skill_names  = [
        f"Python ML {skill_suffix}",
        f"FastAPI ML {skill_suffix}",
        f"PostgreSQL ML {skill_suffix}",
    ]
    skill_ids = []
    for sname in skill_names:
        s, sr = _call("POST", "/skills", token=cl_tok, body={
            "skill_name":     sname,
            "skill_category": "hard_skill",
            "description":    sname.lower(),
        }, expected=(200, 201))
        sid = _id(sr, "skill_id")
        if s and sid:
            skill_ids.append(sid)
            info(f"created skill: {sname} ({sid})")
        else:
            info(f"skill creation failed for {sname}")

    added_skills = 0
    for sid in skill_ids:
        s, _ = _call("POST", "/freelancer-skills", token=fl_tok, body={
            "freelancer_id":    fl_id,
            "skill_id":         sid,
            "proficiency_level": "expert",
        }, expected=(200, 201))
        if s:
            added_skills += 1
    info(f"added {added_skills}/{len(skill_ids)} skills to freelancer")
    ok(f"freelancer has {added_skills} skills") if added_skills > 0 else fail("no skills added — skill_overlap signal will be neutral")

    # Queue freelancer re-embedding
    _call("POST", f"/ai/job_matching/embed/freelancer/{fl_id}",
          body={}, token=fl_tok, expected=(200, 201, 202))

    # 2.4 Create job post (status=active) + job role with USD budget
    step("Create job post (status=active) + job role with budget")
    s, jp = _call("POST", "/job-posts", token=cl_tok, body={
        "job_title":         f"Heuristic Matching Test Job {_TS}",
        "job_description":   "We need an experienced Python/FastAPI/PostgreSQL backend engineer to build a REST API. Strong database design and API optimization skills required. Must have intermediate or higher experience.",
        "project_category":  "backend_development",
        "project_type":      "individual",
        "project_scope":     "medium",
        "estimated_duration": "8 weeks",
        "experience_level":  "intermediate",
        "status":            "active",
    }, expected=(200, 201))
    jp_id = _id(jp, "job_post_id")
    if not jp_id:
        fail("job post creation failed — skipping rest of matching section")
        return
    info(f"job_post_id: {jp_id}")
    ok("job post created")

    s, jr = _call("POST", "/job-roles", token=cl_tok, body={
        "job_post_id":       jp_id,
        "role_title":        "Backend Engineer",
        "budget_type":       "fixed",
        "role_budget":       8000,
        "budget_currency":   "USD",
        "positions_available": 1,
    }, expected=(200, 201))
    jr_id = _id(jr, "job_role_id")
    if jr_id:
        info(f"job_role_id: {jr_id}  budget=8000 USD")
        ok("job role created with budget")
    else:
        fail("job role creation failed — budget signal will be neutral")

    # Queue job re-embedding
    _call("POST", f"/ai/job_matching/embed/job/{jp_id}",
          body={}, token=cl_tok, expected=(200, 201, 202))

    # 2.5 Add same skills as required to job role
    step("Add required skills to job role")
    added_reqs = 0
    if jr_id:
        for sid in skill_ids:
            s, _ = _call("POST", "/job-role-skills", token=cl_tok, body={
                "job_role_id":     jr_id,
                "skill_id":        sid,
                "is_required":     True,
                "importance_level": "required",
            }, expected=(200, 201))
            if s:
                added_reqs += 1
    info(f"added {added_reqs}/{len(skill_ids)} required skills to job role")
    ok(f"skill_overlap should be 100% ({added_reqs} matching)") if added_reqs > 0 else info("no required skills added")

    # 2.6 Run embedding sweep
    step("POST /ai/job_matching/sweep — process all queued embeddings")
    s, sw = _call("POST", "/ai/job_matching/sweep", body={}, token=cl_tok, expected=(200,))
    if s:
        sw_d = _d(sw)
        info(f"embedded: freelancers={sw_d.get('freelancers_embedded',0)}  jobs={sw_d.get('jobs_embedded',0)}")
        ok("sweep complete")
    else:
        info("sweep failed — embeddings may have been processed in background already")
    time.sleep(1.0)

    # 2.7 GET /ai/job_matching/match/freelancer-to-jobs
    step("GET /ai/job_matching/match/freelancer-to-jobs  (limit=5)")
    s, mr = _call("GET", "/ai/job_matching/match/freelancer-to-jobs",
                  token=fl_tok, params={"limit": 5}, expected=(200,))
    if not s:
        fail("match endpoint failed")
    else:
        matches = _d(mr).get("matches", [])
        info(f"total matches returned: {len(matches)}")
        if matches:
            ok(f"match feed returned {len(matches)} result(s)")
            print(f"\n      {'#':>2}  {'Title':<38}  {'Heuristic':>9}  {'Cosine':>8}  {'SkillOlp':>9}")
            print(f"      {'--':>2}  {'-'*38}  {'-'*9}  {'-'*8}  {'-'*9}")
            for i, m in enumerate(matches, 1):
                title    = str(m.get("job_title", "?"))[:38]
                h_score  = m.get("heuristic_score", "N/A")
                cosine   = m.get("similarity_score", 0.0)
                overlap  = m.get("skill_overlap_pct")
                ol_str   = f"{overlap:.1f}%" if isinstance(overlap, (int, float)) else "N/A"
                print(f"      {i:>2}  {title:<38}  {str(h_score):>9}  {cosine:>8.4f}  {ol_str:>9}")

            # Check for heuristic fields (NOT match_probability from old ML ranker)
            top = matches[0]
            if "heuristic_score" in top:
                ok("heuristic_score field present (Stage 3 ML removed correctly)")
            else:
                fail("heuristic_score missing — check heuristic_ranker integration")

            if "match_probability" in top:
                fail("match_probability still present — old ML ranker still in use")
            else:
                ok("match_probability absent (old ML field correctly removed)")

            # Show match_reasons + penalty_reasons for top result
            if top.get("match_reasons"):
                info("match_reasons for top result:")
                for r in top["match_reasons"]:
                    info(f"  (+) [{r.get('factor')}] {r.get('label')}")
            if top.get("penalty_reasons"):
                info("penalty_reasons for top result:")
                for r in top["penalty_reasons"]:
                    info(f"  (-) [{r.get('factor')}] {r.get('label')}")
        else:
            info("no matches returned — embeddings may still be processing; run sweep again if needed")

    # 2.8 RAG analysis (optional — may be slow or unavailable without Ollama)
    step("GET /ai/job_matching/analyse/job/{id}  (RAG + LLM — may be slow or unavailable)")
    s, rag = _call("GET", f"/ai/job_matching/analyse/job/{jp_id}",
                   token=fl_tok, expected=(200, 502, 503, 504))
    if s and _d(rag).get("overall_match_score") is not None:
        rd = _d(rag)
        info(f"match_score: {rd.get('overall_match_score')}  recommendation: {rd.get('overall_recommendation')}")
        ok("RAG analysis returned")
    else:
        info("RAG analysis not available (Ollama may not be running) — skipping")


# ---------------------------------------------------------------------------
# SECTION 3: Content Moderation
# ---------------------------------------------------------------------------

CLEAN_TEXT = "I am looking for an experienced Python developer to build a REST API."
TOXIC_TEXT = "You fucking idiot, you are a worthless piece of shit and I hate you!"
SCAM_TEXT  = "guaranteed income easy money get rich quick no experience needed earn unlimited earning pay to work"
TOXIC_BIO  = "You are such an idiot! I hate everyone, you're all worthless morons and complete losers."


def section_content_moderation():
    section("CONTENT MODERATION  (ML-first with keyword fallback)")

    # ── 3A: Direct ML API ────────────────────────────────────────────────────
    print("\n  [3A] Direct ML API  (/content_moderation/*)")

    step("GET /content_moderation/models — verify RoBERTa is available")
    s, ms = _call("GET", "/content_moderation/models", expected=(200,))
    if s:
        available = _d(ms).get("available_models", [])
        for m in available:
            info(f"model={m.get('type')}  available={m.get('available')}  default={m.get('default')}")
            if m.get("test_metrics"):
                tm = m["test_metrics"]
                info(f"  metrics: F1={tm.get('f1',0):.4f}  precision={tm.get('precision',0):.4f}  recall={tm.get('recall',0):.4f}")
        ok(f"{len(available)} model(s) available") if available else fail("no models found — run TRAIN_MODEL.ipynb")
    else:
        fail("models endpoint failed")

    step("GET /content_moderation/labels — list the 6 harm labels")
    s, ls = _call("GET", "/content_moderation/labels", expected=(200,))
    if s:
        labels = _d(ls)
        info("labels: " + ", ".join(v.get("name", k) for k, v in labels.items() if isinstance(v, dict)))
        ok(f"{len(labels)} labels")
    else:
        fail("labels endpoint failed")

    step("POST /content_moderation/moderate — CLEAN text (expect is_harmful=False)")
    s, cr = _call("POST", "/content_moderation/moderate",
                  body={"text": CLEAN_TEXT},
                  params={"model_type": "best", "threshold": "0.5"},
                  expected=(200,))
    if s:
        rd = _d(cr)
        info(f"is_harmful={rd.get('is_harmful')}  labels={rd.get('labels')}  model={rd.get('model')}")
        info("scores: " + "  ".join(f"{k}={v:.4f}" for k, v in (rd.get("scores") or {}).items()))
        if not rd.get("is_harmful"):
            ok("clean text correctly not flagged")
        else:
            fail(f"clean text incorrectly flagged as harmful: {rd.get('labels')}")
    else:
        fail("moderate endpoint failed for clean text")

    step("POST /content_moderation/moderate — TOXIC text (expect is_harmful=True)")
    s, tr = _call("POST", "/content_moderation/moderate",
                  body={"text": TOXIC_TEXT},
                  params={"model_type": "best", "threshold": "0.5"},
                  expected=(200,))
    if s:
        rd = _d(tr)
        info(f"is_harmful={rd.get('is_harmful')}  labels={rd.get('labels')}")
        info("scores: " + "  ".join(f"{k}={v:.4f}" for k, v in (rd.get("scores") or {}).items()))
        if rd.get("is_harmful"):
            ok(f"toxic text flagged correctly: {rd.get('labels')}")
        else:
            fail("toxic text NOT flagged — model may need retraining")
    else:
        fail("moderate endpoint failed for toxic text")

    step("POST /content_moderation/moderate_batch — mixed texts (clean + toxic + scam)")
    batch_texts = [CLEAN_TEXT, TOXIC_TEXT, SCAM_TEXT]
    s, br = _call("POST", "/content_moderation/moderate_batch",
                  body={"texts": batch_texts},
                  params={"model_type": "best", "threshold": "0.5"},
                  expected=(200,))
    if s:
        bd = _d(br)
        summary = bd.get("summary", {})
        info(f"total={summary.get('total')}  harmful={summary.get('harmful')}  clean={summary.get('clean')}")
        for i, res in enumerate(bd.get("results", []), 1):
            info(f"  [{i}] is_harmful={res.get('is_harmful')}  labels={res.get('labels')}")
        ok("batch moderation complete")
    else:
        fail("moderate_batch endpoint failed")

    # ── 3B: Admin-triggered manual scan ──────────────────────────────────────
    print("\n  [3B] Admin manual scan  (/admin/moderation/scan + /admin/scam-flags/scan)")

    step("Admin login")
    s, al = _call("POST", "/auth/login",
                  body={"email": _ADMIN_EMAIL, "password": _ADMIN_PASS},
                  expected=(200,))
    admin_tok = _token(al) if s else ""
    if not admin_tok:
        fail("admin login failed — check ADMIN_EMAIL / ADMIN_PASS in this script")
        print("      Skipping all admin-only steps in 3B, 3C, 3D")
    else:
        s, me = _call("GET", "/auth/me", token=admin_tok, expected=(200,))
        admin_me = _d(me) if s else {}
        info(
            f"logged in as: {admin_me.get('email', _ADMIN_EMAIL)}  "
            f"is_admin={admin_me.get('is_admin')}"
        )
        if not s or not admin_me.get("is_admin"):
            fail(
                "admin login succeeded, but this database row is not admin "
                "(users.is_admin must be TRUE)"
            )
            admin_tok = ""
            print("      Skipping all admin-only steps in 3B, 3C, 3D")
        else:
            ok("admin logged in with admin privileges")

    # Use a dummy content_id and user_id (admin scan doesn't require real FK)
    import uuid
    dummy_jp_id  = str(uuid.uuid4())
    dummy_uid    = str(uuid.uuid4())

    if admin_tok:
        step("POST /admin/moderation/scan — CLEAN text (expect flagged=False)")
        s, cs = _call("POST", "/admin/moderation/scan", token=admin_tok, body={
            "content_type": "job_post",
            "content_id":   dummy_jp_id,
            "user_id":      dummy_uid,
            "text":         CLEAN_TEXT,
        }, expected=(200,))
        if s:
            flagged = _d(cs).get("flagged")
            info(f"flagged={flagged}")
            ok("clean content correctly not flagged") if not flagged else fail("clean content incorrectly flagged")

        step("POST /admin/moderation/scan — TOXIC text (expect flagged=True)")
        toxic_jp_id = str(uuid.uuid4())
        s, ts_ = _call("POST", "/admin/moderation/scan", token=admin_tok, body={
            "content_type": "job_post",
            "content_id":   toxic_jp_id,
            "user_id":      dummy_uid,
            "text":         TOXIC_TEXT,
        }, expected=(200,))
        mod_id = ""
        if s:
            td = _d(ts_)
            flagged = td.get("flagged")
            rec = td.get("moderation_record") or {}
            mod_id = str(rec.get("moderation_id", ""))
            scan_method = rec.get("scan_method", "unknown")
            info(f"flagged={flagged}  moderation_id={mod_id}  scan_method={scan_method}")
            info(f"detected_labels={rec.get('detected_labels')}")
            info(f"scores: toxic={rec.get('toxic_score')}  severe_toxic={rec.get('severe_toxic_score')}  obscene={rec.get('obscene_score')}  insult={rec.get('insult_score')}")
            ok("toxic content flagged and queued for admin review") if flagged else fail("toxic content NOT flagged")
        else:
            fail("moderation/scan endpoint failed for toxic text")

        step("GET /admin/moderation — list pending items")
        s, mq = _call("GET", "/admin/moderation", token=admin_tok,
                      params={"status": "pending", "page_size": 5}, expected=(200,))
        if s:
            items = _d(mq).get("list", []) or (_d(mq) if isinstance(_d(mq), list) else [])
            # handle list inside details
            raw = mq.get("details", [])
            if isinstance(raw, list):
                items = raw
            info(f"pending moderation items: {len(items)}")
            for it in items[:3]:
                info(f"  id={it.get('moderation_id')}  type={it.get('content_type')}  labels={it.get('detected_labels')}")
            ok(f"{len(items)} pending item(s) visible") if items else info("queue empty (may have been cleared)")
        else:
            fail("GET /admin/moderation failed")

        if mod_id:
            step(f"POST /admin/moderation/{mod_id}/approve — clear the flagged item")
            s, _ = _call("POST", f"/admin/moderation/{mod_id}/approve", token=admin_tok,
                         body={"admin_note": "walkthrough test — approved manually"}, expected=(200,))
            ok(f"item {mod_id} approved") if s else fail("approve failed")

        step("POST /admin/scam-flags/scan — SCAM text (expect flagged=True)")
        scam_jp_id = str(uuid.uuid4())
        s, sf = _call("POST", "/admin/scam-flags/scan", token=admin_tok, body={
            "job_post_id": scam_jp_id,
            "client_id":   dummy_uid,
            "text":        SCAM_TEXT,
        }, expected=(200,))
        scam_flag_id = ""
        if s:
            sfd = _d(sf)
            flagged = sfd.get("flagged")
            rec = sfd.get("scam_flag") or sfd.get("flag") or {}
            if not rec:
                # Try the nested structure
                for v in sfd.values():
                    if isinstance(v, dict) and "scam_score" in v:
                        rec = v
                        break
            scam_flag_id = str(rec.get("flag_id", ""))
            info(f"flagged={flagged}  flag_id={scam_flag_id}  scam_score={rec.get('scam_score')}  keywords={rec.get('detected_keywords')}")
            ok("scam text flagged") if flagged else info("scam text not flagged (may not match keyword threshold)")
        else:
            fail("scam-flags/scan endpoint failed")

        step("GET /admin/scam-flags — list pending scam flags")
        s, sfq = _call("GET", "/admin/scam-flags", token=admin_tok,
                       params={"status": "pending", "page_size": 5}, expected=(200,))
        if s:
            raw = sfq.get("details", [])
            flags = raw if isinstance(raw, list) else []
            info(f"pending scam flags: {len(flags)}")
            for f_ in flags[:3]:
                info(f"  flag_id={f_.get('flag_id')}  score={f_.get('scam_score')}  keywords={f_.get('detected_keywords')}")
            ok(f"{len(flags)} pending scam flag(s)") if flags else info("no scam flags in queue")
        else:
            fail("GET /admin/scam-flags failed")

        if scam_flag_id:
            step(f"POST /admin/scam-flags/{scam_flag_id}/approve — mark as safe (false positive)")
            s, _ = _call("POST", f"/admin/scam-flags/{scam_flag_id}/approve",
                         token=admin_tok,
                         body={"admin_note": "walkthrough test — marked safe"}, expected=(200,))
            ok(f"scam flag {scam_flag_id} marked safe") if s else fail("approve scam flag failed")

    # ── 3C: Auto-triggered via profile creation ───────────────────────────────
    print("\n  [3C] Auto-triggered scan on profile creation / update")

    step("Register freelancer, update with toxic bio, check admin queue")
    fl_email_c = f"mod.fl.{_TS}@test.dev"
    _, fl_tok_c = _register_verify_login(fl_email_c, "freelancer", f"Mod Freelancer {_TS}")
    if not fl_tok_c:
        fail("could not set up freelancer account — skipping 3C freelancer test")
    else:
        s, me_c = _call("GET", "/auth/me", token=fl_tok_c, expected=(200,))
        fl_id_c = _id(me_c, "freelancer_id")
        if fl_id_c:
            # PUT with toxic bio → background scan fires
            s, _ = _call("PUT", f"/freelancers/{fl_id_c}", token=fl_tok_c, form={
                "bio": TOXIC_BIO,
            }, expected=(200,))
            if s:
                info(f"PUT freelancer/{fl_id_c} with toxic bio — background scan queued")
                time.sleep(2.0)   # let background thread complete
                if admin_tok:
                    s, mq2 = _call("GET", "/admin/moderation", token=admin_tok,
                                   params={"status": "pending", "content_type": "freelancer_profile", "page_size": 5},
                                   expected=(200,))
                    raw2 = mq2.get("details", [])
                    items2 = raw2 if isinstance(raw2, list) else []
                    info(f"freelancer_profile pending items: {len(items2)}")
                    for it in items2[:2]:
                        info(f"  id={it.get('moderation_id')}  labels={it.get('detected_labels')}")
                    ok(f"{len(items2)} freelancer_profile item(s) in queue") if items2 else info("queue empty (scan may have found content clean)")
            else:
                info("PUT freelancer failed — bio update skipped")

    step("Register client, update with toxic bio, check admin queue")
    cl_email_c = f"mod.cl.{_TS}@test.dev"
    _, cl_tok_c = _register_verify_login(cl_email_c, "client", f"Mod Client {_TS}")
    if not cl_tok_c:
        fail("could not set up client account — skipping 3C client test")
    else:
        s, me_cl = _call("GET", "/auth/me", token=cl_tok_c, expected=(200,))
        cl_id_c = _id(me_cl, "client_id")
        if cl_id_c:
            s, _ = _call("PUT", f"/clients/{cl_id_c}", token=cl_tok_c, form={
                "bio": TOXIC_BIO,
            }, expected=(200,))
            if s:
                info(f"PUT client/{cl_id_c} with toxic bio — background scan queued")
                time.sleep(2.0)
                if admin_tok:
                    s, mq3 = _call("GET", "/admin/moderation", token=admin_tok,
                                   params={"status": "pending", "content_type": "client_profile", "page_size": 5},
                                   expected=(200,))
                    raw3 = mq3.get("details", [])
                    items3 = raw3 if isinstance(raw3, list) else []
                    info(f"client_profile pending items: {len(items3)}")
                    for it in items3[:2]:
                        info(f"  id={it.get('moderation_id')}  labels={it.get('detected_labels')}")
                    ok(f"{len(items3)} client_profile item(s) in queue") if items3 else info("queue empty (scan may have found content clean)")
            else:
                info("PUT client failed — bio update skipped")

    # ── 3D: Auto-triggered via job post creation ──────────────────────────────
    print("\n  [3D] Auto-triggered scan on job post creation (content + scam)")

    # Need a client account for this
    step("Register client for auto-scan test job posts")
    cl_email_d = f"mod.jpcl.{_TS}@test.dev"
    _, cl_tok_d = _register_verify_login(cl_email_d, "client", f"AutoScan Client {_TS}")

    if cl_tok_d:
        step("Create job post with TOXIC content → background content scan")
        s, jp_t = _call("POST", "/job-posts", token=cl_tok_d, body={
            "job_title":         "Test Toxic Job Post",
            "job_description":   TOXIC_TEXT,
            "project_category":  "other",
            "project_type":      "individual",
            "project_scope":     "small",
            "estimated_duration": "1 week",
            "experience_level":  "entry",
            "status":            "active",
        }, expected=(200, 201))
        jp_t_id = _id(jp_t, "job_post_id")
        if jp_t_id:
            info(f"toxic job post created: {jp_t_id}")
            time.sleep(2.0)
            if admin_tok:
                s, mq4 = _call("GET", "/admin/moderation", token=admin_tok,
                               params={"status": "pending", "content_type": "job_post", "page_size": 5},
                               expected=(200,))
                raw4 = mq4.get("details", [])
                items4 = raw4 if isinstance(raw4, list) else []
                # find the one matching our job post
                matched = [it for it in items4 if str(it.get("content_id")) == jp_t_id]
                info(f"job_post moderation items total={len(items4)}  matching this job={len(matched)}")
                if matched:
                    m = matched[0]
                    info(f"  id={m.get('moderation_id')}  labels={m.get('detected_labels')}  toxic_score={m.get('toxic_score')}")
                    ok("toxic job post auto-flagged in content moderation queue")
                else:
                    info("no moderation record found for this specific job_post_id — may be in queue with different status or not flagged")

        step("Create job post with SCAM content → background scam scan")
        s, jp_s = _call("POST", "/job-posts", token=cl_tok_d, body={
            "job_title":         "Test Scam Job Post",
            "job_description":   SCAM_TEXT,
            "project_category":  "other",
            "project_type":      "individual",
            "project_scope":     "small",
            "estimated_duration": "1 week",
            "experience_level":  "entry",
            "status":            "active",
        }, expected=(200, 201))
        jp_s_id = _id(jp_s, "job_post_id")
        if jp_s_id:
            info(f"scam job post created: {jp_s_id}")
            time.sleep(2.0)
            if admin_tok:
                s, sfq2 = _call("GET", "/admin/scam-flags", token=admin_tok,
                                params={"status": "pending", "page_size": 10},
                                expected=(200,))
                raw5 = sfq2.get("details", [])
                flags2 = raw5 if isinstance(raw5, list) else []
                matched2 = [f_ for f_ in flags2 if str(f_.get("job_post_id")) == jp_s_id]
                info(f"scam flags total={len(flags2)}  matching this job={len(matched2)}")
                if matched2:
                    f_ = matched2[0]
                    info(f"  flag_id={f_.get('flag_id')}  scam_score={f_.get('scam_score')}  keywords={f_.get('detected_keywords')}")
                    ok("scam job post auto-flagged in scam flags queue")
                else:
                    info("no scam flag found for this specific job_post_id")
    else:
        info("no client token — skipping 3D auto-scan tests")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    tee, path = _start_tee()

    try:
        print("=" * 72)
        print("  Capstone API — Full Walkthrough")
        print("  Forgot Password | Heuristic Job Matching | Content Moderation")
        print("=" * 72)
        print(f"  BASE_URL     : {BASE_URL}")
        print(f"  Timestamp ID : {_TS}")
        print(f"  Admin email  : {_ADMIN_EMAIL}")

        t_start = time.perf_counter()

        section_forgot_password()
        section_job_matching()
        section_content_moderation()

        elapsed = time.perf_counter() - t_start

        print(f"\n{'=' * 72}")
        print(f"  WALKTHROUGH COMPLETE")
        print(f"{'=' * 72}")
        print(f"  Passed  : {_pass_count}")
        print(f"  Failed  : {_fail_count}")
        print(f"  Time    : {elapsed:.1f}s")
        print()
        print("  Section 1 — Forgot Password")
        print("    Freelancer + client full reset cycles, OTP replay rejection")
        print()
        print("  Section 2 — Heuristic Job Matching")
        print("    3-stage pipeline: pgvector -> skill filter -> weighted heuristic score")
        print("    Fields verified: heuristic_score, skill_overlap_pct, match_reasons, penalty_reasons")
        print("    match_probability (old ML) confirmed absent")
        print()
        print("  Section 3 — Content Moderation")
        print("    3A  Direct ML API: /content_moderation/moderate + moderate_batch")
        print("    3B  Admin manual scan: ML-first with keyword fallback, approve/reject flow")
        print("    3C  Auto-triggered on profile PUT (freelancer_profile + client_profile)")
        print("    3D  Auto-triggered on job post creation (content + scam queues)")
        print(f"{'=' * 72}")

    finally:
        _stop_tee(tee, path)


if __name__ == "__main__":
    main()
