"""
Comprehensive platform walkthrough — ALL features, ALL routes.

Covers:
  1.  Auth: register / verify / login / add-role (dual-role user)
  2.  Freelancer profile: bio, rate, skills, specialities, languages, work-exp, education, portfolio
  3.  CV Upload & Analysis: DOCX → ATS check + profile match + LLM recommendations
  4.  Job post setup: post + role + required skills (CLIENT and DUAL_ROLE)
  5.  Discovery: search, browse, saved-jobs
  6.  Embeddings: trigger + verify (freelancer + job)
  7.  Job Matching — Stage 1 cosine / Stage 2 skill overlap / Stage 3 CatBoost ML + SHAP
  8.  RAG job-fit analysis
  9.  Proposal: submit / view / accept
  10. Contract: create / messages / read
  11. Work submission: submit / revision request / re-submit / approve
  12. Review: AI question → submit ratings → AI pipeline → trust score + red flags
  13. Dashboard: freelancer + client
  14. Performance ratings + client trust score

Requirements on the server:
  APP_ENV=development  and  SHOW_DEV_OTP=true   (OTP returned in register response)

Usage:
    python walkthrough/walkthrough-new.py
    python walkthrough/walkthrough-new.py --embed-wait 12
    python walkthrough/walkthrough-new.py --skip-analyse
    python walkthrough/walkthrough-new.py --skip-cv
"""

import argparse
import datetime
import io
import json
import os
import sys
import time

import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "SecurePass123!"
DEFAULT_EMBED_WAIT = 8
DEFAULT_REVIEW_WAIT = 6


# ── Tee: mirror stdout to a timestamped file ──────────────────────────────────

class _Tee:
    def __init__(self, path: str):
        self._stdout = sys.stdout
        self._file = open(path, "w", encoding="utf-8")

    def write(self, s: str):
        self._stdout.write(s)
        self._file.write(s)

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
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"walkthrough_{ts}.md")
    tee = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee, path):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved → {path}")


# ── Section / step tracking ───────────────────────────────────────────────────

_sec = 0
_stp = 0


def section(title: str):
    global _sec, _stp
    _sec += 1
    _stp = 0
    print(f"\n{'═' * 70}")
    print(f"  ◆  SECTION {_sec}: {title}")
    print(f"{'═' * 70}")


def step(title: str):
    global _stp
    _stp += 1
    print(f"\n  {'─' * 60}")
    print(f"  Step {_sec}.{_stp}: {title}")
    print(f"  {'─' * 60}")


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _ex(resp: dict):
    """Unwrap ResponseSchema envelope."""
    return resp.get("details", resp)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _die(tag: str, status: int, body: dict):
    print(f"  ✗ {tag} [{status}] FAILED")
    print(json.dumps(body, indent=2, default=str)[:600])
    sys.exit(1)


def post(endpoint: str, body: dict, token: str = None, expected: set = None,
         timeout: int = 60, label: str = None) -> dict:
    expected = expected or {200, 201}
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"POST {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


def post_mp(endpoint: str, files, data: dict = None, token: str = None,
            expected: set = None, timeout: int = 60, label: str = None) -> dict:
    """POST multipart/form-data (file uploads). `files` may be dict or list of tuples."""
    expected = expected or {200, 201}
    headers = _hdr(token) if token else {}
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data or {},
                      headers=headers, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"POST {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        print(f"    ↳ {json.dumps(payload, default=str)[:300]}")
    return payload


def get(endpoint: str, token: str = None, expected: set = None,
        timeout: int = 120, label: str = None, params: dict = None) -> dict:
    expected = expected or {200}
    r = requests.get(f"{BASE_URL}{endpoint}", headers=_hdr(token) if token else {},
                     params=params, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"GET  {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


def put(endpoint: str, body: dict, token: str, expected: set = None,
        label: str = None) -> dict:
    expected = expected or {200}
    r = requests.put(f"{BASE_URL}{endpoint}", json=body, headers=_hdr(token), timeout=60)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"PUT  {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


def put_form(endpoint: str, data: dict, token: str, expected: set = None,
             label: str = None) -> dict:
    expected = expected or {200}
    r = requests.put(f"{BASE_URL}{endpoint}", data=data, headers=_hdr(token), timeout=60)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"PUT  {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


def patch(endpoint: str, params: dict = None, body: dict = None, token: str = None,
          expected: set = None, label: str = None) -> dict:
    expected = expected or {200}
    r = requests.patch(f"{BASE_URL}{endpoint}", params=params, json=body,
                       headers=_hdr(token) if token else {}, timeout=60)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok = r.status_code in expected
    tag = label or f"PATCH {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


# ── Catalog search helpers ────────────────────────────────────────────────────

def _search_results(resp: dict) -> list:
    """Extract the results list from a search response envelope."""
    data = _ex(resp)
    if isinstance(data, list):
        return data
    return data.get("results", [])


def _find_skill(name: str, token: str) -> dict | None:
    """Search /skills/search?name=... and return first exact match (case-insensitive)."""
    resp = get("/skills/search", token, expected={200}, label=f"    Search skill: {name}", params={"name": name})
    for item in _search_results(resp):
        if (item.get("skill_name") or "").lower() == name.lower():
            return item
    return None


def _find_speciality(name: str, token: str) -> dict | None:
    resp = get("/specialities/search", token, expected={200}, label=f"    Search speciality: {name}", params={"name": name})
    for item in _search_results(resp):
        if (item.get("speciality_name") or "").lower() == name.lower():
            return item
    return None


def _find_language(name: str, token: str) -> dict | None:
    resp = get("/languages/search", token, expected={200}, label=f"    Search language: {name}", params={"name": name})
    for item in _search_results(resp):
        if (item.get("language_name") or "").lower() == name.lower():
            return item
    return None


# ── Auth helper ───────────────────────────────────────────────────────────────

def register_and_verify(email: str, user_type: str, full_name: str) -> str:
    reg = post("/auth/register", {
        "email": email, "password": PASSWORD,
        "user_type": user_type, "full_name": full_name,
    }, expected={201}, label=f"  Register {user_type} ({full_name})")
    otp = _ex(reg).get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": email, "otp": otp},
             expected={200}, label="  Verify OTP")
    else:
        print("    No dev OTP — set APP_ENV=development and SHOW_DEV_OTP=true on the server.")
        otp = input("    Enter OTP: ").strip()
        post("/auth/verify-email", {"email": email, "otp": otp}, expected={200})
    login = post("/auth/login", {"email": email, "password": PASSWORD},
                 expected={200}, label="  Login")
    return _ex(login)["access_token"]


# ── CV / submission file helpers ──────────────────────────────────────────────

_CV_PDF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Angelica Suti Whiharto_CV.pdf")


def _make_submission_docx(content: str) -> bytes:
    """Build a minimal DOCX in memory for contract submission test files."""
    try:
        from docx import Document
        doc = Document()
        doc.add_paragraph(content)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    except Exception as e:
        print(f"    WARNING: could not build submission DOCX ({e})")
        return b"%PDF-1.4 fake"


# ── Job-match display ─────────────────────────────────────────────────────────

def _bar(value: float, width: int = 20) -> str:
    filled = min(width, int(value * width))
    return "█" * filled + "░" * (width - filled)


def _print_match(m: dict, target_id: str):
    title = (m.get("job_title") or "?")[:55]
    jid = str(m.get("job_post_id", "?"))
    flag = "  ← TARGET" if jid == str(target_id) else ""
    print(f"\n    ┌─ {title}{flag}")
    print(f"    │  job_post_id     : {jid}")

    cosine = m.get("similarity_score")
    if cosine is not None:
        print(f"    │  Stage 1 cosine  : {cosine:.4f}  [{_bar(cosine)}]  (pgvector ANN)")

    overlap = m.get("skill_overlap_pct")
    if overlap is not None:
        gate = "✓ pass" if overlap >= 20 else "✗ filtered"
        print(f"    │  Stage 2 overlap : {overlap:5.1f}%  [{_bar(overlap/100)}]  ({gate}, threshold ≥20%)")

    ml = m.get("match_probability")
    if ml is not None:
        delta = (ml / 100) - (cosine or 0)
        effect = f"↑ +{delta:.3f}" if delta > 0 else f"↓ {delta:.3f}"
        print(f"    │  Stage 3 CatBoost: {ml:5.1f}%  [{_bar(ml/100)}]  re-rank {effect}")
        print(f"    │    (model: CatBoost gradient boosting, AUC 0.8054)")

    for r in (m.get("match_reasons") or []):
        c = r.get("contribution", 0)
        arrow = "▲" if c > 0 else "▼"
        sign = "+" if c > 0 else ""
        print(f"    │  SHAP  {arrow} [{sign}{c:.4f}]  {r.get('label', '?')}")
    for r in (m.get("penalty_reasons") or []):
        c = r.get("contribution", 0)
        print(f"    │  SHAP  ▼ [{c:.4f}]  {r.get('label', '?')}  ← dragging score down")

    print(f"    └{'─' * 55}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(embed_wait: int, skip_analyse: bool, skip_cv: bool):
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    c_email  = f"wt.client.{ts}@test.dev"
    fl_email = f"wt.freelancer.{ts}@test.dev"
    du_email = f"wt.dual.{ts}@test.dev"

    print("\n" + "═" * 70)
    print("  CAPSTONE — Comprehensive Platform Walkthrough")
    print("═" * 70)
    print(f"  Server      : {BASE_URL}")
    print(f"  Client      : {c_email}")
    print(f"  Freelancer  : {fl_email}")
    print(f"  Dual-role   : {du_email}")
    print(f"  embed-wait  : {embed_wait}s  |  skip-analyse: {skip_analyse}  |  skip-cv: {skip_cv}")

    # ══════════════════════════════════════════════════════════════════════════
    section("AUTH — Register, Verify, Login, Add-Role")
    # ══════════════════════════════════════════════════════════════════════════

    step("Register CLIENT")
    c_tok = register_and_verify(c_email, "client", "Walkthrough Client")
    c_me = _ex(get("/auth/me", c_tok))
    client_id = c_me["client_id"]
    print(f"    client_id  : {client_id}")

    step("Register FREELANCER")
    fl_tok = register_and_verify(fl_email, "freelancer", "Alex Developer")
    fl_me = _ex(get("/auth/me", fl_tok))
    freelancer_id = fl_me["freelancer_id"]
    print(f"    freelancer_id : {freelancer_id}")

    step("Register DUAL_ROLE user (starts as freelancer, will gain client role)")
    du_tok = register_and_verify(du_email, "freelancer", "Dual Role User")
    du_me = _ex(get("/auth/me", du_tok))
    du_fl_id = du_me["freelancer_id"]
    print(f"    freelancer_id : {du_fl_id}  (role 1 of 2)")

    step("Add CLIENT role to DUAL_ROLE user via POST /auth/add-role")
    post("/auth/add-role", {"role": "client", "full_name": "Dual Role Client"},
         token=du_tok, expected={200, 201}, label="  POST /auth/add-role")
    du_me2 = _ex(get("/auth/me", du_tok))
    du_cl_id = du_me2.get("client_id")
    print(f"    client_id     : {du_cl_id}  (role 2 of 2)")
    print(f"    freelancer_id : {du_me2.get('freelancer_id')}")
    print(f"    → Single user now holds BOTH freelancer and client roles simultaneously")

    # ══════════════════════════════════════════════════════════════════════════
    section("FREELANCER PROFILE — Skills, Specialities, Languages, Work Exp, Education, Portfolio")
    # ══════════════════════════════════════════════════════════════════════════

    step("Update freelancer bio and rate")
    fl_upd = put_form(f"/freelancers/{freelancer_id}", {
        "full_name": "Alex Developer",
        "bio": (
            "Full-stack Python developer with 5 years experience building scalable REST APIs "
            "with FastAPI and PostgreSQL. Increased API throughput by 40% through caching. "
            "Delivered 15+ projects for 30+ clients in fintech and e-commerce. "
            "Strong communicator, deadline-driven, and expert in Docker and CI/CD pipelines."
        ),
        "estimated_rate": "5000000",
        "rate_time": "monthly",
        "rate_currency": "IDR",
    }, token=fl_tok)
    fl = _ex(fl_upd)
    print(f"    name : {fl.get('full_name')}")
    print(f"    rate : {fl.get('estimated_rate')} {fl.get('rate_currency')}/{fl.get('rate_time')}")

    step("Search & add 5 skills to freelancer profile")
    want_skills = {"Python": "expert", "FastAPI": "expert",
                   "PostgreSQL": "advanced", "React": "intermediate", "Docker": "intermediate"}
    added_skills = []
    skill_catalog: dict[str, dict] = {}   # name → skill row (for reuse in job-role-skills)
    for name, level in want_skills.items():
        sk = _find_skill(name, fl_tok)
        if sk:
            skill_catalog[name] = sk
            post("/freelancer-skills", {
                "freelancer_id": freelancer_id,
                "skill_id": sk["skill_id"],
                "proficiency_level": level,
            }, token=fl_tok, expected={200, 201}, label=f"      Add: {name} ({level})")
            added_skills.append(name)
        else:
            print(f"      Skill '{name}' not found in catalog — skipping")
    print(f"    Added: {', '.join(added_skills) or '(none found)'}")

    step("Search & add specialities to freelancer profile")
    want_specs = {"Web Development": False, "Backend Development": True}
    added_specs = []
    for name, is_primary in want_specs.items():
        sp = _find_speciality(name, fl_tok)
        if sp:
            post("/freelancer-specialities", {
                "freelancer_id": freelancer_id,
                "speciality_id": sp["speciality_id"],
                "is_primary": is_primary,
            }, token=fl_tok, expected={200, 201}, label=f"      Add: {name} (primary={is_primary})")
            added_specs.append(name)
        else:
            print(f"      Speciality '{name}' not found in catalog — skipping")
    print(f"    Added: {', '.join(added_specs) or '(none found)'}")

    step("Search & add languages to freelancer profile")
    want_langs = {"English": "fluent", "Indonesian": "native"}
    added_langs = []
    for name, level in want_langs.items():
        lg = _find_language(name, fl_tok)
        if lg:
            post("/freelancer-languages", {
                "freelancer_id": freelancer_id,
                "language_id": lg["language_id"],
                "proficiency_level": level,
            }, token=fl_tok, expected={200, 201}, label=f"      Add: {name} ({level})")
            added_langs.append(name)
        else:
            print(f"      Language '{name}' not found in catalog — skipping")
    print(f"    Added: {', '.join(added_langs) or '(none found)'}")

    step("Add 2 work experience records")
    we1 = _ex(post("/work-experiences", {
        "freelancer_id": freelancer_id,
        "job_title": "Backend Developer",
        "company_name": "Tokopedia",
        "start_date": "2021-06-01",
        "end_date": "2023-08-31",
        "is_current": False,
        "description": (
            "Built REST APIs with FastAPI + PostgreSQL serving 100K+ daily users. "
            "Reduced query latency by 35%. Led payments microservice team of 3."
        ),
    }, token=fl_tok, expected={200, 201}))
    print(f"    WE 1: {we1.get('job_title')} at {we1.get('company_name')}")

    we2 = _ex(post("/work-experiences", {
        "freelancer_id": freelancer_id,
        "job_title": "Junior Python Developer",
        "company_name": "Bukalapak",
        "start_date": "2019-01-01",
        "end_date": "2021-05-31",
        "is_current": False,
        "description": "Maintained product catalog service. Raised test coverage 45% → 80%.",
    }, token=fl_tok, expected={200, 201}))
    print(f"    WE 2: {we2.get('job_title')} at {we2.get('company_name')}")

    step("Add education")
    edu = _ex(post("/educations", {
        "freelancer_id": freelancer_id,
        "degree": "Bachelor of Computer Science",
        "field_of_study": "Computer Science",
        "institution_name": "Universitas Indonesia",
        "start_date": "2017-08-01",
        "end_date": "2021-07-31",
    }, token=fl_tok, expected={200, 201}))
    print(f"    {edu.get('degree')} — {edu.get('institution_name')}")

    step("Add portfolio project")
    port = _ex(post("/portfolios", {
        "freelancer_id": freelancer_id,
        "project_title": "E-Commerce REST API Platform",
        "project_description": (
            "Production-ready e-commerce backend with FastAPI, PostgreSQL, and Redis. "
            "Handles catalog, cart, payments, and order management for 50K+ SKUs."
        ),
        "completion_date": "2023-03-01",
    }, token=fl_tok, expected={200, 201}))
    print(f"    {port.get('project_title')}")

    step("View complete freelancer profile")
    profile = _ex(get(f"/freelancers/{freelancer_id}/profile", fl_tok))
    _fl_data = profile.get('freelancer') or {}
    print(f"    name       : {_fl_data.get('full_name')}")
    print(f"    bio chars  : {len(_fl_data.get('bio') or '')}")
    print(f"    skills     : {len(profile.get('skills') or [])}")
    print(f"    work exp   : {len(profile.get('work_experience') or [])}")
    print(f"    education  : {len(profile.get('education') or [])}")
    print(f"    portfolio  : {len(profile.get('portfolio') or [])}")

    # ══════════════════════════════════════════════════════════════════════════
    section("CV UPLOAD & ANALYSIS")
    # ══════════════════════════════════════════════════════════════════════════

    if skip_cv:
        step("CV upload — SKIPPED (--skip-cv)")
    else:
        step("Load CV: Angelica Suti Whiharto_CV.pdf from walkthrough folder")
        if not os.path.exists(_CV_PDF_PATH):
            print(f"    CV file not found: {_CV_PDF_PATH}")
            print("    Skipping CV upload — place the PDF in the walkthrough/ folder to enable this step.")
        else:
            with open(_CV_PDF_PATH, "rb") as _f:
                cv_bytes = _f.read()
            print(f"    PDF loaded: {len(cv_bytes):,} bytes")

            step("POST /cv_upload — extract text → compare profile → ATS check → LLM recs")
            cv_resp = post_mp(
                "/cv_upload",
                files={"file": ("Angelica Suti Whiharto_CV.pdf", cv_bytes, "application/pdf")},
                token=fl_tok,
                expected={200, 201},
                timeout=120,
                label="POST /cv_upload",
            )
            d = _ex(cv_resp)
            if not isinstance(d, dict):
                print(f"    CV response error: {str(d)[:300]}")
            else:
                sim  = d.get("similarity_score") or 0
                cov  = d.get("skill_coverage")
                ats  = d.get("ats_score") or 0
                scoring = d.get("scoring") or "n/a"
                print(f"\n    ┌─ CV ANALYSIS RESULTS")
                print(f"    │  File URL     : ...{str(d.get('file_url',''))[-40:]}")
                print(f"    │  Overall score: {str(scoring).upper()}")
                print(f"    │  Similarity   : {sim:.4f}  [{_bar(sim)}]  (CV ↔ profile, SentenceTransformer)")
                if cov is not None:
                    print(f"    │  Skill cover  : {cov:.1%}    [{_bar(cov)}]  ({len(d.get('matched_skills',[]))} matched)")
                print(f"    │  ATS score    : {ats}/100   [{_bar(ats/100)}]  (rule-based)")
                if d.get("matched_skills"):
                    print(f"    │  ✓ Matched    : {', '.join(d['matched_skills'][:8])}")
                if d.get("missing_skills"):
                    print(f"    │  ✗ Missing    : {', '.join(d['missing_skills'][:8])}")
                for flag in (d.get("ats_flags") or []):
                    print(f"    │  ⚠  {flag}")
                for i, rec in enumerate((d.get("recommendations") or []), 1):
                    print(f"    │  {i}. {str(rec)[:120]}")
                print(f"    └{'─' * 55}")
                if not scoring or scoring == "n/a":
                    print(f"    NOTE: 'scoring' field missing — raw keys: {list(d.keys())}")

    # ══════════════════════════════════════════════════════════════════════════
    section("JOB POST SETUP — CLIENT and DUAL_ROLE")
    # ══════════════════════════════════════════════════════════════════════════

    step("CLIENT creates job post")
    jp = _ex(post("/job-posts", {
        "job_title": "Senior Python/FastAPI Backend Developer",
        "job_description": (
            "We need an experienced Python backend developer to design and maintain our REST API "
            "using FastAPI and PostgreSQL. Own the backend end-to-end: async Python, JWT auth, "
            "Docker-based CI/CD, payment and email integrations, scalable DB schemas."
        ),
        "project_type": "individual",
        "estimated_duration": "3 months",
        "working_days": 22,
        "experience_level": "entry",
        "status": "active",
    }, token=c_tok, expected={200, 201}))
    job_post_id = jp["job_post_id"]
    print(f"    job_post_id : {job_post_id}")
    print(f"    title       : {jp.get('job_title')}")

    step("CLIENT creates job role")
    jr = _ex(post("/job-roles", {
        "job_post_id": job_post_id,
        "role_title": "Backend Developer",
        "role_budget": 15000000.00,
        "budget_currency": "IDR",
        "budget_type": "fixed",
        "role_description": "Owns API layer end-to-end: design, implementation, testing, deployment.",
        "positions_available": 1,
        "is_required": True,
    }, token=c_tok, expected={200, 201}))
    job_role_id = jr["job_role_id"]
    print(f"    job_role_id : {job_role_id}  | budget: {jr.get('role_budget')} {jr.get('budget_currency')}")

    step("CLIENT adds required skills to job role")
    jrs_added = []
    for name in ["Python", "FastAPI", "PostgreSQL", "Docker"]:
        sk = skill_catalog.get(name) or _find_skill(name, c_tok)
        if sk:
            post("/job-role-skills", {
                "job_role_id": job_role_id,
                "skill_id": sk["skill_id"],
                "is_required": True,
                "importance_level": "required",
            }, token=c_tok, expected={200, 201}, label=f"      Add required skill: {name}")
            jrs_added.append(name)
        else:
            print(f"      Skill '{name}' not found — skipping")
    print(f"    Required skills: {', '.join(jrs_added)}")

    step("DUAL_ROLE user creates a second job post (acting as CLIENT)")
    jp2 = _ex(post("/job-posts", {
        "job_title": "React Frontend Developer",
        "job_description": (
            "Looking for a React developer for a modern dashboard UI. "
            "Must know Redux/Zustand, REST API integration, responsive design, and Jest."
        ),
        "project_type": "individual",
        "estimated_duration": "2 months",
        "experience_level": "intermediate",
        "status": "active",
    }, token=du_tok, expected={200, 201}))
    job_post2_id = jp2["job_post_id"]
    print(f"    job_post_id : {job_post2_id}  (posted by dual-role user as client)")
    print(f"    title       : {jp2.get('job_title')}")
    print(f"    → Same account has freelancer_id={du_fl_id} AND client_id={du_cl_id}")

    # ══════════════════════════════════════════════════════════════════════════
    section("DISCOVERY — Browse, Search, Saved Jobs")
    # ══════════════════════════════════════════════════════════════════════════

    step("Search job posts for 'Python'")
    try:
        results = _ex(get("/job-posts/search", fl_tok, expected={200}, params={"name": "Python"}))
        count = len(results) if isinstance(results, list) else "?"
        print(f"    {count} job(s) matching 'Python'")
    except SystemExit:
        print("    Search endpoint not available — skipping")

    step("FREELANCER saves the job")
    saved_job_id = None
    try:
        sv = _ex(post("/saved-jobs", {
            "freelancer_id": freelancer_id,
            "job_post_id": job_post_id,
        }, token=fl_tok, expected={200, 201}))
        saved_job_id = sv.get("saved_job_id")
        print(f"    saved_job_id : {saved_job_id}")
        sv_list = _ex(get(f"/saved-jobs/freelancer/{freelancer_id}", fl_tok))
        sv_list = sv_list if isinstance(sv_list, list) else []
        print(f"    Saved jobs   : {len(sv_list)}")
    except Exception as e:
        print(f"    Saved jobs skipped: {e}")

    # ══════════════════════════════════════════════════════════════════════════
    section("EMBEDDINGS — Trigger, Wait, Verify")
    # ══════════════════════════════════════════════════════════════════════════

    step("Trigger freelancer embedding")
    post(f"/ai/job_matching/embed/freelancer/{freelancer_id}", {},
         token=fl_tok, expected={200, 201, 202})

    step("Trigger job embeddings (both job posts)")
    post(f"/ai/job_matching/embed/job/{job_post_id}", {},
         token=c_tok, expected={200, 201, 202})
    post(f"/ai/job_matching/embed/job/{job_post2_id}", {},
         token=du_tok, expected={200, 201, 202})

    print(f"\n  Waiting {embed_wait}s for background embedding workers...")
    time.sleep(embed_wait)

    step("Verify freelancer embedding in DB")
    emb = _ex(get(f"/freelancer-embeddings/freelancer/{freelancer_id}", fl_tok, expected={200}))
    if emb and emb.get("embedding_id"):
        print(f"    embedding_id  : {emb['embedding_id']}")
        print(f"    source_text   : {len(emb.get('source_text') or '')} chars")
        print(f"    vector dims   : {len(emb.get('embedding_vector') or [])}")
    else:
        print("    No embedding yet — try --embed-wait 15")

    step("Verify job embedding in DB")
    jemb = _ex(get(f"/job-embeddings/job-post/{job_post_id}", c_tok, expected={200}))
    if jemb and jemb.get("embedding_id"):
        print(f"    embedding_id  : {jemb['embedding_id']}")
        print(f"    vector dims   : {len(jemb.get('embedding_vector') or [])}")
    else:
        print("    No job embedding yet")

    # ══════════════════════════════════════════════════════════════════════════
    section("JOB MATCHING — Stage 1 → Stage 2 → Stage 3 + SHAP Explanation")
    # ══════════════════════════════════════════════════════════════════════════

    step("How the 3-stage pipeline works")
    print("""
  ┌─────────┬────────────────────────────────────────────────────────────────┐
  │ Stage 1 │ pgvector ANN cosine similarity                                 │
  │         │ Encodes freelancer profile + job post with SentenceTransformer  │
  │         │ Compares vectors in high-dimensional space → similarity score   │
  │         │ Returns top-K candidates for Stage 2                            │
  ├─────────┼────────────────────────────────────────────────────────────────┤
  │ Stage 2 │ Skill overlap heuristic (hard filter)                          │
  │         │ Counts how many freelancer skills appear in job required skills │
  │         │ Threshold: ≥ 20% overlap — jobs below this are dropped         │
  │         │ Prevents unqualified matches from reaching the ML model         │
  ├─────────┼────────────────────────────────────────────────────────────────┤
  │ Stage 3 │ CatBoost gradient boosting classifier                          │
  │         │ Features: cosine similarity, skill overlap %, experience match  │
  │         │ Output: match_probability 0–100% (AUC 0.8054 on validation set) │
  │         │ SHAP values explain which features pushed prediction up or down │
  └─────────┴────────────────────────────────────────────────────────────────┘
    """)

    step("Freelancer-to-jobs match (top 10) — full pipeline output")
    ftj = _ex(get("/ai/job_matching/match/freelancer-to-jobs?limit=10",
                  fl_tok, timeout=120))
    matches = ftj.get("matches", [])
    print(f"  {len(matches)} match(es) returned")

    target_found = False
    for m in matches:
        _print_match(m, job_post_id)
        if str(m.get("job_post_id")) == str(job_post_id):
            target_found = True

    if not target_found:
        print(f"\n  NOTE: Target job ({job_post_id}) not in top 10.")
        print("        Embeddings may still be indexing. Re-run with --embed-wait 15.")

    if not skip_analyse:
        step("RAG job-fit analysis — LLM explains fit in natural language")
        print("  (Calls LLM to generate narrative analysis — may take 15–30s)")
        try:
            r = requests.get(
                f"{BASE_URL}/ai/job_matching/analyse/job/{job_post_id}",
                headers=_hdr(fl_tok), timeout=120,
            )
            print(f"  {'✓' if r.ok else '✗'} GET /ai/job_matching/analyse/job/{job_post_id}  [{r.status_code}]")
            if r.ok:
                a = _ex(r.json())
                print(f"\n    ┌─ RAG FIT ANALYSIS")
                print(f"    │  match_score    : {a.get('match_score')}")
                print(f"    │  recommendation : {str(a.get('recommendation') or '')[:120]}")
                for s in (a.get("strengths") or [])[:4]:
                    print(f"    │  ✓ {s}")
                for g in (a.get("gaps") or [])[:4]:
                    print(f"    │  ✗ {g}")
                print(f"    └{'─' * 55}")
        except requests.exceptions.Timeout:
            print("    Analysis timed out — LLM may be slow. Continuing.")

    # ══════════════════════════════════════════════════════════════════════════
    section("PROPOSAL — Submit, View, Accept")
    # ══════════════════════════════════════════════════════════════════════════

    step("FREELANCER submits proposal")
    prop = _ex(post("/proposals", {
        "job_post_id": job_post_id,
        "job_role_id": job_role_id,
        "cover_letter": (
            "Hello! I am excited about this Senior Backend Developer role. "
            "I have 5 years of hands-on FastAPI + PostgreSQL experience from Tokopedia "
            "and can start immediately. I delivered a similar production system for 100K+ users. "
            "Happy to discuss architecture in detail."
        ),
        "proposed_budget": 15000000.00,
        "proposed_duration": "3 months",
    }, token=fl_tok, expected={200, 201}))
    proposal_id = prop["proposal_id"]
    print(f"    proposal_id : {proposal_id}")
    print(f"    status      : {prop.get('status')}  | budget: {prop.get('proposed_budget')}")

    step("FREELANCER views their own proposals (GET /proposals/me)")
    my_props = _ex(get("/proposals/me", fl_tok))
    my_props = my_props if isinstance(my_props, list) else []
    print(f"    {len(my_props)} proposal(s) submitted by this freelancer")

    step("CLIENT views proposals for their job post")
    job_props = _ex(get(f"/proposals/job-post/{job_post_id}", c_tok))
    job_props = job_props if isinstance(job_props, list) else []
    print(f"    {len(job_props)} proposal(s) on job {job_post_id}")
    for p in job_props:
        print(f"    · {p.get('proposal_id')}  status={p.get('status')}  budget={p.get('proposed_budget')}")

    step("CLIENT accepts the proposal")
    accepted = _ex(patch(f"/proposals/{proposal_id}/status",
                         params={"status": "accepted"}, token=c_tok))
    print(f"    New status : {accepted.get('status')}")

    # ══════════════════════════════════════════════════════════════════════════
    section("CONTRACT — Create, Messages, View")
    # ══════════════════════════════════════════════════════════════════════════

    today    = datetime.date.today()
    end_date = today + datetime.timedelta(days=90)

    step("CLIENT creates contract")
    ct = _ex(post("/contracts", {
        "job_post_id":       job_post_id,
        "job_role_id":       job_role_id,
        "proposal_id":       proposal_id,
        "freelancer_id":     freelancer_id,
        "client_id":         client_id,
        "contract_title":    "Backend Development Contract — Alex Developer",
        "role_title":        "Backend Developer",
        "agreed_budget":     15000000.00,
        "budget_currency":   "IDR",
        "payment_structure": "milestone_based",
        "agreed_duration":   "3 months",
        "status":            "active",
        "start_date":        str(today),
        "end_date":          str(end_date),
    }, token=c_tok, expected={200, 201}))
    contract_id = ct["contract_id"]
    print(f"    contract_id : {contract_id}")
    print(f"    status      : {ct.get('status')}  | budget: {ct.get('agreed_budget')} {ct.get('budget_currency')}")
    print(f"    period      : {ct.get('start_date')} → {ct.get('end_date')}")

    step("View auto-sent contract messages")
    msgs = _ex(get(f"/messages/contract/{contract_id}", c_tok))
    msgs = msgs if isinstance(msgs, list) else []
    print(f"    {len(msgs)} message(s) in contract thread")
    for msg in msgs:
        text = str(msg.get("message_text") or "").replace("\n", " ↵ ")[:100]
        print(f"    · [{msg.get('message_type','?')}] {text}")

    step("FREELANCER marks messages as read")
    put(f"/messages/contract/{contract_id}/read", {}, token=fl_tok, expected={200})
    print(f"    Messages marked as read")

    step("Both parties view their contract lists")
    fl_cts = _ex(get(f"/contracts/freelancer/{freelancer_id}", fl_tok))
    cl_cts = _ex(get(f"/contracts/client/{client_id}", c_tok))
    fl_cts = fl_cts if isinstance(fl_cts, list) else []
    cl_cts = cl_cts if isinstance(cl_cts, list) else []
    print(f"    Freelancer contracts : {len(fl_cts)}")
    print(f"    Client contracts     : {len(cl_cts)}")

    # ══════════════════════════════════════════════════════════════════════════
    section("WORK SUBMISSION — Submit, Revision, Re-submit, Approve")
    # ══════════════════════════════════════════════════════════════════════════

    # Helper: safe dict access on post_mp results (response can be a string on error)
    def _sub_get(data, key, default="?"):
        return data.get(key, default) if isinstance(data, dict) else default

    step("FREELANCER submits work — first delivery")
    # Use .docx extension — allowed types: pdf, doc, docx, png, jpg, jpeg, zip
    file_v1 = _make_submission_docx("Deliverable v1 — all endpoints implemented and tested.")
    sub1 = _ex(post_mp(
        "/contract-submissions",
        files=[("files", ("deliverable_v1.docx", file_v1,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        data={"contract_id": contract_id, "note": "First delivery — all endpoints live and tested."},
        token=fl_tok,
        expected={200, 201},
        label="POST /contract-submissions (v1)",
        timeout=30,
    ))
    sub1_id = _sub_get(sub1, "submission_id")
    print(f"    submission_id : {sub1_id}  | status: {_sub_get(sub1, 'status')}")

    step("CLIENT views submissions")
    subs = _ex(get(f"/contract-submissions/contract/{contract_id}", c_tok))
    subs = subs if isinstance(subs, list) else []
    print(f"    {len(subs)} submission(s) on record")

    step("CLIENT requests a revision")
    rev = _ex(put(
        f"/contract-submissions/contract/{contract_id}/request-revision",
        body={"note": "Please add input validation and rate limiting before final delivery."},
        token=c_tok, expected={200},
        label="PUT /contract-submissions/request-revision",
    ))
    print(f"    Revision requested. Latest submission status: {_sub_get(rev, 'status')}")

    step("FREELANCER re-submits revised work")
    file_v2 = _make_submission_docx("Deliverable v2 — revision complete: input validation + rate limiting added. 42/42 tests pass.")
    sub2 = _ex(post_mp(
        "/contract-submissions",
        files=[("files", ("deliverable_v2.docx", file_v2,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))],
        data={"contract_id": contract_id, "note": "Revision done — validation + rate limiting added. 42/42 tests pass."},
        token=fl_tok,
        expected={200, 201},
        label="POST /contract-submissions (v2 revised)",
        timeout=30,
    ))
    print(f"    submission_id : {_sub_get(sub2, 'submission_id')}  | status: {_sub_get(sub2, 'status')}")

    step("CLIENT approves — triggers review pipeline in background")
    approve = _ex(put(
        f"/contract-submissions/contract/{contract_id}/approve",
        body={},
        token=c_tok, expected={200},
        label="PUT /contract-submissions/approve",
    ))
    print(f"    Approved. Status: {_sub_get(approve, 'status')}")
    print(f"    → Background task: AI generates targeted review question + skill tag suggestions")

    # ══════════════════════════════════════════════════════════════════════════
    section("REVIEW — AI Question, Ratings, Analysis, Trust Score, Red Flags")
    # ══════════════════════════════════════════════════════════════════════════

    print(f"\n  Waiting {DEFAULT_REVIEW_WAIT}s for review pipeline (AI question generation)...")
    time.sleep(DEFAULT_REVIEW_WAIT)

    step("Get review form — AI-generated targeted question + suggested skill tags")
    rev_form = get(f"/reviews/contract/{contract_id}", c_tok, expected={200})
    rev_data = _ex(rev_form)
    review_id = None
    if rev_data:
        review_id = rev_data.get("id") or rev_data.get("review_id")
        print(f"    review_id         : {review_id}")
        print(f"    status            : {rev_data.get('status')}")
        q = rev_data.get("targeted_question") or "(not generated yet — pipeline still running)"
        print(f"    targeted question : {q[:120]}")
        tags = rev_data.get("suggested_skill_tags") or []
        print(f"    suggested tags    : {', '.join(tags[:8]) or 'none'}")

    if review_id:
        step("CLIENT submits review with 4-category ratings")
        sub_rev = _ex(post(f"/reviews/{review_id}/submit", {
            "ratings": [
                {"category": "communication",   "score": 5.0},
                {"category": "quality",         "score": 4.5},
                {"category": "professionalism", "score": 5.0},
                {"category": "value_for_money", "score": 4.5},
            ],
            "client_answer": "Yes — clean, well-documented code with all tests passing.",
            "overall_comment": (
                "Outstanding experience. Alex delivered a robust, production-ready API on time "
                "and within budget. Communication was excellent throughout. Handled the revision "
                "request quickly and professionally. Highly recommended."
            ),
            "extra_skill_tags": ["Clean Code", "Fast Delivery", "Communication"],
        }, token=c_tok, expected={200, 201}))
        print(f"    {sub_rev.get('message', 'Review submitted')}")
        print("    → Background: AI sentiment analysis + publish pipeline queued")

        print(f"\n  Waiting {DEFAULT_REVIEW_WAIT}s for AI analysis pipeline...")
        time.sleep(DEFAULT_REVIEW_WAIT)

        step("Get published review — full detail with AI analysis")
        detail = _ex(get(f"/reviews/{review_id}", c_tok, expected={200}))
        print(f"    status    : {detail.get('status')}")
        print(f"    comment   : {str(detail.get('overall_comment') or '')[:100]}")
        for rat in (detail.get("ratings") or []):
            print(f"    · {str(rat.get('category')):20s} : {rat.get('score')}/5.0")
        ai = detail.get("ai_analysis") or {}
        if ai:
            print(f"    AI sentiment : {ai.get('sentiment')}")
            print(f"    AI summary   : {str(ai.get('summary') or '')[:120]}")

        step("Get all published reviews for freelancer")
        all_revs = _ex(get(f"/reviews/freelancer/{freelancer_id}", c_tok, expected={200}))
        all_revs = all_revs if isinstance(all_revs, list) else []
        print(f"    {len(all_revs)} published review(s)")

        step("Get freelancer trust score breakdown")
        ts = _ex(get(f"/reviews/trust-score/{freelancer_id}", c_tok, expected={200}))
        print(f"    overall_score : {ts.get('overall_score')}")
        print(f"    total_reviews : {ts.get('total_reviews')}")
        for k, v in list((ts.get("components") or {}).items())[:5]:
            print(f"    · {k:25s} : {v}")

        step("Check red flags (expect 0 for a high-rated freelancer)")
        flags = _ex(get(f"/reviews/red-flags/{freelancer_id}", c_tok, expected={200}))
        flags = flags if isinstance(flags, list) else []
        print(f"    {len(flags)} red flag(s) detected")
        for f in flags:
            print(f"    ⚠  {f.get('flag_type')}: {str(f.get('description',''))[:80]}")
    else:
        step("Review section — skipped (review not created, pipeline may not have run)")

    # ══════════════════════════════════════════════════════════════════════════
    section("DASHBOARD")
    # ══════════════════════════════════════════════════════════════════════════

    step("Freelancer dashboard")
    fl_dash = _ex(get("/dashboard/freelancer", fl_tok))
    print(f"    active_contracts  : {fl_dash.get('active_contracts', '?')}")
    print(f"    pending_proposals : {fl_dash.get('pending_proposals', '?')}")
    print(f"    total_earnings    : {fl_dash.get('total_earnings', '?')}")

    step("Client dashboard")
    cl_dash = _ex(get("/dashboard/client", c_tok))
    print(f"    active_contracts  : {cl_dash.get('active_contracts', '?')}")
    print(f"    active_job_posts  : {cl_dash.get('active_job_posts', '?')}")
    print(f"    total_spent       : {cl_dash.get('total_spent', '?')}")

    # ══════════════════════════════════════════════════════════════════════════
    section("PERFORMANCE RATINGS & CLIENT TRUST SCORE")
    # ══════════════════════════════════════════════════════════════════════════

    step("Freelancer performance ratings")
    try:
        perf = _ex(get(f"/performance-ratings/freelancer/{freelancer_id}", fl_tok, expected={200}))
        print(f"    {str(perf)[:200]}")
    except SystemExit:
        print("    Performance ratings endpoint not reachable")

    step("Client trust score")
    try:
        cts = _ex(get(f"/client-trust-scores/{client_id}", c_tok, expected={200}))
        print(f"    {str(cts)[:200]}")
    except SystemExit:
        print("    Client trust score endpoint not reachable")

    # ── Summary ───────────────────────────────────────────────────────────────

    print("\n" + "═" * 70)
    print("  WALKTHROUGH COMPLETE — ALL FEATURES COVERED")
    print("═" * 70)
    print(f"  client_id       : {client_id}")
    print(f"  freelancer_id   : {freelancer_id}")
    print(f"  dual_role       : fl={du_fl_id}  cl={du_cl_id}")
    print(f"  job_post_id     : {job_post_id}  (CLIENT)")
    print(f"  job_post_id2    : {job_post2_id} (DUAL_ROLE as client)")
    print(f"  job_role_id     : {job_role_id}")
    print(f"  proposal_id     : {proposal_id}")
    print(f"  contract_id     : {contract_id}")
    if review_id:
        print(f"  review_id       : {review_id}")


def main():
    parser = argparse.ArgumentParser(description="Comprehensive platform walkthrough.")
    parser.add_argument("--embed-wait", type=int, default=DEFAULT_EMBED_WAIT,
                        help=f"Seconds to wait after triggering embeddings (default {DEFAULT_EMBED_WAIT})")
    parser.add_argument("--skip-analyse", action="store_true",
                        help="Skip the RAG analyse step")
    parser.add_argument("--skip-cv", action="store_true",
                        help="Skip CV upload and analysis")
    args = parser.parse_args()

    tee, path = _start_tee()
    try:
        run(embed_wait=args.embed_wait, skip_analyse=args.skip_analyse, skip_cv=args.skip_cv)
    finally:
        _stop_tee(tee, path)


if __name__ == "__main__":
    main()
