"""
CV Autofill Profile Walkthrough.

Demonstrates the autofill-from-CV onboarding flow:
  1. Register and verify account
  2. Login → get freelancer ID
  3. POST /freelancers/parse-cv  →  backend extracts + parses CV, returns structured data
  4. Simulate frontend caching suggestions and populating form placeholders
  5. User "submits the form":
       PUT  /freelancers/{id}          — bio (and optionally name, rate, etc.)
       POST /freelancer-skills         — one call per skill   (needs skill_id lookup first)
       POST /freelancer-languages      — one call per language (needs language_id lookup first)
       POST /work-experiences          — one call per job entry
       POST /educations                — one call per education entry
  6. Verify the complete profile and embedding were saved correctly

Usage (inside the backend container):
    python walkthrough/walkthrough-cv-profile.py

Or from outside:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough-cv-profile.py
"""

import datetime
import json
import os
import random
import sys
import time
import requests

BASE_URL = "http://localhost:8000"

_RUN_ID  = random.randint(1000, 9999)
_EMAIL   = f"angelica.cv.{_RUN_ID}@testprofile.com"
_PASSWORD = "SecurePass123!"
_NAME    = "Angelica Suti Whiharto"
_CV_FILE = os.path.join(os.path.dirname(__file__), "Angelica Suti Whiharto_CV.pdf")

# ── output tee ────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath):
        self.file    = open(filepath, "w", encoding="utf-8")
        self._stdout = sys.stdout
        sys.stdout   = self

    def write(self, data):
        self._stdout.write(data)
        self.file.write(data)
        self.file.flush()

    def flush(self):
        self._stdout.flush()
        self.file.flush()

    def close(self):
        self.file.close()

    def fileno(self):
        return self._stdout.fileno()

    def isatty(self):
        return self._stdout.isatty()


def _start_tee():
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_cv_profile_{ts}.md")
    tee      = _Tee(filepath)
    return tee, filepath


def _stop_tee(tee, filepath):
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")

# ── HTTP helpers ──────────────────────────────────────────────────────────────

_step = 0


def step(title):
    global _step
    _step += 1
    print(f"\n{'='*60}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*60}")


def post(endpoint, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    data = r.json()
    if not r.ok:
        print(f"    Error: {data}")
    return data


def get(endpoint, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, timeout=60)
    status = "OK" if r.ok else "FAIL"
    print(f"  GET  {endpoint}  [{r.status_code}] {status}")
    data = r.json()
    if not r.ok:
        print(f"    Error: {data}")
    return data


def put(endpoint, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.put(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    status = "OK" if r.ok else "FAIL"
    print(f"  PUT  {endpoint}  [{r.status_code}] {status}")
    data = r.json()
    if not r.ok:
        print(f"    Error: {data}")
    return data


def post_file(endpoint, files, data=None, token=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", files=files, data=data, headers=headers, timeout=120)
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if r.ok:
        return r.json()
    print(f"    Error: {r.text}")
    return {}


def extract(response):
    return response.get("details", response)


def _pp(label, value):
    if isinstance(value, (dict, list)):
        print(f"  {label}:")
        for line in json.dumps(value, indent=4, ensure_ascii=False).splitlines():
            print(f"    {line}")
    else:
        print(f"  {label}: {value}")

# ── helpers ───────────────────────────────────────────────────────────────────

def register_and_verify(email, password, name):
    resp    = post("/auth/register", {"email": email, "password": password,
                                      "user_type": "freelancer", "full_name": name})
    details = extract(resp)
    otp     = details.get("verification", {}).get("dev_verification_otp")
    if otp:
        post("/auth/verify-email", {"email": email, "otp": otp})
    else:
        print("  WARNING: OTP not returned — complete email verification manually.")
    return resp


def login(email, password):
    resp  = post("/auth/login", {"email": email, "password": password})
    token = extract(resp).get("access_token")
    if not token:
        print("  FATAL: login failed. Aborting.")
        sys.exit(1)
    return token


def get_freelancer_id(token):
    me  = extract(get("/auth/me", token))
    fid = me.get("freelancer_id")
    if not fid:
        print("  FATAL: no freelancer_id in /auth/me. Aborting.")
        sys.exit(1)
    return fid


def _parse_date(date_str):
    """Normalise YYYY / YYYY-MM / YYYY-MM-DD to a full date string."""
    if not date_str:
        return None
    date_str = date_str.strip()
    if len(date_str) == 4:
        return f"{date_str}-01-01"
    if len(date_str) == 7:
        return f"{date_str}-01"
    return date_str


def _resolve_skill_id(name, token):
    """Search for a skill by name; return its skill_id or None if not found."""
    resp    = get(f"/skills/search?q={requests.utils.quote(name)}&limit=1", token)
    results = extract(resp).get("results", [])
    if results:
        return results[0].get("skill_id")
    return None


def _resolve_language_id(name, token):
    """Search for a language by name; return its language_id or None if not found."""
    resp    = get(f"/languages/search?name={requests.utils.quote(name)}", token)
    results = extract(resp).get("results", [])
    if results:
        return results[0].get("language_id")
    return None

# ── main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  CV Autofill Profile Walkthrough")
    print("="*60)
    print(f"  Target : {BASE_URL}")
    print(f"  User   : {_EMAIL}")
    print(f"  CV     : {_CV_FILE}")
    print(f"  Output : {out_path}")

    # ── 1. Register + verify ───────────────────────────────────────────────────
    step(f"Register freelancer — run id: {_RUN_ID}")
    register_and_verify(_EMAIL, _PASSWORD, _NAME)
    print("  Registration and email verification done.")

    # ── 2. Login ───────────────────────────────────────────────────────────────
    step("Login")
    token = login(_EMAIL, _PASSWORD)
    print("  Token obtained.")

    # ── 3. Get freelancer ID ───────────────────────────────────────────────────
    step("Get freelancer ID from /auth/me")
    fid = get_freelancer_id(token)
    print(f"  freelancer_id: {fid}")

    # ── 4. Parse CV for autofill ───────────────────────────────────────────────
    step("POST /freelancers/parse-cv  →  get autofill suggestions")
    if not os.path.exists(_CV_FILE):
        print(f"  ERROR: CV file not found at {_CV_FILE}")
        print("  Tip: place a PDF at the path above, or adjust _CV_FILE at the top of this script.")
        _stop_tee(tee, out_path)
        return

    with open(_CV_FILE, "rb") as f:
        parse_resp = post_file(
            "/freelancers/parse-cv",
            files={"file": (os.path.basename(_CV_FILE), f, "application/pdf")},
            token=token,
        )

    if not parse_resp:
        print("  ERROR: parse-cv failed.")
        _stop_tee(tee, out_path)
        return

    suggestions = extract(parse_resp)
    print(f"\n  suggested_bio     : {(suggestions.get('suggested_bio') or '')[:120]}...")
    print(f"  skills found      : {len(suggestions.get('skills', []))}")
    print(f"  languages found   : {len(suggestions.get('languages', []))}")
    print(f"  work_experience   : {len(suggestions.get('work_experience', []))}")
    print(f"  education entries : {len(suggestions.get('education', []))}")
    print()
    print("  [FE] Caches these suggestions and pre-fills form placeholders.")
    print("  [FE] User reviews / edits each field, then submits the form.")

    # ── 5. Simulate frontend: user reviews and confirms suggestions ────────────
    step("Simulate frontend: user confirms / edits the suggestions")

    bio = (suggestions.get("suggested_bio") or f"Experienced professional — {_NAME}.").rstrip(".")
    bio = bio + " (profile reviewed and confirmed)."

    raw_skills    = suggestions.get("skills", [])
    raw_languages = suggestions.get("languages", [])
    raw_work_exp  = suggestions.get("work_experience", [])
    raw_education = suggestions.get("education", [])

    skill_names = [
        (s if isinstance(s, str) else s.get("name", ""))
        for s in raw_skills if s
    ]
    lang_items = [
        {"name": l["name"], "proficiency": l.get("proficiency", "conversational")}
        for l in raw_languages if isinstance(l, dict) and l.get("name")
    ]

    print(f"  bio               : {bio[:100]}...")
    print(f"  skills to save    : {skill_names[:6]}{'...' if len(skill_names) > 6 else ''}")
    print(f"  languages to save : {[l['name'] for l in lang_items]}")
    print(f"  work exp entries  : {len(raw_work_exp)}")
    print(f"  education entries : {len(raw_education)}")

    # ── 6. Apply — step-by-step via regular endpoints ─────────────────────────
    # This is exactly what the frontend does when the user submits the form.

    # 6a. Bio → PUT /freelancers/{fid}
    step("PUT /freelancers/{fid}  →  save bio")
    put(f"/freelancers/{fid}", {"bio": bio}, token=token)

    # 6b. Skills → resolve skill_id, then POST /freelancer-skills
    step("POST /freelancer-skills  →  save each skill")
    skills_saved   = []
    skills_skipped = []
    for name in skill_names:
        if not name:
            continue
        skill_id = _resolve_skill_id(name, token)
        if not skill_id:
            skills_skipped.append(name)
            print(f"    SKIP (not in DB): {name}")
            continue
        resp = post("/freelancer-skills", {
            "freelancer_id"   : fid,
            "skill_id"        : skill_id,
            "proficiency_level": None,
        }, token=token)
        if extract(resp).get("freelancer_skill_id"):
            skills_saved.append(name)
        else:
            skills_skipped.append(name)

    print(f"\n  saved   : {skills_saved}")
    print(f"  skipped : {skills_skipped}")

    # 6c. Languages → resolve language_id, then POST /freelancer-languages
    step("POST /freelancer-languages  →  save each language")
    langs_saved   = []
    langs_skipped = []
    for item in lang_items:
        lang_id = _resolve_language_id(item["name"], token)
        if not lang_id:
            langs_skipped.append(item["name"])
            print(f"    SKIP (not in DB): {item['name']}")
            continue
        resp = post("/freelancer-languages", {
            "freelancer_id"   : fid,
            "language_id"     : lang_id,
            "proficiency_level": item["proficiency"],
        }, token=token)
        if extract(resp).get("freelancer_language_id"):
            langs_saved.append(item["name"])
        else:
            langs_skipped.append(item["name"])

    print(f"\n  saved   : {langs_saved}")
    print(f"  skipped : {langs_skipped}")

    # 6d. Work experience → POST /work-experiences
    step("POST /work-experiences  →  save each job")
    we_saved = 0
    for we in raw_work_exp:
        if not we.get("job_title") or not we.get("company_name"):
            continue
        resp = post("/work-experiences", {
            "freelancer_id": fid,
            "job_title"    : we["job_title"],
            "company_name" : we["company_name"],
            "location"     : we.get("location"),
            "start_date"   : _parse_date(we.get("start_date")),
            "end_date"     : _parse_date(we.get("end_date")) if not we.get("is_current") else None,
            "is_current"   : we.get("is_current", False),
            "description"  : we.get("description"),
        }, token=token)
        if extract(resp).get("work_experience_id"):
            we_saved += 1
            print(f"    + {we['job_title']} @ {we['company_name']}")

    print(f"\n  work experiences saved: {we_saved}")

    # 6e. Education → POST /educations
    step("POST /educations  →  save each education entry")
    edu_saved = 0
    for edu in raw_education:
        if not edu.get("institution_name") or not edu.get("degree"):
            continue
        resp = post("/educations", {
            "freelancer_id"  : fid,
            "institution_name": edu["institution_name"],
            "degree"         : edu["degree"],
            "field_of_study" : edu.get("field_of_study"),
            "start_date"     : _parse_date(edu.get("start_date")),
            "end_date"       : _parse_date(edu.get("end_date")) if not edu.get("is_current") else None,
            "is_current"     : edu.get("is_current", False),
            "grade"          : edu.get("grade"),
        }, token=token)
        if extract(resp).get("education_id"):
            edu_saved += 1
            print(f"    + {edu['degree']} at {edu['institution_name']}")

    print(f"\n  education entries saved: {edu_saved}")

    # ── 7. Verify: GET comprehensive profile ──────────────────────────────────
    step(f"Verify: GET /freelancers/{fid}/profile")
    profile_resp = get(f"/freelancers/{fid}/profile", token=token)
    full_profile = extract(profile_resp)

    if full_profile:
        freelancer_data = full_profile.get("freelancer", {})
        bio_saved = freelancer_data.get("bio") or ""
        print(f"\n  full_name       : {freelancer_data.get('full_name')}")
        print(f"  bio             : {bio_saved[:120]}{'...' if len(bio_saved) > 120 else ''}")
        print(f"  skills          : {[s.get('skill_name') for s in full_profile.get('skills', [])]}")
        print(f"  languages       : {[l.get('language_name') for l in full_profile.get('languages', [])]}")
        print(f"  work_experience : {len(full_profile.get('work_experience', []))} entries")
        print(f"  education       : {len(full_profile.get('education', []))} entries")
    else:
        print("  WARNING: could not retrieve comprehensive profile.")

    # ── 8. Verify embedding was created ───────────────────────────────────────
    step(f"Verify: GET /freelancers/{fid}/embedding")
    print("  Waiting 4 s for background embedding task to complete...")
    time.sleep(4)

    emb_resp = get(f"/freelancers/{fid}/embedding", token=token)
    emb      = extract(emb_resp)

    if isinstance(emb, dict) and emb.get("source_text"):
        src = emb["source_text"]
        print(f"\n  Embedding exists   : YES")
        print(f"  Source text length : {len(src)} chars")
        print(f"  Source text preview: {src[:200]}...")
    else:
        print("\n  Embedding: not yet available (may still be generating).")
        print(f"  Re-run: GET /freelancers/{fid}/embedding")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  CV Autofill Profile Walkthrough — Complete")
    print("="*60)
    print(f"  freelancer_id    : {fid}")
    print(f"  skills saved     : {len(skills_saved)}  skipped: {len(skills_skipped)}")
    print(f"  languages saved  : {len(langs_saved)}  skipped: {len(langs_skipped)}")
    print(f"  work exp saved   : {we_saved}")
    print(f"  education saved  : {edu_saved}")
    print()
    print("  Next steps:")
    print("    PUT /freelancers/{id}         — edit name, rate, profile photo")
    print("    POST /freelancer-specialities — add specialities")
    print("    PUT /freelancer-skills/{id}   — adjust skill proficiency levels")
    print("    POST /portfolio               — add portfolio projects")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    run()
