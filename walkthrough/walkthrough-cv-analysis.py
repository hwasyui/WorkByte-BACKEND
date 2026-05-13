"""
CV Analysis Walkthrough — focused flow: login → upload CV → display results.

Assumes the freelancer account and profile already exist.
Use walkthrough-cv.py for the full flow including profile setup.

Flow:
  1.  Login as existing freelancer
  2.  Upload CV: Angelica Suti Whiharto_CV.pdf
  3.  Display full analysis results:
        - Resume score     (0–100, from GROQ LLM)
        - Overall assessment & profile match analysis (from GROQ LLM)
        - Similarity score (all-MiniLM-L6-v2 cosine, CV vs. profile)
        - Skill coverage   (regex word-boundary matching)
        - ATS compliance   (rule-based 0–100)
        - ATS flags        (missing sections, no email, clichés, etc.)
        - Section-by-section recommendations (Skills, Work Experience,
          Education, ATS Optimization) — pure LLM output via GROQ
        - Parsed profile suggestions (bio, skills, languages, experience,
          education extracted from CV text via GROQ)
  4.  Verify cv_file_url is saved to profile
  5.  Summary

Requirements (server):
  APP_ENV=development   SHOW_DEV_OTP=true

Usage:
    python walkthrough/walkthrough-cv-analysis.py
    python walkthrough/walkthrough-cv-analysis.py --base-url http://localhost:8000
"""

import argparse
import datetime
import json
import os
import sys
import time

import requests

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
PASSWORD = "intan2706"
_CV_PDF  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Angelica Suti Whiharto_CV.pdf")


# ── Tee: mirror stdout to a timestamped file ──────────────────────────────────

class _Tee:
    def __init__(self, path: str):
        self._stdout = sys.stdout
        self._file   = open(path, "w", encoding="utf-8")

    def write(self, s):
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
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"cv_analysis_{ts}.md")
    tee  = _Tee(path)
    sys.stdout = tee
    return tee, path


def _stop_tee(tee, path):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Output saved → {path}")


# ── Section / step tracking ───────────────────────────────────────────────────

_sec = 0
_stp = 0


def section(title: str):
    global _sec, _stp
    _sec += 1
    _stp  = 0
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
    """Unwrap ResponseSchema envelope: return 'details' if present."""
    return resp.get("details", resp)


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _die(tag: str, status: int, body: dict):
    print(f"  ✗ {tag} [{status}] — FATAL")
    print(json.dumps(body, indent=2, default=str)[:600])
    sys.exit(1)


def post(endpoint, body, token=None, expected=None, label=None, timeout=60):
    expected = expected or {200, 201}
    headers  = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok  = r.status_code in expected
    tag = label or f"POST {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


def post_mp(endpoint, files, data=None, token=None, expected=None, label=None, timeout=180):
    expected = expected or {200, 201}
    headers  = _hdr(token) if token else {}
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data or {},
                      headers=headers, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok  = r.status_code in expected
    tag = label or f"POST {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        print(f"    ↳ {json.dumps(payload, default=str)[:400]}")
    return payload


def get(endpoint, token=None, expected=None, label=None, timeout=60, params=None):
    expected = expected or {200}
    r = requests.get(f"{BASE_URL}{endpoint}", headers=_hdr(token) if token else {},
                     params=params, timeout=timeout)
    try:
        payload = r.json()
    except Exception:
        payload = {"raw": r.text}
    ok  = r.status_code in expected
    tag = label or f"GET  {endpoint}"
    print(f"  {'✓' if ok else '✗'} {tag}  [{r.status_code}]")
    if not ok:
        _die(tag, r.status_code, payload)
    return payload


# ── ASCII progress bar ────────────────────────────────────────────────────────

def _bar(value: float, width: int = 24) -> str:
    filled = min(width, int(value * width))
    return "█" * filled + "░" * (width - filled)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(base_url: str):
    global BASE_URL
    BASE_URL = base_url

    ts    = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    email = "pasyaintan@gmail.com"

    print("\n" + "═" * 70)
    print("  CAPSTONE — CV Analysis Walkthrough")
    print("═" * 70)
    print(f"  Server    : {BASE_URL}")
    print(f"  Email     : {email}")
    print(f"  CV file   : {_CV_PDF}")
    print(f"  Timestamp : {ts}")

    # ══════════════════════════════════════════════════════════════════════════
    section("AUTH — Login")
    # ══════════════════════════════════════════════════════════════════════════

    step("Login")
    r = requests.post(f"{BASE_URL}/auth/login",
                      json={"email": email, "password": PASSWORD},
                      headers={"Content-Type": "application/json"}, timeout=30)
    if r.status_code != 200:
        print(f"  ✗ Login [{r.status_code}] — account not found or wrong password")
        print("    Run walkthrough-cv.py first to register and build the profile.")
        sys.exit(1)
    token = _ex(r.json()).get("access_token")
    print(f"  ✓ Login  [200]")
    print(f"    Token : {token[:30]}...")

    step("GET /freelancers — retrieve freelancer_id")
    me_resp       = _ex(get("/freelancers", token))
    me            = me_resp[0] if isinstance(me_resp, list) else me_resp
    freelancer_id = me.get("freelancer_id") or me.get("id")
    print(f"    freelancer_id : {freelancer_id}")

    # ══════════════════════════════════════════════════════════════════════════
    section("CV UPLOAD & ANALYSIS")
    # ══════════════════════════════════════════════════════════════════════════

    step("Load PDF file from the walkthrough folder")
    if not os.path.exists(_CV_PDF):
        print(f"  ✗ File not found: {_CV_PDF}")
        print("    Place 'Angelica Suti Whiharto_CV.pdf' in the walkthrough/ folder and try again.")
        sys.exit(1)

    with open(_CV_PDF, "rb") as fh:
        cv_bytes = fh.read()
    print(f"    File   : Angelica Suti Whiharto_CV.pdf")
    print(f"    Size   : {len(cv_bytes):,} bytes  ({len(cv_bytes)/1024:.1f} KB)")

    step("POST /cv_upload — extract → ATS check → GROQ analysis → profile parse")
    print("    (This may take 30–120 seconds due to embedding + LLM inference...)")
    t0      = time.time()
    cv_resp = post_mp(
        "/cv_upload",
        files={"file": ("Angelica Suti Whiharto_CV.pdf", cv_bytes, "application/pdf")},
        token=token,
        expected={200, 201},
        label="POST /cv_upload",
        timeout=180,
    )
    elapsed = time.time() - t0
    print(f"    Completed in {elapsed:.1f} seconds")

    d = _ex(cv_resp)

    if not isinstance(d, dict):
        print(f"  ✗ Invalid response: {str(d)[:300]}")
        sys.exit(1)

    # DEBUG: print raw keys and score fields
    print(f"    [DEBUG] response keys: {list(d.keys())}")
    print(f"    [DEBUG] overall_score={d.get('overall_score')!r}  overall_grade={d.get('overall_grade')!r}  resume_score={d.get('resume_score')!r}  ats_score={d.get('ats_score')!r}  is_initial={d.get('is_initial')!r}")

    # ── Display full analysis results ─────────────────────────────────────────

    step("CV analysis results — full detail")

    sim            = d.get("similarity_score") or 0.0
    cov            = d.get("skill_coverage")
    ats            = d.get("ats_score") or 0
    overall_score  = d.get("overall_score") or 0
    overall_grade  = d.get("overall_grade") or "n/a"
    resume_score   = d.get("resume_score") or 0
    overall        = d.get("overall_assessment") or ""
    match_analysis = d.get("profile_match_analysis") or ""
    sections       = d.get("sections") or []
    matched        = d.get("matched_skills") or []
    missing        = d.get("missing_skills") or []
    flags          = d.get("ats_flags") or []
    suggested      = d.get("suggested_profile") or {}
    file_url       = d.get("file_url") or ""

    SCORE_ICON = {"excellent": "✅", "good": "✅", "fair": "⚠️ ", "bad": "❌"}
    score_icon = SCORE_ICON.get(overall_grade.lower(), "?")
    sim_pct    = sim * 100

    SECTION_ICON = {
        "skills analysis": "🛠 ",
        "work experience": "💼",
        "education":       "🎓",
        "ats optimization":"🤖",
    }

    print(f"""
    ╔══════════════════════════════════════════════════════════╗
    ║                CV ANALYSIS RESULTS                       ║
    ╚══════════════════════════════════════════════════════════╝

    File URL     : ...{file_url[-45:]}
    """)

    # ── Overall score ─────────────────────────────────────────────────────────
    print(f"    ┌─ OVERALL SCORE ────────────────────────────────────────")
    print(f"    │  {overall_score} / 100  →  {score_icon}  {overall_grade.upper()}")
    print(f"    │  [{_bar(overall_score / 100)}]")
    print(f"    │  (Resume Score: {resume_score}/100  +  ATS Score: {ats}/100) ÷ 2")
    print(f"    └────────────────────────────────────────────────────────")

    # ── Overall assessment ────────────────────────────────────────────────────
    if overall:
        print(f"\n    ┌─ OVERALL ASSESSMENT ───────────────────────────────────")
        for ln in [overall[i:i+90] for i in range(0, len(overall), 90)]:
            print(f"    │  {ln}")
        print(f"    └────────────────────────────────────────────────────────")

    # ── Profile match analysis ────────────────────────────────────────────────
    if match_analysis:
        print(f"\n    ┌─ PROFILE MATCH ANALYSIS ──────────────────────────────")
        for ln in [match_analysis[i:i+90] for i in range(0, len(match_analysis), 90)]:
            print(f"    │  {ln}")
        print(f"    └────────────────────────────────────────────────────────")

    # ── Metrics ───────────────────────────────────────────────────────────────
    print(f"\n    ┌─ METRICS ─────────────────────────────────────────────")
    print(f"    │  Similarity Score  : {sim:.4f}  ({sim_pct:.1f}%)")
    print(f"    │  [{_bar(sim)}]")
    print(f"    │  → Cosine similarity between CV embedding and profile embedding")
    print(f"    │    (model: all-MiniLM-L6-v2, 384-dim)")
    if cov is not None:
        cov_pct = cov * 100
        print(f"    │")
        print(f"    │  Skill Coverage    : {cov:.4f}  ({cov_pct:.1f}%)")
        print(f"    │  [{_bar(cov)}]")
        print(f"    │  → {len(matched)} of {len(matched)+len(missing)} profile skills found in CV text")
    print(f"    │")
    print(f"    │  ATS Compliance    : {ats}/100")
    print(f"    │  [{_bar(ats/100)}]")
    print(f"    │  → Rule-based: section presence, contact info, word count, content quality")
    print(f"    └────────────────────────────────────────────────────────")

    # ── Skill matching ────────────────────────────────────────────────────────
    print(f"\n    ┌─ SKILL MATCHING ───────────────────────────────────────")
    if matched:
        print(f"    │  ✓ Found in CV ({len(matched)} skill(s)):")
        for sk in matched:
            print(f"    │      • {sk}")
    else:
        print(f"    │  ✗ No profile skills found in CV text")
    if missing:
        print(f"    │")
        print(f"    │  ✗ Missing from CV ({len(missing)} skill(s)):")
        for sk in missing:
            print(f"    │      • {sk}")
    print(f"    └────────────────────────────────────────────────────────")

    # ── ATS flags ─────────────────────────────────────────────────────────────
    print(f"\n    ┌─ ATS FLAGS ({len(flags)} issue(s)) ────────────────────────────")
    if flags:
        for flag in flags:
            print(f"    │  ⚠  {flag}")
    else:
        print(f"    │  ✓ No ATS issues — CV is fully compliant!")
    print(f"    └────────────────────────────────────────────────────────")

    # ── Section-by-section recommendations (from LLM) ─────────────────────────
    total_recs = sum(len(s.get("recommendations", [])) for s in sections)
    print(f"\n    ┌─ SECTION-BY-SECTION RECOMMENDATIONS ({total_recs} total) ───────")
    if sections:
        for sec in sections:
            title         = sec.get("title", "")
            icon          = SECTION_ICON.get(title.lower(), "📋")
            analysis_text = sec.get("analysis", "")
            recs          = sec.get("recommendations", [])
            print(f"    │")
            print(f"    │  {icon}  {title.upper()}")
            if analysis_text:
                print(f"    │  Analysis:")
                for ln in [analysis_text[i:i+88] for i in range(0, len(analysis_text), 88)]:
                    print(f"    │    {ln}")
            if recs:
                print(f"    │  Recommendations:")
                for i, rec in enumerate(recs, 1):
                    rec_str = str(rec)
                    lines   = [rec_str[j:j+86] for j in range(0, len(rec_str), 86)]
                    print(f"    │    {i}. {lines[0]}")
                    for ln in lines[1:]:
                        print(f"    │       {ln}")
    else:
        print(f"    │  (no sections returned)")
    print(f"    └────────────────────────────────────────────────────────")

    # ── Parsed profile suggestions ────────────────────────────────────────────
    if suggested:
        sugg_bio    = suggested.get("suggested_bio", "")
        sugg_skills = suggested.get("skills", [])
        sugg_langs  = suggested.get("languages", [])
        sugg_we     = suggested.get("work_experience", [])
        sugg_edu    = suggested.get("education", [])
        print(f"\n    ┌─ PARSED PROFILE SUGGESTIONS (from CV) ────────────────")
        if sugg_bio:
            print(f"    │  Bio Suggestion:")
            for ln in [sugg_bio[i:i+88] for i in range(0, len(sugg_bio), 88)]:
                print(f"    │    {ln}")
        if sugg_skills:
            print(f"    │  Skills Found   : {', '.join(sugg_skills[:10])}" +
                  (f"  (+{len(sugg_skills)-10} more)" if len(sugg_skills) > 10 else ""))
        if sugg_langs:
            lang_strs = [f"{l.get('name', '')} ({l.get('proficiency', '')})" for l in sugg_langs]
            print(f"    │  Languages      : {', '.join(lang_strs)}")
        print(f"    │  Work Experience : {len(sugg_we)} entry(ies)")
        for we in sugg_we:
            print(f"    │    • {we.get('job_title', '')} @ {we.get('company_name', '')} "
                  f"({we.get('start_date', '')} – {we.get('end_date', '') or 'present'})")
        print(f"    │  Education      : {len(sugg_edu)} entry(ies)")
        for edu in sugg_edu:
            print(f"    │    • {edu.get('degree', '')} in {edu.get('field_of_study', '')} "
                  f"— {edu.get('institution_name', '')}")
        print(f"    └────────────────────────────────────────────────────────")

    # ══════════════════════════════════════════════════════════════════════════
    section("VERIFICATION — Confirm cv_file_url is saved to profile")
    # ══════════════════════════════════════════════════════════════════════════

    step("GET /freelancers — check cv_file_url on profile")
    me_after_resp = _ex(get("/freelancers", token))
    me_after      = me_after_resp[0] if isinstance(me_after_resp, list) else me_after_resp
    cv_url        = me_after.get("cv_file_url") or ""
    if cv_url:
        print(f"    ✓ cv_file_url saved successfully")
        print(f"      ...{cv_url[-60:]}")
    else:
        print(f"    ✗ cv_file_url is empty — check server logs")

    step("GET /freelancers/{id}/profile — final profile verification")
    final = _ex(get(f"/freelancers/{freelancer_id}/profile", token))
    print(f"    name    : {final.get('full_name')}")
    print(f"    skills  : {len(final.get('skills') or [])}")
    print(f"    cv_url  : {'present ✓' if final.get('cv_file_url') else 'missing ✗'}")

    # ══════════════════════════════════════════════════════════════════════════
    section("SUMMARY")
    # ══════════════════════════════════════════════════════════════════════════

    print(f"""
  ┌──────────────────────────────────────────────────────────────────┐
  │                     CV ANALYSIS SUMMARY                          │
  ├──────────────────────────────────────────────────────────────────┤
  │  File        : Angelica Suti Whiharto_CV.pdf                    │
  │  Freelancer  : {freelancer_id:<52} │
  │  Overall     : {overall_score}/100  ({overall_grade.upper()})                                   │
  │  Resume Score: {resume_score}/100  ·  ATS Score: {ats}/100                         │
  │  Similarity  : {sim:.4f}  ({sim_pct:.1f}%)                                │
  │  Coverage    : {f"{cov:.4f}  ({cov*100:.1f}%)" if cov is not None else "N/A":<52} │
  │  ATS Score   : {ats}/100                                                │
  │  ATS Flags   : {len(flags)} issue(s)                                           │
  │  Sections    : {len(sections)} section(s), {total_recs} recommendation(s)                      │
  │  Elapsed     : {elapsed:.1f}s                                                  │
  └──────────────────────────────────────────────────────────────────┘""")

    print(f"\n  ✅ CV Analysis Walkthrough complete.")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CV Analysis Walkthrough")
    parser.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"),
                        help="FastAPI server base URL (default: http://localhost:8000)")
    args = parser.parse_args()

    tee, path = _start_tee()
    try:
        run(args.base_url)
    finally:
        _stop_tee(tee, path)
