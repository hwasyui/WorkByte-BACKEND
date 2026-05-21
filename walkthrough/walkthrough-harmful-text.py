"""
Harmful Text Detection Walkthrough — RoBERTa ML model (keyword fallback).

Uses clientinputjobs@client.com — the same client that seeded 100 normal jobs —
to show that toxic job posts from the same account are caught by the moderation
pipeline regardless of the poster's history.

Three scenarios:

  Scenario 1 — CLEAN JOB
    Same client posts a normal, professional job description.
    Background harmful text scan finds nothing. No moderation flag.
    Job stays active.

  Scenario 2 — MILDLY TOXIC JOB  (insult + toxic labels)
    Client posts a job with insulting, demeaning language.
    Background ML/keyword scan flags it (pending in admin queue).
    Admin manually rejects → job is immediately closed.

  Scenario 3 — SEVERELY TOXIC JOB  (multi-label: identity hate + threat + obscene)
    Client posts a job with discriminatory, threatening, and obscene language.
    Flag appears with high cumulative label score (total_score >= 0.85).
    Walkthrough uses POST /admin/moderation/force-expire to simulate the
    30-day auto-action timer expiring → job auto-closed by the system.

Toxicity labels detected (6 total):
  toxic, severe_toxic, obscene, threat, insult, identity_hate

Thresholds:
  1 keyword hit → score = 0.35 per label  → flagged for admin review
  2 hits        → score = 0.70 per label
  3+ hits       → score = 1.00 per label (capped)
  total_score   = sum of all 6 label scores (max 6.0)
  AUTO-CLOSE    : total_score >= 0.85 (job posts), triggered by force-expire

Usage:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-harmful-text.py
Or locally:
    python walkthrough/walkthrough-harmful-text.py
"""

import sys
import json
import os
import time
import datetime
import requests

BASE_URL      = os.environ.get("BASE_URL", "http://localhost:8000")

_CLIENT_EMAIL  = "clientinputjobs@client.com"
_CLIENT_PASS   = "SecurePass123"
_CLIENT_NAME   = "Jobs Seeder Client"

_ADMIN_EMAIL   = "admin@admin.com"
_ADMIN_PASS    = "thisisanadminaccountpassword"

_POLL_INTERVAL = 2    # seconds between moderation-queue polls
_POLL_MAX_WAIT = 60   # seconds before giving up on background scan


# ── Tee ───────────────────────────────────────────────────────────────────────

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
    filepath = os.path.join(out_dir, f"walkthrough_toxicity_{ts}.md")
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
    print(f"\n{'#' * 65}")
    print(f"  {title}")
    print(f"{'#' * 65}")

def step(title):
    global _step
    _step += 1
    print(f"\n{'=' * 65}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 65}")

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
    print(f"  {method.upper():<6} {endpoint:<55} [{r.status_code}] {label}")
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

def login_or_register(email, password, full_name, user_type="client"):
    resp  = _req("post", "/auth/login", body={"email": email, "password": password}, allow_fail=True)
    details = extract(resp)
    token = details.get("access_token") if isinstance(details, dict) else None
    if token:
        return token
    # Register
    reg = _req("post", "/auth/register", body={
        "email": email, "password": password,
        "user_type": user_type, "full_name": full_name,
    }, allow_fail=True)
    reg_details = extract(reg)
    otp = reg_details.get("verification", {}).get("dev_verification_otp") if isinstance(reg_details, dict) else None
    if otp:
        _req("post", "/auth/verify-email", body={"email": email, "otp": otp}, allow_fail=True)
    resp2 = _req("post", "/auth/login", body={"email": email, "password": password})
    return extract(resp2)["access_token"]


# ── Polling helpers ───────────────────────────────────────────────────────────

def poll_moderation_flag(tok_admin, job_post_id, max_wait=_POLL_MAX_WAIT):
    """
    Poll GET /admin/moderation until a pending flag appears for job_post_id,
    or until timeout. Returns the flag dict or None.
    """
    print(f"  Polling admin moderation queue (max {max_wait}s) ...", end="", flush=True)
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL
        flags = extract(get("/admin/moderation", tok_admin,
                            params={"status": "pending", "content_type": "job_post", "page_size": 50}))
        if isinstance(flags, list):
            match = next((f for f in flags if str(f.get("content_id")) == str(job_post_id)), None)
            if match:
                print(f" found after ~{elapsed}s")
                return match
        print(".", end="", flush=True)
    print(f" timeout ({max_wait}s)")
    return None

def get_job(tok, job_post_id):
    return extract(get(f"/job-posts/{job_post_id}", tok))


# ── Job texts ─────────────────────────────────────────────────────────────────

# Clean — professional tone, no keywords.
_CLEAN_TITLE = "Senior React Developer for Dashboard Project"
_CLEAN_DESC  = (
    "We are looking for a skilled React developer to build an analytics dashboard. "
    "Requirements: 3+ years of React experience, proficiency with TypeScript and "
    "Tailwind CSS, familiarity with REST APIs and data visualisation libraries. "
    "We offer a competitive rate, flexible hours, and a collaborative remote team."
)

# Mildly toxic — insulting, demeaning language toward applicants.
# Keywords triggered (keyword fallback):
#   toxic     : 'idiot', 'moron'        → toxic_score     = 0.70  (2 hits × 0.35)
#   insult    : 'disgusting', 'garbage' → insult_score     = 0.70  (2 hits × 0.35)
#   total_score ≈ 1.40  →  >= 0.85 auto-close threshold
_MILD_TITLE  = "Backend Developer Needed (Serious Applicants Only)"
_MILD_DESC   = (
    "We have wasted months interviewing complete idiots and morons who can't write "
    "basic SQL. If you are one of those disgusting, garbage developers who submits "
    "copy-pasted code, do NOT apply. We need someone who actually knows what they are doing. "
    "Skills: Python, FastAPI, PostgreSQL. Budget: negotiable for the right person."
)

# Severely toxic — identity hate, threat, and obscene content across multiple labels.
# Keywords triggered (keyword fallback):
#   identity_hate : 'sexist', 'racist'            → identity_hate_score = 0.70
#   threat        : 'hurt you', 'kill you'         → threat_score        = 0.70
#   obscene       : 'nude model', 'sex for money'  → obscene_score       = 0.70
#   insult        : 'disgusting', 'scum'           → insult_score        = 0.70
#   total_score   ≈ 2.80  →  well above 0.85 auto-close threshold
_SEVERE_TITLE = "Urgent: Developer for Client Project"
_SEVERE_DESC  = (
    "We are tired of sexist and racist developers ruining our projects. "
    "We will hurt you and make you regret it if you waste our time again. "
    "We once had a developer who was running a nude model site and doing sex for money "
    "on the side — absolutely disgusting scum. "
    "We need a reliable fullstack developer. Immediate start. Contact us now."
)


# ── Main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "=" * 65)
    print("  Capstone API — Harmful Text Detection Walkthrough")
    print("=" * 65)
    print(f"  Target   : {BASE_URL}")
    print(f"  Client   : {_CLIENT_EMAIL}  (same account as bulk job seeder)")
    print(f"  Admin    : {_ADMIN_EMAIL}")
    print(f"  Model    : RoBERTa ML (keyword fallback)")
    print(f"  Threshold: any label hit → flagged; total_score >= 0.85 → auto-close")
    print(f"  Output   : {out_path}")


    # ── Setup ─────────────────────────────────────────────────────────────────
    section("SETUP")

    step("Login as admin")
    tok_admin = login_or_register(_ADMIN_EMAIL, _ADMIN_PASS, "Admin User", "client")
    me_admin  = extract(get("/auth/me", tok_admin))
    print(f"  email    : {me_admin.get('email')}")
    print(f"  is_admin : {me_admin.get('is_admin')}")
    if not me_admin.get("is_admin"):
        print("  WARNING — account is not admin; moderation endpoints will return 403")

    step("Login as client (clientinputjobs@client.com)")
    tok_client = login_or_register(_CLIENT_EMAIL, _CLIENT_PASS, _CLIENT_NAME, "client")
    me_client  = extract(get("/auth/me", tok_client))
    print(f"  email     : {me_client.get('email')}")
    print(f"  user_id   : {me_client.get('user_id')}")
    print(f"  client_id : {me_client.get('client_id')}")
    print("  (This is the same account that seeded 100 legitimate jobs.)")


    # ══════════════════════════════════════════════════════════════════════════
    # SCENARIO 1 — CLEAN JOB
    # ══════════════════════════════════════════════════════════════════════════
    section("SCENARIO 1 — CLEAN JOB  (expected: active, no moderation flag)")

    print(f"\n  Title      : {_CLEAN_TITLE}")
    print(f"  Preview    : \"{_CLEAN_DESC[:100]}...\"")
    print(f"  Keywords   : none")

    step("1a — Client posts a legitimate job")
    resp1   = extract(post("/job-posts", {
        "job_title":          _CLEAN_TITLE,
        "job_description":    _CLEAN_DESC,
        "project_type":       "individual",
        "project_scope":      "medium",
        "estimated_duration": "6 weeks",
        "experience_level":   "intermediate",
        "status":             "active",
    }, tok_client))
    jid1 = resp1["job_post_id"]
    print(f"  job_post_id    : {jid1}")
    print(f"  status on POST : {resp1['status']}")
    print("\n  Background harmful text scan triggered — API returned immediately.")

    step("1b — Wait, then confirm no moderation flag")
    print(f"  Waiting {_POLL_MAX_WAIT // 3}s for background scan to complete ...")
    time.sleep(_POLL_MAX_WAIT // 3)
    flags1 = extract(get("/admin/moderation", tok_admin,
                         params={"status": "pending", "content_type": "job_post", "page_size": 50}))
    flag1  = next((f for f in (flags1 if isinstance(flags1, list) else [])
                   if str(f.get("content_id")) == str(jid1)), None)
    job1   = get_job(tok_client, jid1)
    print(f"  job status       : {job1.get('status')}")
    print(f"  moderation flag  : {'none' if flag1 is None else flag1}")
    if job1.get("status") == "active" and flag1 is None:
        print("  PASS — clean job active, no harmful text flag created")
    else:
        print("  UNEXPECTED — clean job may have been incorrectly flagged")


    # ══════════════════════════════════════════════════════════════════════════
    # SCENARIO 2 — MILDLY TOXIC JOB  (manual admin reject)
    # ══════════════════════════════════════════════════════════════════════════
    section("SCENARIO 2 — MILDLY TOXIC JOB  (expected: flagged → admin rejects → closed)")

    print(f"\n  Title      : {_MILD_TITLE}")
    print(f"  Preview    : \"{_MILD_DESC[:100]}...\"")
    print(f"  Keywords   : 'idiot', 'moron' (toxic) + 'disgusting', 'garbage' (insult)")
    print(f"  Expected   : toxic_score=0.70, insult_score=0.70 → total≈1.40")

    step("2a — Client posts a job with insulting language")
    resp2 = extract(post("/job-posts", {
        "job_title":          _MILD_TITLE,
        "job_description":    _MILD_DESC,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "4 weeks",
        "experience_level":   "intermediate",
        "status":             "active",
    }, tok_client))
    jid2 = resp2["job_post_id"]
    print(f"  job_post_id    : {jid2}")
    print(f"  status on POST : {resp2['status']}")
    print("\n  Job returned as 'active' to client immediately.")
    print("  Background harmful text scan running (asyncio task) ...")

    step("2b — Poll admin moderation queue for the flag")
    flag2 = poll_moderation_flag(tok_admin, jid2)
    if flag2:
        mid2 = str(flag2["moderation_id"])
        print(f"  moderation_id    : {mid2}")
        print(f"  content_type     : {flag2.get('content_type')}")
        print(f"  toxic_score      : {flag2.get('toxic_score')}")
        print(f"  severe_toxic     : {flag2.get('severe_toxic_score')}")
        print(f"  insult_score     : {flag2.get('insult_score')}")
        print(f"  identity_hate    : {flag2.get('identity_hate_score')}")
        print(f"  detected_labels  : {flag2.get('detected_labels')}")
        print(f"  flagged_text     : \"{str(flag2.get('flagged_text',''))[:80]}...\"")
        print(f"  flag status      : {flag2.get('status')}")
        print(f"  scan_method      : {flag2.get('scan_method', 'n/a')}")
        print("  PASS — toxic content flag visible in admin moderation queue")
    else:
        print("  FAIL — no flag found within timeout")
        print("  (Check server logs; ML model may need warm-up or keyword list may differ)")
        mid2 = None

    step("2c — Flag left PENDING for manual admin review")
    if mid2:
        job2 = get_job(tok_client, jid2)
        print(f"  moderation_id : {mid2}")
        print(f"  flag status   : {flag2.get('status')}  ← pending for you to try")
        print(f"  job status    : {job2.get('status')}   ← still active until admin acts")
        print()
        print("  To test the approve/reject flow yourself:")
        print(f"    POST /admin/moderation/{mid2}/approve  → flag confirmed, job will be closed")
        print(f"    POST /admin/moderation/{mid2}/reject   → flag dismissed, job stays active")
    else:
        print("  SKIP — no flag found in step 2b")


    # ══════════════════════════════════════════════════════════════════════════
    # SCENARIO 3 — SEVERELY TOXIC JOB  (auto-close via force-expire)
    # ══════════════════════════════════════════════════════════════════════════
    section("SCENARIO 3 — SEVERELY TOXIC JOB  (expected: high score → force-expire → auto-closed)")

    print(f"\n  Title      : {_SEVERE_TITLE}")
    print(f"  Preview    : \"{_SEVERE_DESC[:100]}...\"")
    print(f"  Keywords   : 'sexist','racist' (identity_hate) + 'hurt you','kill you' (threat)")
    print(f"               'nude model','sex for money' (obscene) + 'disgusting','scum' (insult)")
    print(f"  Expected   : total_score ≈ 2.80  (well above 0.85 auto-close threshold)")
    print()
    print("  The system normally waits 30 days before auto-closing.")
    print("  POST /admin/moderation/force-expire backdates the timer to simulate it.")

    step("3a — Client posts a severely toxic job")
    resp3 = extract(post("/job-posts", {
        "job_title":          _SEVERE_TITLE,
        "job_description":    _SEVERE_DESC,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "2 weeks",
        "experience_level":   "entry",
        "status":             "active",
    }, tok_client))
    jid3 = resp3["job_post_id"]
    print(f"  job_post_id    : {jid3}")
    print(f"  status on POST : {resp3['status']}")

    step("3b — Poll admin moderation queue for the flag")
    flag3 = poll_moderation_flag(tok_admin, jid3)
    if flag3:
        mid3 = str(flag3["moderation_id"])
        ts   = (flag3.get("toxic_score", 0) + flag3.get("severe_toxic_score", 0) +
                flag3.get("obscene_score", 0) + flag3.get("threat_score", 0) +
                flag3.get("insult_score", 0) + flag3.get("identity_hate_score", 0))
        print(f"  moderation_id    : {mid3}")
        print(f"  toxic_score      : {flag3.get('toxic_score')}")
        print(f"  severe_toxic     : {flag3.get('severe_toxic_score')}")
        print(f"  obscene_score    : {flag3.get('obscene_score')}")
        print(f"  threat_score     : {flag3.get('threat_score')}")
        print(f"  insult_score     : {flag3.get('insult_score')}")
        print(f"  identity_hate    : {flag3.get('identity_hate_score')}")
        print(f"  total_score      : {round(ts, 4)}  (threshold = 0.85)")
        print(f"  detected_labels  : {flag3.get('detected_labels')}")
        print(f"  flag status      : {flag3.get('status')}")
        if ts >= 0.85:
            print("  PASS — total_score above auto-close threshold")
        else:
            print(f"  NOTE — total_score {round(ts,4)} < 0.85; ML model may have lower scores than keyword estimate")
    else:
        print("  FAIL — no flag found within timeout")
        mid3 = None

    step("3c — Force-expire the 30-day timer to trigger automatic action")
    if mid3:
        print(f"  Backdating auto_approve_at for moderation_id={mid3} ...")
        expire_resp = extract(post(
            "/admin/moderation/force-expire",
            {"ids": [mid3]},
            tok_admin,
        ))
        print(f"  force-expire result : {expire_resp}")

        job3 = get_job(tok_client, jid3)
        print(f"  job status          : {job3.get('status')}")
        print(f"  closure_reason      : {job3.get('closure_reason')}")

        # Re-fetch flag to see updated status
        flags_after = extract(get("/admin/moderation", tok_admin,
                                  params={"status": "all", "content_type": "job_post", "page_size": 50}))
        flag3_after = next(
            (f for f in (flags_after if isinstance(flags_after, list) else [])
             if str(f.get("content_id")) == str(jid3)),
            None
        )
        print(f"  moderation status   : {flag3_after.get('status') if flag3_after else 'not found'}")

        ts = (flag3.get("toxic_score", 0) + flag3.get("severe_toxic_score", 0) +
              flag3.get("obscene_score", 0) + flag3.get("threat_score", 0) +
              flag3.get("insult_score", 0) + flag3.get("identity_hate_score", 0))

        if job3.get("status") == "closed" and round(ts, 2) >= 0.85:
            print("  PASS — system auto-closed job after force-expire (total_score >= 0.85)")
            print(f"  flag status → approved  (flag confirmed by timer)")
        elif job3.get("status") == "closed":
            print("  PASS — job closed (note: ML model scores may differ from keyword estimate)")
            print(f"  flag status → approved  (flag confirmed by timer)")
        elif flag3_after and flag3_after.get("status") == "rejected":
            print("  NOTE — flag was auto-dismissed (total_score < 0.85 per ML model scores)")
            print("         ML model gave lower scores than keyword heuristic; job stays active.")
            print("         In production you would use force-expire on a high-score item.")
        else:
            print(f"  UNEXPECTED — job status: {job3.get('status')}, flag status: {flag3_after.get('status') if flag3_after else 'n/a'}")
    else:
        print("  SKIP — no flag found in step 3b, cannot force-expire")


    # ══════════════════════════════════════════════════════════════════════════
    # SCENARIO 4 — DISMISS TEST  (admin rejects = dismisses flag, job stays)
    # ══════════════════════════════════════════════════════════════════════════
    section("SCENARIO 4 — DISMISS FLOW  (expected: flagged → admin dismisses → job stays active)")

    print(f"\n  Title      : {_MILD_TITLE}")
    print(f"  Preview    : \"{_MILD_DESC[:100]}...\"")
    print(f"  Action     : admin calls /reject  (dismisses the flag — false positive decision)")

    step("4a — Client posts a mildly toxic job (same text as Scenario 2)")
    resp4 = extract(post("/job-posts", {
        "job_title":          _MILD_TITLE,
        "job_description":    _MILD_DESC,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "3 weeks",
        "experience_level":   "intermediate",
        "status":             "active",
    }, tok_client))
    jid4 = resp4["job_post_id"]
    print(f"  job_post_id    : {jid4}")
    print(f"  status on POST : {resp4['status']}")

    step("4b — Poll admin moderation queue for the flag")
    flag4 = poll_moderation_flag(tok_admin, jid4)
    mid4 = None
    if flag4:
        mid4 = str(flag4["moderation_id"])
        print(f"  moderation_id  : {mid4}")
        print(f"  flag status    : {flag4.get('status')}")
        print("  PASS — flag visible in admin queue")
    else:
        print("  FAIL — no flag found within timeout")

    step("4c — Admin dismisses the flag (POST /reject)")
    if mid4:
        dismissed4 = extract(post(
            f"/admin/moderation/{mid4}/reject",
            {"admin_note": "Reviewed and determined this is a false positive — job description is acceptable."},
            tok_admin,
        ))
        print(f"  moderation status after dismiss : {dismissed4.get('status')}")
        job4 = get_job(tok_client, jid4)
        print(f"  job status after dismiss        : {job4.get('status')}")
        if dismissed4.get("status") == "rejected" and job4.get("status") != "closed":
            print("  PASS — flag dismissed (status=rejected), job remains active")
        else:
            print(f"  UNEXPECTED — moderation={dismissed4.get('status')}, job={job4.get('status')}")
    else:
        print("  SKIP — no flag found in step 4b, cannot dismiss")

    jid4_val = jid4 if 'jid4' in dir() else 'n/a'


    # ── Summary ───────────────────────────────────────────────────────────────
    section("SUMMARY")

    print(f"""
  Client tested : {_CLIENT_EMAIL}
  (Same account that seeded 100 legitimate jobs — history does not exempt
   from moderation. Every job post goes through the same pipeline.)

  Results:
    Scenario 1 (clean)         : job {jid1}
      job status  → active   |  moderation flag → none

    Scenario 2 (mildly toxic)  : job {jid2}
      keywords    → insult + toxic labels
      flag        → PENDING in admin queue  ← try approve/reject yourself

    Scenario 3 (severely toxic): job {jid3}
      keywords    → identity_hate + threat + obscene + insult
      total_score → >= 0.85 auto-close threshold
      action      → force-expire timer → flag status=approved, job auto-closed

    Scenario 4 (dismiss test)  : job {jid4_val}
      keywords    → insult + toxic labels
      admin action→ reject (dismiss) → flag status=rejected, job stays active

  Detection flow:
    POST /job-posts         →  job saved, response returned immediately
    Background async task   →  RoBERTa ML scan (keyword fallback if unavailable)
    is_flagged = False      →  nothing; job stays active
    is_flagged = True       →  harmful_text_queue record inserted (status=pending)
    Admin GET /admin/moderation    →  view flag with per-label scores
    Admin POST .../approve         →  flag CONFIRMED → job closed (content_violation)
    Admin POST .../reject          →  flag DISMISSED → job stays active
    Force-expire (30-day timer)    →  total_score >= 0.85 → status=approved + close
                                      total_score <  0.85 → status=rejected + dismiss
    """)

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
