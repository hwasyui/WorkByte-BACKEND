"""
Job Scam Detection Walkthrough — SBERT + Random Forest ML model.

Two scenarios tested end-to-end:

  Scenario 1 — CLEAN JOB
    1a. Client posts a legitimate Backend Developer job
    1b. Background ML scan runs (asyncio task)
    1c. Job status stays 'active'  — no scam flag created

  Scenario 2 — SCAM JOB
    2a. Client posts a suspicious work-from-home job
    2b. Background ML scan runs (asyncio task)
    2c. Job is automatically closed (status → 'closed')
    2d. Scam flag created in admin queue
    2e. Admin views flag — sees scam_score, detected keywords, client info
    2f. Admin confirms scam → removes flag → client gets a strike
    2g. Verify client_scam_record (total_scam_confirmed=1)

Notes:
  - Model: SBERT (all-MiniLM-L6-v2) + Random Forest — 394 features
  - Threshold: scam_probability >= 0.4 → flagged and auto-closed
  - First run may take ~20s for SBERT cold start; subsequent runs are faster.

Usage:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-scam-detection.py
"""

import sys
import json
import os
import time
import random
import datetime
import requests

BASE_URL       = "http://localhost:8000"
_RUN_ID        = random.randint(1000, 9999)
_PASSWORD      = "SecurePass123!"
_ADMIN_EMAIL   = "psyintann@gmail.com"
_ADMIN_PASS    = "client123"
_CLIENT_EMAIL  = "sarah.fischer@student.president.ac.id"
_CLIENT_PASS   = "admin123"

# Polling config for background scan
_SCAN_POLL_INTERVAL = 2   # seconds between status checks
_SCAN_MAX_WAIT      = 60  # seconds to wait before giving up


# ── Tee (stdout → file) ───────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath):
        self._stdout = sys.stdout
        self._file   = open(filepath, "w", encoding="utf-8")
    def write(self, data):
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

def _start_tee():
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_scam_detection_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath

def _stop_tee(tee, filepath):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved → {filepath}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

_step = 0

def section(title):
    print(f"\n{'#' * 62}")
    print(f"  {title}")
    print(f"{'#' * 62}")

def step(title):
    global _step
    _step += 1
    print(f"\n{'=' * 62}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 62}")

def _req(method, endpoint, body=None, token=None, params=None, allow_fail=False):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    r = getattr(requests, method)(
        f"{BASE_URL}{endpoint}", json=body, headers=headers,
        params=params, timeout=30,
    )
    label = "OK" if r.ok else "FAIL"
    print(f"  {method.upper():<6} {endpoint:<50} [{r.status_code}] {label}")
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if not r.ok and not allow_fail:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data

def post(endpoint, body, token=None, allow_fail=False):
    return _req("post", endpoint, body=body, token=token, allow_fail=allow_fail)

def get(endpoint, token=None, params=None):
    return _req("get", endpoint, token=token, params=params)

def extract(resp):
    return resp.get("details", resp)

def register_and_verify(body):
    resp    = post("/auth/register", body)
    details = extract(resp)
    otp     = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": body["email"], "otp": otp})
    return resp

def login(email, password):
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


# ── Polling helpers ───────────────────────────────────────────────────────────

def poll_job_closed(tok, job_post_id, max_wait=_SCAN_MAX_WAIT):
    """
    Poll until job status becomes 'closed' (ML detected scam and auto-closed it)
    or until timeout. Returns the job dict.
    """
    print(f"  Waiting for background ML scan (max {max_wait}s) ...", end="", flush=True)
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(_SCAN_POLL_INTERVAL)
        elapsed += _SCAN_POLL_INTERVAL
        job = extract(get(f"/job-posts/{job_post_id}", tok))
        status = job.get("status")
        if status == "closed":
            print(f" closed after ~{elapsed}s")
            return job
        print(".", end="", flush=True)
    print(f" timeout ({max_wait}s)")
    job = extract(get(f"/job-posts/{job_post_id}", tok))
    return job

def poll_job_stays_active(tok, job_post_id, wait=_SCAN_MAX_WAIT // 2):
    """
    Wait a fixed time then verify job is still 'active'.
    For the clean scenario — there's no flag to poll for, so we just wait.
    """
    print(f"  Waiting {wait}s for background ML scan to complete ...", end="", flush=True)
    time.sleep(wait)
    print(" done")
    return extract(get(f"/job-posts/{job_post_id}", tok))

def find_scam_flag(tok_admin, job_post_id):
    flags = extract(get("/admin/scam-flags", tok_admin, params={"status": "pending", "page_size": 100}))
    if not isinstance(flags, list):
        return None
    return next((f for f in flags if str(f.get("job_post_id")) == str(job_post_id)), None)


# ── Job texts ─────────────────────────────────────────────────────────────────

# Clean job — professional, specific requirements, no suspicious signals.
_CLEAN_TITLE = "Backend Developer (Python / FastAPI)"
_CLEAN_DESC  = (
    "We are looking for an experienced Backend Developer to join our team. "
    "You will design and build RESTful APIs using Python and FastAPI, "
    "integrating with PostgreSQL and Redis. "
    "Requirements: 3+ years Python experience, solid understanding of async programming, "
    "familiarity with Docker and CI/CD pipelines. "
    "We offer a competitive salary, health benefits, and flexible work hours."
)

# Scam job — work-from-home, no experience, high pay, vague tasks.
# Tuned to score >= 0.4 with the SBERT + RF model (threshold = 0.4).
_SCAM_TITLE = "Work From Home — Earn $500/day No Experience"
_SCAM_DESC  = (
    "We are urgently hiring for work from home data entry jobs. "
    "No experience needed. Earn up to 500 dollars per day. "
    "No skills required. Easy money working from home. "
    "Get paid daily via wire transfer. Limited slots available act now. "
    "Anyone can apply. No qualifications needed. Work from home immediately."
)

# Soft-flag job — vague and suspicious semantics but no classic scam keywords.
# Tuned to score 0.25–0.4: flagged for admin review but NOT auto-closed.
_SOFT_TITLE = "Flexible Home-Based Data Collector"
_SOFT_DESC  = (
    "We are looking for individuals to collect and input data from home. "
    "No formal experience required. You set your own hours. "
    "Compensation is provided per task completed. "
    "Ideal for anyone looking to earn extra income online."
)


# ── Main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "=" * 62)
    print("  Capstone API — Job Scam Detection Walkthrough")
    print("=" * 62)
    print(f"  Target  : {BASE_URL}")
    print(f"  Run ID  : {_RUN_ID}")
    print(f"  Model   : SBERT (all-MiniLM-L6-v2) + Random Forest")
    print(f"  Threshold: scam_probability >= 0.4")
    print(f"  Output  : {out_path}")

    # ── Setup ─────────────────────────────────────────────────────────────────

    section("SETUP")

    step("Admin login")
    tok_admin = login(_ADMIN_EMAIL, _ADMIN_PASS)
    me = extract(get("/auth/me", tok_admin))
    print(f"  logged in as: {me['email']}  is_admin={me['is_admin']}")

    step("Login Scenario 1 client (clean job poster)")
    tok_clean  = login(_CLIENT_EMAIL, _CLIENT_PASS)
    cid_clean  = extract(get("/clients", tok_clean))[0]["client_id"]
    print(f"  email     : {_CLIENT_EMAIL}")
    print(f"  client_id : {cid_clean}")

    step("Login Scenario 2 client (scam job poster)")
    tok_scam  = login(_CLIENT_EMAIL, _CLIENT_PASS)
    cid_scam  = extract(get("/clients", tok_scam))[0]["client_id"]
    print(f"  email     : {_CLIENT_EMAIL}")
    print(f"  client_id : {cid_scam}")


    # ══════════════════════════════════════════════════════════════
    # SCENARIO 1 — CLEAN JOB
    # ══════════════════════════════════════════════════════════════
    section("SCENARIO 1 — CLEAN JOB (expected: stays active, no scam flag)")

    print(f"""
  Job title : {_CLEAN_TITLE}
  Description preview:
    "{_CLEAN_DESC[:120]}..."
    """)

    step("1a — Client posts a legitimate job")
    resp_clean = extract(post("/job-posts", {
        "job_title":          _CLEAN_TITLE,
        "job_description":    _CLEAN_DESC,
        "project_type":       "individual",
        "project_scope":      "medium",
        "estimated_duration": "3 months",
        "experience_level":   "intermediate",
        "status":             "active",
    }, tok_clean))
    jid_clean = resp_clean["job_post_id"]
    print(f"  job_post_id    : {jid_clean}")
    print(f"  status on POST : {resp_clean['status']}")
    print()
    print("  Background ML scan triggered (asyncio task)")
    print("  API returned immediately — user does NOT wait for scan")

    step("1b — Wait for background ML scan, then verify job still active")
    job_clean = poll_job_stays_active(tok_clean, jid_clean, wait=20)
    status_clean = job_clean.get("status")
    print(f"  job status : {status_clean}")

    if status_clean == "active":
        print("  PASS — clean job stays active, ML scan found no scam signals")
    else:
        print(f"  UNEXPECTED — job status is '{status_clean}' (expected 'active')")

    step("1c — Admin verifies no scam flag created for this job")
    flag_clean = find_scam_flag(tok_admin, jid_clean)
    if flag_clean is None:
        print(f"  No pending scam flag found for job {jid_clean}")
        print("  PASS — clean job produced no scam flag")
    else:
        print(f"  UNEXPECTED — scam flag found: {flag_clean}")


    # ══════════════════════════════════════════════════════════════
    # SCENARIO 2 — SCAM JOB
    # ══════════════════════════════════════════════════════════════
    section("SCENARIO 2 — SCAM JOB (expected: auto-closed + admin flag)")

    print(f"""
  Job title : {_SCAM_TITLE}
  Description preview:
    "{_SCAM_DESC[:120]}..."
    """)

    step("2a — Client posts a suspicious job")
    resp_scam = extract(post("/job-posts", {
        "job_title":          _SCAM_TITLE,
        "job_description":    _SCAM_DESC,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "1 month",
        "experience_level":   "entry",
        "status":             "active",
    }, tok_scam))
    jid_scam = resp_scam["job_post_id"]
    print(f"  job_post_id    : {jid_scam}")
    print(f"  status on POST : {resp_scam['status']}")
    print()
    print("  Job returned as 'active' to user immediately.")
    print("  Background ML scan triggered (asyncio task) — user does NOT wait.")

    step("2b — Wait for background ML scan to close the job")
    job_scam = poll_job_closed(tok_scam, jid_scam, max_wait=_SCAN_MAX_WAIT)
    status_scam = job_scam.get("status")
    print(f"  job status       : {status_scam}")
    print(f"  closure_reason   : {job_scam.get('closure_reason')}")
    print(f"  closure_note     : {job_scam.get('closure_note')}")

    if status_scam == "closed" and job_scam.get("closure_reason") == "scam":
        print("  PASS — scam job auto-closed by ML scan")
    else:
        print(f"  UNEXPECTED — job status is '{status_scam}'")
        print("  (Check server logs — model may need warm-up on first run)")

    step("2c — Admin views the scam flag in the queue")
    flag_scam = find_scam_flag(tok_admin, jid_scam)
    if flag_scam:
        print(f"  flag_id          : {flag_scam.get('flag_id')}")
        print(f"  scam_score       : {flag_scam.get('scam_score')}")
        print(f"  detected_keywords: {flag_scam.get('detected_keywords')}")
        print(f"  flag status      : {flag_scam.get('status')}")
        print(f"  flagged_text     : {str(flag_scam.get('flagged_text', ''))[:80]}...")
        print(f"  job_title        : {flag_scam.get('job_title')}")
        print(f"  client_name      : {flag_scam.get('client_name')}")
        print(f"  client_email     : {flag_scam.get('client_email')}")
        print("  PASS — scam flag visible in admin queue with client info")
    else:
        print(f"  No pending scam flag found for job {jid_scam}")
        print("  (Scan may not have completed yet or score was below threshold)")
        sys.exit(1)

    step("2d — Admin removes the scam flag (confirms scam)")
    flag_id = str(flag_scam["flag_id"])
    removed = extract(post(
        f"/admin/scam-flags/{flag_id}/remove",
        {"admin_note": "Confirmed scam — vague tasks, unrealistic pay, no skill requirement."},
        tok_admin,
    ))
    print(f"  flag status after remove : {removed.get('status')}")

    if removed.get("status") == "removed":
        print("  PASS — flag confirmed and removed by admin")
    else:
        print(f"  UNEXPECTED — flag status is '{removed.get('status')}'")

    step("2e — Verify client gets a scam strike")
    rec = extract(get(f"/admin/scam-flags/client/{cid_scam}", tok_admin))
    print(f"  client_id              : {cid_scam}")
    print(f"  total_scam_confirmed   : {rec.get('total_scam_confirmed')}")
    print(f"  is_banned              : {rec.get('is_banned')}")

    if rec.get("total_scam_confirmed", 0) >= 1:
        print("  PASS — client has scam strike on record")
        print("  (3 strikes = permanent ban)")
    else:
        print("  UNEXPECTED — no strike recorded")

    step("2f — Verify job post remains closed after admin action")
    job_after = extract(get(f"/job-posts/{jid_scam}", tok_scam))
    print(f"  job status      : {job_after.get('status')}")
    print(f"  closure_reason  : {job_after.get('closure_reason')}")

    if job_after.get("status") == "closed":
        print("  PASS — job remains closed after admin confirms scam")
    else:
        print(f"  UNEXPECTED — job status is '{job_after.get('status')}'")


    # ══════════════════════════════════════════════════════════════
    # SCENARIO 3 — SOFT FLAG
    # ══════════════════════════════════════════════════════════════
    section("SCENARIO 3 — SOFT FLAG (expected: job stays active + admin review flag)")

    print(f"""
  Job title : {_SOFT_TITLE}
  Description preview:
    "{_SOFT_DESC[:120]}..."
    """)

    step("3a — Client posts a subtly suspicious job")
    resp_soft = extract(post("/job-posts", {
        "job_title":          _SOFT_TITLE,
        "job_description":    _SOFT_DESC,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "1 month",
        "experience_level":   "entry",
        "status":             "active",
    }, tok_clean))
    jid_soft = resp_soft["job_post_id"]
    print(f"  job_post_id    : {jid_soft}")
    print(f"  status on POST : {resp_soft['status']}")
    print()
    print("  Background ML scan triggered (asyncio task)")
    print("  Score expected: 0.25–0.4 → soft flag only, job stays active")

    step("3b — Wait for background ML scan, verify job still active")
    job_soft = poll_job_stays_active(tok_clean, jid_soft, wait=20)
    status_soft = job_soft.get("status")
    print(f"  job status : {status_soft}")

    if status_soft == "active":
        print("  PASS — soft-flag job NOT auto-closed, still visible to freelancers")
    else:
        print(f"  UNEXPECTED — job status is '{status_soft}' (expected 'active')")

    step("3c — Admin sees soft flag in queue (auto_closed=false)")
    flag_soft = find_scam_flag(tok_admin, jid_soft)
    if flag_soft:
        print(f"  flag_id          : {flag_soft.get('flag_id')}")
        print(f"  scam_score       : {flag_soft.get('scam_score')}")
        print(f"  auto_closed      : {flag_soft.get('auto_closed')}")
        print(f"  flag status      : {flag_soft.get('status')}")
        print(f"  job_title        : {flag_soft.get('job_title')}")
        print(f"  client_email     : {flag_soft.get('client_email')}")
        if flag_soft.get("auto_closed") is False:
            print("  PASS — soft flag visible in admin queue, job still active")
        else:
            print("  UNEXPECTED — auto_closed should be False for soft flag")
    else:
        print(f"  No pending flag found for job {jid_soft}")
        print("  UNEXPECTED — soft flag should have been created (score 0.25–0.4)")

    step("3d — Admin dismisses soft flag as safe")
    if flag_soft:
        flag_soft_id = str(flag_soft["flag_id"])
        dismissed = extract(post(
            f"/admin/scam-flags/{flag_soft_id}/approve",
            {"admin_note": "Reviewed — borderline content but not confirmed scam."},
            tok_admin,
        ))
        print(f"  flag status after approve : {dismissed.get('status')}")
        job_after_soft = extract(get(f"/job-posts/{jid_soft}", tok_clean))
        print(f"  job status after approve  : {job_after_soft.get('status')}")
        if dismissed.get("status") == "safe" and job_after_soft.get("status") == "active":
            print("  PASS — soft flag dismissed, job remains active (was never closed)")
        else:
            print("  UNEXPECTED — check flag and job status above")


    # ── Summary ───────────────────────────────────────────────────────────────

    print("\n" + "=" * 62)
    print("  Scam Detection Walkthrough Complete")
    print("=" * 62)
    print()
    print("  Results:")
    print(f"    Scenario 1 (clean)     : job {jid_clean}")
    print(f"      → status: {status_clean}  |  scam flag: {'none' if not flag_clean else 'created'}")
    print()
    print(f"    Scenario 2 (hard scam) : job {jid_scam}")
    print(f"      → status: {status_scam}  |  scam_score: {flag_scam.get('scam_score') if flag_scam else 'n/a'}")
    print(f"      → auto_closed: True   |  client strikes: {rec.get('total_scam_confirmed')}")
    print()
    print(f"    Scenario 3 (soft flag) : job {jid_soft}")
    print(f"      → status: {status_soft}  |  scam_score: {flag_soft.get('scam_score') if flag_soft else 'n/a'}")
    print(f"      → auto_closed: False  |  admin review only, no auto-close")
    print()
    print("  Full flow recap:")
    print("    User POST job       →  job saved + response returned immediately")
    print("    Background ML scan  →  SBERT + RF, threshold=0.4 hard / 0.25 soft")
    print("    score < 0.25        →  clean, nothing happens")
    print("    score 0.25–0.4      →  SOFT FLAG: job stays active, queued for admin review")
    print("    score >= 0.4        →  HARD FLAG: job AUTO-CLOSED + queued for admin audit")
    print()
    print("  Admin actions:")
    print("    Soft flag approve   →  dismissed, job was already active (no change)")
    print("    Hard flag approve   →  false positive, job reopened to 'active'")
    print("    Any flag remove     →  scam confirmed + client gets a strike")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
