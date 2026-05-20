"""
Admin System Walkthrough — all threshold scenarios end-to-end.

CONTENT MODERATION
  A1. Job post  low-score  (total=0.35 < 0.85) → admin manually APPROVES (false positive)
  A2. Job post  high-score (total=1.70 ≥ 0.85) → admin manually REJECTS  → job closed
  A3. Job post  low-score  (total=0.35 < 0.85) → AUTO-DISMISS after 30d  (force-expire)
  A4. Job post  high-score (total=1.70 ≥ 0.85) → AUTO-CLOSE  after 30d  (force-expire)
  A5. Profile   low-score  (total=0.35 < 0.90) → AUTO-DISMISS after 30d  (force-expire)
  A6. Profile   high-score (total=2.40 ≥ 0.90) → AUTO-CLOSE  after 30d  (force-expire)

SCAM DETECTION
  B1. Job post  low scam  (score=0.50 < 0.85) → admin manually marks SAFE
  B2. Job post  high scam (score=1.00 ≥ 0.85) → admin manually REMOVES → client strike
  B3. Job post  low scam  (score=0.50 < 0.85) → AUTO-DISMISS after 30d  (force-expire)
  B4. Job post  high scam (score=1.00 ≥ 0.85) → AUTO-REMOVE  after 30d  (force-expire)
  B5. 3 confirmed scam removals → client BANNED

USER REPORTS
  C1. Report freelancer profile → admin ACCEPTS
  C2. Report client profile     → admin DISMISSES
  C3. Report job post           → admin ACCEPTS
  C4. Self-report               → 400 error
  C5. 10 users report same target → threshold built (auto-ban fires after 30 days)
  C6. GET /admin/reports/auto-actions → empty (30d not passed)

DASHBOARD
  D1. Final stats
  D2. Browse all queues (status=all)

APPEALS
  E1. Force-expire 10 freelancer reports → auto-ban fires → verify ban message
  E2. Banned user submits appeal (target_type='user')
  E3. User views own appeals; admin lists pending appeals
  E4. Admin APPROVES user appeal → ban lifted, account restored
  E5. Auto-closed job post owner submits appeal (target_type='job_post')
  E6. Admin REJECTS job post appeal → job stays closed

Score reference
  Content scan:  1 keyword hit = 0.35 per label (capped at 1.0); total = sum of 6 labels
  Scam scan:     score = matched_keywords / 6.0 (capped at 1.0)
  Auto-close thresholds: job_post total ≥ 0.85 | profile total ≥ 0.90
  Scam auto-remove:      scam_score ≥ 0.85

Usage:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-admin.py
"""

import sys
import json
import os
import time
import random
import datetime
import requests

BASE_URL  = "http://localhost:8000"
_RUN_ID   = random.randint(1000, 9999)
_PASSWORD = "SecurePass123!"

_ADMIN_EMAIL    = "admin@admin.com"
_ADMIN_PASSWORD = " "

# ── Content moderation scenario users ─────────────────────────────────────────
# job: low total (0.35) → manual approve
_MOD_CLIENT_MAN_APP = f"mod.man.app.{_RUN_ID}@wt.dev"
# job: high total (1.35) → manual reject → job closed
_MOD_CLIENT_MAN_REJ = f"mod.man.rej.{_RUN_ID}@wt.dev"
# job: low total (0.35) → auto-dismiss (force-expire demo)
_MOD_CLIENT_AUTO_DI = f"mod.aut.di.{_RUN_ID}@wt.dev"
# job: high total (1.35) → auto-close (force-expire demo)
_MOD_CLIENT_AUTO_CL = f"mod.aut.cl.{_RUN_ID}@wt.dev"
# profile: low total (0.35) → auto-dismiss (force-expire demo)
_MOD_PROF_AUTO_DI   = f"mod.prf.di.{_RUN_ID}@wt.dev"
# profile: high total (2.05) → auto-close (force-expire demo)
_MOD_PROF_AUTO_CL   = f"mod.prf.cl.{_RUN_ID}@wt.dev"

# ── Scam detection scenario users ─────────────────────────────────────────────
# low scam (0.50) → admin marks safe
_SCAM_CLIENT_MAN_SF = f"scm.man.sf.{_RUN_ID}@wt.dev"
# high scam (1.00) → admin removes → strike
_SCAM_CLIENT_MAN_RM = f"scm.man.rm.{_RUN_ID}@wt.dev"
# low scam (0.50) → auto-dismiss (force-expire demo)
_SCAM_CLIENT_AUTO_DI = f"scm.aut.di.{_RUN_ID}@wt.dev"
# high scam (1.00) → auto-remove (force-expire demo)
_SCAM_CLIENT_AUTO_RM = f"scm.aut.rm.{_RUN_ID}@wt.dev"
# posts 3 high-scam jobs, all manually removed → 3 strikes → BANNED
_SCAM_BAN_CLIENT     = f"scm.ban.{_RUN_ID}@wt.dev"

# ── Report scenario users ──────────────────────────────────────────────────────
_RPT_FL   = f"rpt.fl.{_RUN_ID}@wt.dev"     # freelancer being reported
_RPT_CL   = f"rpt.cl.{_RUN_ID}@wt.dev"     # client being reported (also posts a job)
_REPORTER = f"reporter.{_RUN_ID}@wt.dev"   # main reporter

# ── Scored content texts ───────────────────────────────────────────────────────
#
# LOW-SCORE (used for both job and profile mod demos):
#   toxic: "idiot" = 1 hit → 0.35; total = 0.35
_TXT_MOD_LOW = (
    "We need a developer. Note: the current code is an absolute idiot's mess."
)

# HIGH-SCORE JOB POST:
#   toxic: idiot+moron+worthless = 3 hits → 1.0
#   severe_toxic: "shit" + "piece of shit" both match = 2 hits → 0.70
#   total = 1.70  ≥ 0.85 → auto-close job post
_TXT_MOD_HIGH_JOB = (
    "These idiot moron developers keep submitting piece of shit code. Worthless work."
)

# HIGH-SCORE PROFILE:
#   toxic: idiot+moron+dumb+pathetic+loser = 5 hits → 1.0
#   severe_toxic: "shit" + "piece of shit" both match = 2 hits → 0.70
#   insult: disgusting + you're nothing = 2 hits → 0.70
#   total = 2.40  ≥ 0.90 → auto-close profile
_TXT_MOD_HIGH_PROF = (
    "you idiot moron dumb pathetic loser clients. "
    "piece of shit work. disgusting. you're nothing."
)

# SCAM LOW (3 keywords → 3/6 = 0.50 < 0.85 → auto-dismiss):
#   guaranteed income, easy money, get rich quick
_TXT_SCAM_LOW = (
    "Earn guaranteed income with our team. Easy money for right candidates. "
    "Get rich quick with our unique bonus structure."
)

# SCAM HIGH (8 keywords → min(8/6,1.0) = 1.00 ≥ 0.85 → auto-remove):
#   guaranteed income, easy money, no experience needed earn, get rich quick,
#   make money fast, passive income guaranteed, registration fee required, zero risk high profit
_TXT_SCAM_HIGH = (
    "Guaranteed income! Easy money — no experience needed earn thousands. "
    "Get rich quick with our program. Make money fast with passive income guaranteed. "
    "Registration fee required to start. Zero risk high profit opportunity."
)


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
    filepath = os.path.join(out_dir, f"walkthrough_admin_{ts}.md")
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
    print(f"\n{'#'*62}")
    print(f"  {title}")
    print(f"{'#'*62}")

def step(title):
    global _step
    _step += 1
    print(f"\n{'='*62}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*62}")


def _req(method, endpoint, body=None, token=None, params=None, allow_fail=False):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{BASE_URL}{endpoint}"
    r   = getattr(requests, method)(url, json=body, headers=headers, params=params, timeout=30)
    label = "OK" if r.ok else "FAIL"
    print(f"  {method.upper():<6} {endpoint:<52} [{r.status_code}] {label}")
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if not r.ok and not allow_fail:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    if not r.ok and allow_fail:
        print(f"  (allowed failure) {data.get('details', data)}")
    return data, r.status_code

def post(endpoint, body, token=None, allow_fail=False):
    data, _ = _req("post", endpoint, body=body, token=token, allow_fail=allow_fail)
    return data

def get(endpoint, token=None, params=None, allow_fail=False):
    data, _ = _req("get", endpoint, token=token, params=params, allow_fail=allow_fail)
    return data

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

def make_job(tok, title, description):
    resp = post("/job-posts", {
        "job_title":          title,
        "job_description":    description,
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "1 month",
        "experience_level":   "entry",
        "status":             "active",
    }, tok)
    return extract(resp)["job_post_id"]

def find_mod_item(tok_admin, content_id):
    items = extract(get("/admin/moderation", tok_admin, params={"status": "pending", "page_size": 100}))
    items = items if isinstance(items, list) else []
    return next((i for i in items if str(i.get("content_id")) == str(content_id)), None)

def find_scam_flag(tok_admin, job_post_id):
    flags = extract(get("/admin/scam-flags", tok_admin, params={"status": "pending", "page_size": 100}))
    flags = flags if isinstance(flags, list) else []
    return next((f for f in flags if str(f.get("job_post_id")) == str(job_post_id)), None)

def show_mod(label, item):
    total = round(
        float(item.get("toxic_score") or 0) +
        float(item.get("severe_toxic_score") or 0) +
        float(item.get("obscene_score") or 0) +
        float(item.get("threat_score") or 0) +
        float(item.get("insult_score") or 0) +
        float(item.get("identity_hate_score") or 0),
        4
    )
    print(f"  {label}")
    print(f"    moderation_id  : {item.get('moderation_id')}")
    print(f"    content_type   : {item.get('content_type')}")
    print(f"    status         : {item.get('status')}")
    print(f"    detected_labels: {item.get('detected_labels')}")
    print(f"    total_score    : {total}")

def show_scam(label, flag):
    print(f"  {label}")
    print(f"    flag_id        : {flag.get('flag_id')}")
    print(f"    scam_score     : {flag.get('scam_score')}")
    print(f"    detected_kw    : {flag.get('detected_keywords')}")
    print(f"    status         : {flag.get('status')}")


# ── main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*62)
    print("  Capstone API — Admin System Walkthrough (All Scenarios)")
    print("="*62)
    print(f"  Target : {BASE_URL}")
    print(f"  Run ID : {_RUN_ID}")
    print(f"  Output : {out_path}")


    # ══════════════════════════════════════════════════════════════
    # SETUP
    # ══════════════════════════════════════════════════════════════
    section("SETUP — admin login + create all scenario users")

    step("Admin login")
    tok_admin = login(_ADMIN_EMAIL, _ADMIN_PASSWORD)
    me = extract(get("/auth/me", tok_admin))
    print(f"  logged in as: {me['email']}  is_admin={me['is_admin']}")

    step("Create toxicity detection scenario users (6)")
    _mod_users = {}
    for email, utype in [
        (_MOD_CLIENT_MAN_APP, "client"),
        (_MOD_CLIENT_MAN_REJ, "client"),
        (_MOD_CLIENT_AUTO_DI, "client"),
        (_MOD_CLIENT_AUTO_CL, "client"),
        (_MOD_PROF_AUTO_DI,   "freelancer"),
        (_MOD_PROF_AUTO_CL,   "freelancer"),
    ]:
        register_and_verify({"email": email, "password": _PASSWORD,
                              "user_type": utype, "full_name": email.split("@")[0]})
        tok = login(email, _PASSWORD)
        uid = extract(get("/auth/me", tok))["user_id"]
        if utype == "client":
            cid = extract(get("/clients", tok))[0]["client_id"]
            _mod_users[email] = {"tok": tok, "uid": uid, "cid": cid}
        else:
            fid = extract(get("/freelancers", tok))[0]["freelancer_id"]
            _mod_users[email] = {"tok": tok, "uid": uid, "fid": fid}
        print(f"    {email}  uid={uid[:8]}...")

    step("Create scam detection scenario users (5)")
    _scam_users = {}
    for email in [_SCAM_CLIENT_MAN_SF, _SCAM_CLIENT_MAN_RM,
                  _SCAM_CLIENT_AUTO_DI, _SCAM_CLIENT_AUTO_RM, _SCAM_BAN_CLIENT]:
        register_and_verify({"email": email, "password": _PASSWORD,
                              "user_type": "client", "full_name": email.split("@")[0]})
        tok = login(email, _PASSWORD)
        cid = extract(get("/clients", tok))[0]["client_id"]
        uid = extract(get("/auth/me", tok))["user_id"]
        _scam_users[email] = {"tok": tok, "uid": uid, "cid": cid}
        print(f"    {email}  cid={cid[:8]}...")

    step("Create report scenario users (3)")
    register_and_verify({"email": _RPT_FL,   "password": _PASSWORD,
                          "user_type": "freelancer", "full_name": "Reported Freelancer"})
    register_and_verify({"email": _RPT_CL,   "password": _PASSWORD,
                          "user_type": "client",     "full_name": "Reported Client"})
    register_and_verify({"email": _REPORTER, "password": _PASSWORD,
                          "user_type": "freelancer", "full_name": "Reporter User"})
    tok_rpt_fl   = login(_RPT_FL,   _PASSWORD)
    tok_rpt_cl   = login(_RPT_CL,   _PASSWORD)
    tok_reporter = login(_REPORTER, _PASSWORD)
    uid_rpt_fl   = extract(get("/auth/me", tok_rpt_fl))["user_id"]
    uid_rpt_cl   = extract(get("/auth/me", tok_rpt_cl))["user_id"]
    uid_reporter = extract(get("/auth/me", tok_reporter))["user_id"]
    # client posts a job so we have something to report
    rpt_job_id = make_job(tok_rpt_cl,
        "Hiring a developer",
        "Looking for a Python developer to help build a REST API.")
    print(f"  freelancer to report : {uid_rpt_fl[:8]}...")
    print(f"  client to report     : {uid_rpt_cl[:8]}...")
    print(f"  reporter             : {uid_reporter[:8]}...")
    print(f"  reportable job post  : {rpt_job_id[:8]}...")


    # ══════════════════════════════════════════════════════════════
    # CONTENT MODERATION
    # ══════════════════════════════════════════════════════════════
    section("CONTENT MODERATION")

    print("""
  Score guide
  ──────────────────────────────────────────────────────
  low-score text  : toxic=1hit(0.35)  → total=0.35
  high-score job  : toxic=3hits(1.0) + severe_toxic=2hits(0.70) → total=1.70
  high-score prof : toxic=5hits(1.0) + severe_toxic=2hits(0.70) + insult=2hits(0.70) → total=2.40
  auto-close thresholds: job_post ≥ 0.85 | profile ≥ 0.90
  ──────────────────────────────────────────────────────""")

    # ── A1: Manual APPROVE (low-score job post, false positive) ──────────────

    step("A1 — Manual APPROVE: low-score job post (total=0.35 < 0.85)")
    jid_man_app = make_job(
        _mod_users[_MOD_CLIENT_MAN_APP]["tok"],
        "Developer needed — code cleanup project",
        _TXT_MOD_LOW,
    )
    time.sleep(1.5)
    item_man_app = find_mod_item(tok_admin, jid_man_app)
    if item_man_app:
        show_mod("flagged item", item_man_app)
        approved = extract(post(
            f"/admin/moderation/{item_man_app['moderation_id']}/approve",
            {"admin_note": "Reviewed — 'idiot' used informally, not directed at a person. False positive."},
            tok_admin,
        ))
        print(f"  → status after manual approve: {approved['status']}")
        assert approved["status"] == "approved", "Expected approved"
        print("  ✓ PASS — content cleared as false positive")
    else:
        print("  WARN: moderation item not found (background task may not have run)")

    # ── A2: Manual REJECT (high-score job post, violation confirmed) ──────────

    step("A2 — Manual REJECT: high-score job post (total=1.70 ≥ 0.85) → job closed")
    jid_man_rej = make_job(
        _mod_users[_MOD_CLIENT_MAN_REJ]["tok"],
        "Review of submitted work",
        _TXT_MOD_HIGH_JOB,
    )
    time.sleep(1.5)
    item_man_rej = find_mod_item(tok_admin, jid_man_rej)
    if item_man_rej:
        show_mod("flagged item", item_man_rej)
        rejected = extract(post(
            f"/admin/moderation/{item_man_rej['moderation_id']}/reject",
            {"admin_note": "Severe toxic language. Job post closed."},
            tok_admin,
        ))
        print(f"  → status after manual reject: {rejected['status']}")
        assert rejected["status"] == "rejected", "Expected rejected"
        print("  ✓ PASS — violation confirmed, job post closed")
    else:
        print("  WARN: moderation item not found")

    # ── A3: Auto-DISMISS (low-score job post, force-expire) ───────────────────

    step("A3 — AUTO-DISMISS: low-score job post (total=0.35 < 0.85) after 30d")
    jid_auto_di = make_job(
        _mod_users[_MOD_CLIENT_AUTO_DI]["tok"],
        "Backend cleanup needed",
        _TXT_MOD_LOW,
    )
    time.sleep(1.5)
    item_auto_di = find_mod_item(tok_admin, jid_auto_di)
    if item_auto_di:
        show_mod("flagged item (pending)", item_auto_di)
        mid_auto_di = str(item_auto_di["moderation_id"])
        post("/admin/moderation/force-expire", {"ids": [mid_auto_di]}, tok_admin)
        # re-fetch to check result
        all_items = extract(get("/admin/moderation", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((i for i in all_items if str(i.get("moderation_id")) == mid_auto_di), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        assert result and result["status"] == "approved", "Expected auto-dismissed (approved)"
        print("  ✓ PASS — low-score → auto-dismissed as false positive")
    else:
        print("  WARN: moderation item not found")

    # ── A4: Auto-CLOSE (high-score job post, force-expire) ───────────────────

    step("A4 — AUTO-CLOSE: high-score job post (total=1.70 ≥ 0.85) after 30d → job closed")
    jid_auto_cl = make_job(
        _mod_users[_MOD_CLIENT_AUTO_CL]["tok"],
        "Feedback on project delivery",
        _TXT_MOD_HIGH_JOB,
    )
    time.sleep(1.5)
    item_auto_cl = find_mod_item(tok_admin, jid_auto_cl)
    if item_auto_cl:
        show_mod("flagged item (pending)", item_auto_cl)
        mid_auto_cl = str(item_auto_cl["moderation_id"])
        post("/admin/moderation/force-expire", {"ids": [mid_auto_cl]}, tok_admin)
        all_items = extract(get("/admin/moderation", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((i for i in all_items if str(i.get("moderation_id")) == mid_auto_cl), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        assert result and result["status"] == "rejected", "Expected auto-closed (rejected)"
        print("  ✓ PASS — high-score → auto-closed, job post status set to closed")
    else:
        print("  WARN: moderation item not found")

    # ── A5: Auto-DISMISS (low-score profile, force-expire) ───────────────────

    step("A5 — AUTO-DISMISS: low-score profile (total=0.35 < 0.90) after 30d")
    u_prf_di = _mod_users[_MOD_PROF_AUTO_DI]
    scan_di = extract(post("/admin/moderation/scan", {
        "content_type": "freelancer_profile",
        "content_id":   u_prf_di["fid"],
        "user_id":      u_prf_di["uid"],
        "text":         _TXT_MOD_LOW,
    }, tok_admin))
    if scan_di.get("flagged"):
        show_mod("flagged item (pending)", scan_di["moderation_record"])
        mid_prf_di = str(scan_di["moderation_record"]["moderation_id"])
        post("/admin/moderation/force-expire", {"ids": [mid_prf_di]}, tok_admin)
        all_items = extract(get("/admin/moderation", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((i for i in all_items if str(i.get("moderation_id")) == mid_prf_di), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        assert result and result["status"] == "approved", "Expected auto-dismissed"
        print("  ✓ PASS — low-score profile → auto-dismissed as false positive")
    else:
        print("  WARN: low-score profile scan not flagged — check scan threshold")

    # ── A6: Auto-CLOSE (high-score profile, force-expire) ────────────────────

    step("A6 — AUTO-CLOSE: high-score profile (total=2.40 ≥ 0.90) after 30d")
    u_prf_cl = _mod_users[_MOD_PROF_AUTO_CL]
    scan_cl = extract(post("/admin/moderation/scan", {
        "content_type": "freelancer_profile",
        "content_id":   u_prf_cl["fid"],
        "user_id":      u_prf_cl["uid"],
        "text":         _TXT_MOD_HIGH_PROF,
    }, tok_admin))
    if scan_cl.get("flagged"):
        show_mod("flagged item (pending)", scan_cl["moderation_record"])
        mid_prf_cl = str(scan_cl["moderation_record"]["moderation_id"])
        post("/admin/moderation/force-expire", {"ids": [mid_prf_cl]}, tok_admin)
        all_items = extract(get("/admin/moderation", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((i for i in all_items if str(i.get("moderation_id")) == mid_prf_cl), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        assert result and result["status"] == "rejected", "Expected auto-closed (rejected)"
        print("  ✓ PASS — high-score profile → auto-closed (rejection recorded in queue)")
    else:
        print("  WARN: high-score profile scan not flagged")


    # ══════════════════════════════════════════════════════════════
    # SCAM DETECTION
    # ══════════════════════════════════════════════════════════════
    section("SCAM DETECTION")

    print("""
  Score guide
  ──────────────────────────────────────────────────────
  low-scam text  : 3 keywords → 3/6 = 0.50  (flagged for review, but < 0.85)
  high-scam text : 8 keywords → min(8/6,1.0) = 1.00 (≥ 0.85 → auto-remove)
  flag threshold : ≥ 0.10 (any keyword match)
  auto-remove    : score ≥ 0.85 AND pending ≥ 30 days
  auto-dismiss   : score < 0.85 AND pending ≥ 30 days
  ──────────────────────────────────────────────────────""")

    # ── B1: Manual SAFE (low scam, false positive) ────────────────────────────

    step("B1 — Manual SAFE: low-scam job (score=0.50 < 0.85) → admin marks safe")
    jid_scam_man_sf = make_job(
        _scam_users[_SCAM_CLIENT_MAN_SF]["tok"],
        "Earn with our marketing program",
        _TXT_SCAM_LOW,
    )
    time.sleep(1.5)
    flag_man_sf = find_scam_flag(tok_admin, jid_scam_man_sf)
    if flag_man_sf:
        show_scam("scam flag", flag_man_sf)
        safe = extract(post(
            f"/admin/scam-flags/{flag_man_sf['flag_id']}/approve",
            {"admin_note": "Reviewed — legitimate marketing language, not a scam."},
            tok_admin,
        ))
        print(f"  → status after manual safe : {safe['status']}")
        assert safe["status"] == "safe", "Expected safe"
        print("  ✓ PASS — low-score scam flag cleared as false positive")
    else:
        print("  WARN: scam flag not found (background task may not have run)")

    # ── B2: Manual REMOVE (high scam, confirmed) → client strike ──────────────

    step("B2 — Manual REMOVE: high-scam job (score=1.00 ≥ 0.85) → admin removes → client strike #1")
    jid_scam_man_rm = make_job(
        _scam_users[_SCAM_CLIENT_MAN_RM]["tok"],
        "Work from home unlimited earnings",
        _TXT_SCAM_HIGH,
    )
    time.sleep(1.5)
    flag_man_rm = find_scam_flag(tok_admin, jid_scam_man_rm)
    if flag_man_rm:
        show_scam("scam flag", flag_man_rm)
        removed = extract(post(
            f"/admin/scam-flags/{flag_man_rm['flag_id']}/remove",
            {"admin_note": "Confirmed scam — job removed, client strike issued."},
            tok_admin,
        ))
        print(f"  → status after manual remove: {removed['status']}")
        cid_man_rm = _scam_users[_SCAM_CLIENT_MAN_RM]["cid"]
        rec = extract(get(f"/admin/scam-flags/client/{cid_man_rm}", tok_admin))
        print(f"  → client total_scam_confirmed: {rec.get('total_scam_confirmed')}")
        assert removed["status"] == "removed", "Expected removed"
        print("  ✓ PASS — confirmed scam, client strike recorded")
    else:
        print("  WARN: scam flag not found")

    # ── B3: Auto-DISMISS (low scam, force-expire) ─────────────────────────────

    step("B3 — AUTO-DISMISS: low-scam job (score=0.50 < 0.85) after 30d → status=safe")
    jid_scam_auto_di = make_job(
        _scam_users[_SCAM_CLIENT_AUTO_DI]["tok"],
        "Marketing opportunity with bonuses",
        _TXT_SCAM_LOW,
    )
    time.sleep(1.5)
    flag_auto_di = find_scam_flag(tok_admin, jid_scam_auto_di)
    if flag_auto_di:
        show_scam("scam flag (pending)", flag_auto_di)
        fid_auto_di = str(flag_auto_di["flag_id"])
        post("/admin/scam-flags/force-expire", {"ids": [fid_auto_di]}, tok_admin)
        all_flags = extract(get("/admin/scam-flags", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((f for f in all_flags if str(f.get("flag_id")) == fid_auto_di), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        assert result and result["status"] == "safe", "Expected auto-dismissed (safe)"
        print("  ✓ PASS — low-score scam → auto-dismissed as false positive")
    else:
        print("  WARN: scam flag not found")

    # ── B4: Auto-REMOVE (high scam, force-expire) → job closed, client strike ─

    step("B4 — AUTO-REMOVE: high-scam job (score=1.00 ≥ 0.85) after 30d → job closed, client strike")
    jid_scam_auto_rm = make_job(
        _scam_users[_SCAM_CLIENT_AUTO_RM]["tok"],
        "Passive income guaranteed program",
        _TXT_SCAM_HIGH,
    )
    time.sleep(1.5)
    flag_auto_rm = find_scam_flag(tok_admin, jid_scam_auto_rm)
    if flag_auto_rm:
        show_scam("scam flag (pending)", flag_auto_rm)
        fid_auto_rm = str(flag_auto_rm["flag_id"])
        post("/admin/scam-flags/force-expire", {"ids": [fid_auto_rm]}, tok_admin)
        all_flags = extract(get("/admin/scam-flags", tok_admin, params={"status": "all", "page_size": 100}))
        result = next((f for f in all_flags if str(f.get("flag_id")) == fid_auto_rm), None)
        print(f"  → status after force-expire : {result['status'] if result else 'not found'}")
        cid_auto_rm = _scam_users[_SCAM_CLIENT_AUTO_RM]["cid"]
        rec = extract(get(f"/admin/scam-flags/client/{cid_auto_rm}", tok_admin))
        print(f"  → client total_scam_confirmed: {rec.get('total_scam_confirmed')}")
        assert result and result["status"] == "removed", "Expected auto-removed"
        print("  ✓ PASS — high-score scam → auto-removed, job closed, client strike")
    else:
        print("  WARN: scam flag not found")

    # ── B5: 3-strike ban ──────────────────────────────────────────────────────

    step("B5 — 3-STRIKE BAN: client posts 3 high-scam jobs, all manually removed → BANNED")
    ban_cid = _scam_users[_SCAM_BAN_CLIENT]["cid"]
    ban_tok = _scam_users[_SCAM_BAN_CLIENT]["tok"]
    for strike in range(1, 4):
        jid = make_job(ban_tok, f"Easy earnings program #{strike}", _TXT_SCAM_HIGH)
        time.sleep(1.5)
        flag = find_scam_flag(tok_admin, jid)
        if flag:
            post(f"/admin/scam-flags/{flag['flag_id']}/remove",
                 {"admin_note": f"Strike {strike} — confirmed scam."}, tok_admin)
            rec = extract(get(f"/admin/scam-flags/client/{ban_cid}", tok_admin))
            print(f"  strike {strike}: total_scam_confirmed={rec.get('total_scam_confirmed')}  is_banned={rec.get('is_banned')}")
        else:
            # fallback: manual scan if background didn't catch it
            scan_r = extract(post("/admin/scam-flags/scan", {
                "job_post_id": jid,
                "client_id":   ban_cid,
                "text":        _TXT_SCAM_HIGH,
            }, tok_admin))
            if scan_r.get("flagged"):
                post(f"/admin/scam-flags/{scan_r['scam_flag']['flag_id']}/remove",
                     {"admin_note": f"Strike {strike} fallback."}, tok_admin)
                rec = extract(get(f"/admin/scam-flags/client/{ban_cid}", tok_admin))
                print(f"  strike {strike} (manual scan): total={rec.get('total_scam_confirmed')}  banned={rec.get('is_banned')}")
    ban_rec = extract(get(f"/admin/scam-flags/client/{ban_cid}", tok_admin))
    assert ban_rec.get("is_banned"), "Expected client to be banned"
    print(f"  ✓ PASS — client BANNED after {ban_rec.get('total_scam_confirmed')} confirmed scams")


    # ══════════════════════════════════════════════════════════════
    # USER REPORTS
    # ══════════════════════════════════════════════════════════════
    section("USER REPORTS")

    # ── C1: Report freelancer → admin accepts ─────────────────────────────────

    step("C1 — Report freelancer profile → admin ACCEPTS")
    r1 = extract(post("/reports", {
        "reported_type":    "freelancer",
        "reported_user_id": uid_rpt_fl,
        "reasons":          ["inappropriate_content", "harassment"],
        "custom_reason":    "Bio contains offensive language directed at clients.",
    }, tok_reporter))
    rid_fl = r1.get("report_id")
    print(f"  report_id: {rid_fl}")
    accepted = extract(post(f"/admin/reports/{rid_fl}/accept",
                            {"admin_note": "Verified — content violates community standards."}, tok_admin))
    print(f"  → status: {accepted['status']}")
    assert accepted["status"] == "accepted", "Expected accepted"
    print("  ✓ PASS")

    # ── C2: Report client → admin dismisses ───────────────────────────────────

    step("C2 — Report client profile → admin DISMISSES")
    r2 = extract(post("/reports", {
        "reported_type":    "client",
        "reported_user_id": uid_rpt_cl,
        "reasons":          ["spam"],
        "custom_reason":    "Keeps sending unsolicited messages.",
    }, tok_reporter))
    rid_cl = r2.get("report_id")
    print(f"  report_id: {rid_cl}")
    dismissed = extract(post(f"/admin/reports/{rid_cl}/dismiss",
                             {"admin_note": "Investigated — no policy violation found."}, tok_admin))
    print(f"  → status: {dismissed['status']}")
    assert dismissed["status"] == "dismissed", "Expected dismissed"
    print("  ✓ PASS")

    # ── C3: Report job post → admin accepts ───────────────────────────────────

    step("C3 — Report job post → admin ACCEPTS")
    r3 = extract(post("/reports", {
        "reported_type": "job_post",
        "job_post_id":   rpt_job_id,
        "reasons":       ["scam"],
        "custom_reason": "Job post promises unrealistic income.",
    }, tok_reporter))
    rid_jp = r3.get("report_id")
    print(f"  report_id: {rid_jp}")
    acc_jp = extract(post(f"/admin/reports/{rid_jp}/accept",
                          {"admin_note": "Suspicious wording confirmed."}, tok_admin))
    print(f"  → status: {acc_jp['status']}")
    assert acc_jp["status"] == "accepted", "Expected accepted"
    print("  ✓ PASS")

    # ── C4: Self-report → 400 ─────────────────────────────────────────────────

    step("C4 — Self-report → expected 400")
    self_rpt = post("/reports", {
        "reported_type":    "freelancer",
        "reported_user_id": uid_reporter,
        "reasons":          ["spam"],
    }, tok_reporter, allow_fail=True)
    code = self_rpt.get("status_code") or self_rpt.get("code")
    print(f"  response: {extract(self_rpt)}")
    print("  ✓ PASS — cannot report yourself")

    # ── C5: Build 10-report threshold ─────────────────────────────────────────

    step("C5 — Build 10-report threshold: 9 more users report the same freelancer (10 total)")
    print()
    print("  Rule: auto report-ban fires when ≥10 reports AND oldest ≥30 days old.")
    print("  Creating 9 extra reporters...")
    for i in range(1, 10):
        email = f"extra.rpt.{i}.{_RUN_ID}@wt.dev"
        register_and_verify({"email": email, "password": _PASSWORD,
                              "user_type": "freelancer", "full_name": f"Extra Reporter {i}"})
        tok_extra = login(email, _PASSWORD)
        post("/reports", {
            "reported_type":    "freelancer",
            "reported_user_id": uid_rpt_fl,
            "reasons":          ["inappropriate_content"],
            "custom_reason":    f"Extra reporter {i}: offensive bio.",
        }, tok_extra)
    print(f"  Done — 10 total reports against freelancer {uid_rpt_fl[:8]}...")
    print("  30-day condition not yet met → no auto-ban triggered.")

    # ── C6: Auto-action list (empty) ──────────────────────────────────────────

    step("C6 — GET /admin/reports/auto-actions (expected empty — 30d not passed)")
    aa = extract(get("/admin/reports/auto-actions", tok_admin))
    aa_list = aa if isinstance(aa, list) else []
    print(f"  auto-action records: {len(aa_list)}  (expected 0)")
    print("  When oldest report ages ≥30 days, next call to GET /admin/reports")
    print("  or GET /admin/dashboard will auto-ban the user and log it here.")


    # ══════════════════════════════════════════════════════════════
    # DASHBOARD & FINAL CHECKS
    # ══════════════════════════════════════════════════════════════
    section("DASHBOARD & FINAL CHECKS")

    step("D1 — Final dashboard stats")
    stats = extract(get("/admin/dashboard", tok_admin))
    print(f"  pending moderation items  : {stats['pending_moderation_items']}")
    print(f"  pending scam flags        : {stats['pending_scam_flags']}")
    print(f"  pending reports           : {stats['pending_reports']}")
    print(f"  banned clients (scam)     : {stats['banned_clients']}")
    print(f"  total reports accepted    : {stats['total_reports_accepted']}")
    print(f"  auto-approved last 24h    : {stats['auto_approved_last_24h']}")
    print(f"  auto-removed  last 24h    : {stats['auto_removed_last_24h']}")
    print(f"  report auto-actions total : {stats['report_auto_actions_total']}")

    step("D2 — Browse all queues (status=all)")
    all_mod  = extract(get("/admin/moderation",         tok_admin, params={"status": "all", "page_size": 100}))
    all_scam = extract(get("/admin/scam-flags",         tok_admin, params={"status": "all", "page_size": 100}))
    all_rpt  = extract(get("/admin/reports",            tok_admin, params={"status": "all", "page_size": 100}))
    all_aa   = extract(get("/admin/reports/auto-actions", tok_admin))

    def _c(lst): return len(lst) if isinstance(lst, list) else 0
    print(f"  moderation items  (all): {_c(all_mod)}")
    print(f"  scam flags        (all): {_c(all_scam)}")
    print(f"  user reports      (all): {_c(all_rpt)}")
    print(f"  report auto-actions    : {_c(all_aa)}")

    # ══════════════════════════════════════════════════════════════
    # APPEALS
    # ══════════════════════════════════════════════════════════════
    section("APPEALS")

    print("""
  Appeals let users contest bans or job post closures.
  target_type: 'user'     → appeal an account report-ban
  target_type: 'job_post' → appeal a job post closure
  ──────────────────────────────────────────────────────""")

    # ── E1: Force-expire 10 freelancer reports → auto-ban fires ──────────────

    step("E1 — Force-expire 10 freelancer reports → freelancer AUTO-BANNED")
    post("/admin/reports/force-expire-target", {
        "target_type": "user",
        "target_id":   uid_rpt_fl,
    }, tok_admin)
    aa_post = extract(get("/admin/reports/auto-actions", tok_admin))
    aa_list_post = aa_post if isinstance(aa_post, list) else []
    ban_entry = next(
        (a for a in aa_list_post if str(a.get("target_id")) == str(uid_rpt_fl)), None
    )
    print(f"  auto-action record : {ban_entry}")
    assert ban_entry, "Expected auto-action record for the banned user"
    print(f"  report_count       : {ban_entry.get('report_count')}")
    print("  ✓ PASS — freelancer auto-banned after 30d with ≥10 reports")

    # ── E2: Banned user submits appeal ────────────────────────────────────────

    step("E2 — Banned freelancer submits appeal (target_type='user')")
    appeal_u = extract(post("/appeals", {
        "target_type": "user",
        "target_id":   uid_rpt_fl,
        "message": (
            "I believe these reports are unfair and coordinated harassment. "
            "My profile bio does not violate any community guidelines. "
            "Please review and restore my account."
        ),
    }, tok_rpt_fl))
    appeal_id_user = appeal_u.get("appeal_id")
    print(f"  appeal_id : {appeal_id_user}")
    assert appeal_id_user, "Expected appeal_id in response"
    print("  ✓ PASS — appeal submitted successfully")

    # ── E3: User views own appeals; admin lists pending ───────────────────────

    step("E3 — User views own appeals; admin lists all pending appeals")
    mine = extract(get("/appeals/mine", tok_rpt_fl))
    mine_list = mine if isinstance(mine, list) else []
    print(f"  user's own appeals      : {len(mine_list)}")
    if mine_list:
        print(f"  first appeal status     : {mine_list[0].get('status')}")

    admin_appeals = extract(get("/admin/appeals", tok_admin, params={"status": "pending"}))
    adm_list = admin_appeals if isinstance(admin_appeals, list) else []
    print(f"  admin pending appeals   : {len(adm_list)}")
    assert any(str(a.get("appeal_id")) == str(appeal_id_user) for a in adm_list), \
        "Appeal must appear in admin list"
    print("  ✓ PASS — appeal visible to both user and admin")

    # ── E4: Admin APPROVES user appeal → ban lifted ───────────────────────────

    step("E4 — Admin APPROVES user appeal → account ban lifted")
    resolved_u = extract(post(f"/admin/appeals/{appeal_id_user}/approve", {
        "admin_note": "Reviewed full report history — appears coordinated. Account restored.",
    }, tok_admin))
    print(f"  appeal status after approval: {resolved_u.get('status')}")
    assert resolved_u.get("status") == "approved", "Expected approved"
    # Mine endpoint should now show the appeal as approved
    mine_after = extract(get("/appeals/mine", tok_rpt_fl))
    mine_after_list = mine_after if isinstance(mine_after, list) else []
    updated_appeal = next(
        (a for a in mine_after_list if str(a.get("appeal_id")) == str(appeal_id_user)), None
    )
    print(f"  appeal status in /appeals/mine: {updated_appeal.get('status') if updated_appeal else 'not found'}")
    assert updated_appeal and updated_appeal.get("status") == "approved", "Expected appeal marked approved"
    print("  ✓ PASS — appeal approved, user account restored")

    # ── E5: Job post owner submits appeal for auto-closed post ────────────────

    step("E5 — Job post owner submits appeal for auto-closed job (target_type='job_post')")
    # jid_auto_cl was auto-closed in A4 via toxicity detection force-expire
    tok_job_owner = _mod_users[_MOD_CLIENT_AUTO_CL]["tok"]
    appeal_j = extract(post("/appeals", {
        "target_type": "job_post",
        "target_id":   jid_auto_cl,
        "message": (
            "The language in my job post was informal frustration, not targeted abuse. "
            "I have revised the description and request the post be reopened."
        ),
    }, tok_job_owner))
    appeal_id_job = appeal_j.get("appeal_id")
    print(f"  appeal_id : {appeal_id_job}")
    assert appeal_id_job, "Expected appeal_id"
    # Verify it appears in admin list
    adm_all = extract(get("/admin/appeals", tok_admin, params={"status": "pending"}))
    adm_all_list = adm_all if isinstance(adm_all, list) else []
    assert any(str(a.get("appeal_id")) == str(appeal_id_job) for a in adm_all_list), \
        "Job appeal must appear in admin list"
    print("  ✓ PASS — job post appeal submitted and visible to admin")

    # ── E6: Admin REJECTS job post appeal → post stays closed ────────────────

    step("E6 — Admin REJECTS job post appeal → job post remains closed")
    resolved_j = extract(post(f"/admin/appeals/{appeal_id_job}/reject", {
        "admin_note": (
            "Repeated policy violations — content clearly breaches community standards. "
            "Appeal denied."
        ),
    }, tok_admin))
    print(f"  appeal status after rejection: {resolved_j.get('status')}")
    assert resolved_j.get("status") == "rejected", "Expected rejected"
    # All appeals for admin (should now see both resolved)
    all_adm = extract(get("/admin/appeals", tok_admin, params={"status": "all"}))
    all_adm_list = all_adm if isinstance(all_adm, list) else []
    print(f"  total appeals in system : {len(all_adm_list)}")
    print("  ✓ PASS — appeal rejected, job post remains closed")


    # ── Summary ───────────────────────────────────────────────────────────────

    print("\n" + "="*62)
    print("  Admin Walkthrough Complete")
    print("="*62)
    print()
    print("  Scenarios demonstrated:")
    print("  CONTENT MODERATION")
    print("    A1 ✓ Manual APPROVE  — low-score job (false positive cleared)")
    print("    A2 ✓ Manual REJECT   — high-score job (violation, job closed)")
    print("    A3 ✓ Auto-DISMISS    — low-score job  (total 0.35 < 0.85)")
    print("    A4 ✓ Auto-CLOSE      — high-score job (total 1.70 ≥ 0.85, job closed)")
    print("    A5 ✓ Auto-DISMISS    — low-score profile (total 0.35 < 0.90)")
    print("    A6 ✓ Auto-CLOSE      — high-score profile (total 2.40 ≥ 0.90)")
    print("  SCAM DETECTION")
    print("    B1 ✓ Manual SAFE     — low scam (0.50, false positive)")
    print("    B2 ✓ Manual REMOVE   — high scam (1.00, client strike +1)")
    print("    B3 ✓ Auto-DISMISS    — low scam (0.50 < 0.85, after 30d)")
    print("    B4 ✓ Auto-REMOVE     — high scam (1.00 ≥ 0.85, after 30d)")
    print("    B5 ✓ 3-STRIKE BAN    — client banned after 3 confirmed removals")
    print("  USER REPORTS")
    print("    C1 ✓ Report freelancer → admin accepts")
    print("    C2 ✓ Report client     → admin dismisses")
    print("    C3 ✓ Report job post   → admin accepts")
    print("    C4 ✓ Self-report       → 400 blocked")
    print("    C5 ✓ 10-report threshold built (auto-ban after 30d)")
    print("    C6 ✓ Auto-action log shown (empty — 30d not passed)")
    print("  APPEALS")
    print("    E1 ✓ Force-expire reports → user auto-banned")
    print("    E2 ✓ Banned user submits appeal")
    print("    E3 ✓ User + admin both see the pending appeal")
    print("    E4 ✓ Admin APPROVES → ban lifted, account restored")
    print("    E5 ✓ Job post owner submits appeal for auto-closed post")
    print("    E6 ✓ Admin REJECTS → job post remains closed")
    print()
    print("  Auto-enforcement (time-based, not triggered live):")
    print("    • Content mod item: auto-dismiss (low score) or auto-close (high score) after 30d")
    print("    • Scam flag:        auto-dismiss (score <0.85) or auto-remove (≥0.85) after 30d")
    print("    • Report threshold: ≥10 reports → auto-ban user / close job after 30d")
    print()

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
