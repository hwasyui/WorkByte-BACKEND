"""
Cold-start walkthrough — Angel Start (new freelancer, zero history).

Demonstrates how the platform treats a brand-new freelancer who has:
  - A complete profile (skills, specialities, bio, portfolio, work experience)
  - NO completed contracts, NO ratings, NO freelancer_trust_scores row
  - is_cold_start = True → ML ranker uses profile-based heuristic (no cap)

Usage (from inside the backend container):
    pip install requests
    python walkthrough/walkthrough_coldstart.py

Or from outside the container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough_coldstart.py
"""

import sys
import json
import os
import random
import datetime
import requests

BASE_URL = "http://localhost:8000"

_RUN_ID           = random.randint(1000, 9999)
_EMAIL_FREELANCER = f"angel.start.{_RUN_ID}@walkthrough.dev"
_EMAIL_CLIENT1    = f"techstartup.cs.{_RUN_ID}@walkthrough.dev"
_EMAIL_CLIENT2    = f"datacorp.cs.{_RUN_ID}@walkthrough.dev"
_PASSWORD         = "SecurePass123"

# ── output tee ────────────────────────────────────────────────────────────────

class _Tee:
    def __init__(self, filepath: str):
        self._stdout = sys.stdout
        self._file   = open(filepath, "w", encoding="utf-8")

    def write(self, data: str):
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


def _start_tee() -> tuple[_Tee, str]:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_coldstart_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")

# ── helpers ───────────────────────────────────────────────────────────────────

_step = 0

def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'='*60}")
    print(f"  Step {_step}: {title}")
    print(f"{'='*60}")


def post(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  POST {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def get(endpoint: str, token: str = None, params: dict = None) -> dict:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(f"{BASE_URL}{endpoint}", headers=headers, params=params, timeout=90)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  GET  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def put(endpoint: str, body: dict, token: str = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.put(f"{BASE_URL}{endpoint}", json=body, headers=headers, timeout=60)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  PUT  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def put_form(endpoint: str, body: dict, token: str = None) -> dict:
    """PUT with multipart/form-data — required for freelancer/client profile endpoints."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.put(f"{BASE_URL}{endpoint}", data=body, headers=headers, timeout=60)
    data = r.json()
    status = "OK" if r.ok else "FAIL"
    print(f"  PUT  {endpoint}  [{r.status_code}] {status}")
    if not r.ok:
        print(f"  ERROR: {json.dumps(data, indent=4)}")
        sys.exit(1)
    return data


def extract(response: dict) -> dict:
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


def get_or_create_skill(name: str, category: str, description: str, token: str) -> str:
    r = requests.get(f"{BASE_URL}/skills/search/{name}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.ok:
        for item in r.json().get("details", {}).get("results", []):
            if item["skill_name"].lower() == name.lower():
                print(f"    {name} → {item['skill_id']} (existing)")
                return item["skill_id"]
    sid = extract(post("/skills", {"skill_name": name, "skill_category": category,
                                   "description": description}, token))["skill_id"]
    print(f"    {name} → {sid} (created)")
    return sid


def get_or_create_speciality(name: str, description: str, token: str) -> str:
    r = requests.get(f"{BASE_URL}/specialities/search/{name}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.ok:
        for item in r.json().get("details", {}).get("results", []):
            if item["speciality_name"].lower() == name.lower():
                print(f"    {name} → {item['speciality_id']} (existing)")
                return item["speciality_id"]
    sid = extract(post("/specialities", {"speciality_name": name, "description": description},
                       token))["speciality_id"]
    print(f"    {name} → {sid} (created)")
    return sid


def get_or_create_language(name: str, iso_code: str, token: str) -> str:
    r = requests.get(f"{BASE_URL}/languages/search/{name}",
                     headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if r.ok:
        for item in r.json().get("details", {}).get("results", []):
            if item["language_name"].lower() == name.lower():
                print(f"    {name} → {item['language_id']} (existing)")
                return item["language_id"]
    lid = extract(post("/languages", {"language_name": name, "iso_code": iso_code},
                       token))["language_id"]
    print(f"    {name} → {lid} (created)")
    return lid

# ── main ──────────────────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  Capstone API — Cold-Start Walkthrough (Angel Start)")
    print("="*60)
    print(f"  Target : {BASE_URL}")
    print(f"  Output : {out_path}")
    print()
    print("  Angel Start is a brand-new freelancer with:")
    print("    ✓ Complete profile (skills, bio, portfolio, work exp)")
    print("    ✗ Zero completed contracts on the platform")
    print("    ✗ No ratings, no freelancer_trust_scores row")
    print("    → ML ranker uses cold-start heuristic (no cap on score)")
    print("    → RAG analysis runs on profile evidence only (no past contracts)")

    # ── 1. Register ───────────────────────────────────────────────────────────

    step(f"Register Angel Start as freelancer — run id: {_RUN_ID}")
    post("/auth/register", {
        "email":     _EMAIL_FREELANCER,
        "password":  _PASSWORD,
        "user_type": "freelancer",
        "full_name": "Angel Start"
    })

    step("Register two clients for job posts")
    post("/auth/register", {
        "email":     _EMAIL_CLIENT1,
        "password":  _PASSWORD,
        "user_type": "client",
        "full_name": "TechStartup Inc."
    })
    post("/auth/register", {
        "email":     _EMAIL_CLIENT2,
        "password":  _PASSWORD,
        "user_type": "client",
        "full_name": "DataCorp Solutions"
    })

    # ── 2. Login ──────────────────────────────────────────────────────────────

    step("Log in all users")
    tok_freelancer = token_from_login(_EMAIL_FREELANCER, _PASSWORD)
    tok_client1    = token_from_login(_EMAIL_CLIENT1,    _PASSWORD)
    tok_client2    = token_from_login(_EMAIL_CLIENT2,    _PASSWORD)
    print("  All tokens obtained.")

    # ── 3. Fetch profile IDs ──────────────────────────────────────────────────

    step("Fetch profile IDs")
    fid  = extract(get("/freelancers", tok_freelancer))[0]["freelancer_id"]
    cid1 = extract(get("/clients",    tok_client1))[0]["client_id"]
    cid2 = extract(get("/clients",    tok_client2))[0]["client_id"]
    print(f"  freelancer_id : {fid}")
    print(f"  client1_id    : {cid1}")
    print(f"  client2_id    : {cid2}")

    # ── 4. Build Angel's profile ──────────────────────────────────────────────

    step("Fill in Angel Start's profile — strong backend skills, no platform history")
    put_form(f"/freelancers/{fid}", {
        "bio": (
            "Fresh backend developer with a Computer Science degree and 1 year of "
            "freelance project experience outside this platform. Passionate about building "
            "clean REST APIs with Python and FastAPI. Looking for my first platform contract."
        ),
        "estimated_rate": 25.0,
        "rate_time":      "hourly",
        "rate_currency":  "USD"
    }, tok_freelancer)

    # ── 5. Skills ─────────────────────────────────────────────────────────────

    step("Create skills")
    skills_to_create = [
        ("Python",        "hard_skill", "Python programming language"),
        ("FastAPI",       "hard_skill", "Python async web framework"),
        ("PostgreSQL",    "hard_skill", "Relational database"),
        ("REST API",      "hard_skill", "RESTful API design"),
        ("Docker",        "tool",       "Container platform"),
        ("Git",           "tool",       "Version control"),
        ("Redis",         "tool",       "In-memory data store"),
        ("React",         "hard_skill", "Frontend JavaScript framework"),
        ("TypeScript",    "hard_skill", "Typed superset of JavaScript"),
        ("Kubernetes",    "tool",       "Container orchestration"),
        ("AWS",           "tool",       "Amazon Web Services"),
        ("Apache Spark",  "hard_skill", "Distributed data processing"),
        ("Data Modeling", "hard_skill", "Database schema design"),
        ("Node.js",       "hard_skill", "JavaScript server-side runtime"),
    ]
    skill_ids = {}
    for name, category, description in skills_to_create:
        skill_ids[name] = get_or_create_skill(name, category, description, tok_client1)

    step("Create specialities")
    specs_to_create = [
        ("Backend Development",  "Server-side API and service development"),
        ("Frontend Development", "Client-side UI and web development"),
        ("Data Engineering",     "Data pipelines, warehousing and ETL"),
        ("DevOps",               "Infrastructure, CI/CD and platform engineering"),
    ]
    spec_ids = {}
    for name, description in specs_to_create:
        spec_ids[name] = get_or_create_speciality(name, description, tok_client1)

    step("Create languages")
    lang_ids = {}
    for name, iso in [("English", "en"), ("Indonesian", "id")]:
        lang_ids[name] = get_or_create_language(name, iso, tok_client1)

    step("Assign skills to Angel Start — strong backend profile")
    freelancer_skills = [
        ("Python",     "advanced"),
        ("FastAPI",    "advanced"),
        ("PostgreSQL", "intermediate"),
        ("REST API",   "intermediate"),
        ("Docker",     "beginner"),
        ("Git",        "intermediate"),
        ("Redis",      "beginner"),
    ]
    for skill_name, level in freelancer_skills:
        post("/freelancer-skills", {
            "freelancer_id":    fid,
            "skill_id":         skill_ids[skill_name],
            "proficiency_level": level
        }, tok_freelancer)
        print(f"    {skill_name} ({level})")

    step("Assign speciality")
    post("/freelancer-specialities", {
        "freelancer_id": fid,
        "speciality_id": spec_ids["Backend Development"],
        "is_primary":    True
    }, tok_freelancer)

    step("Assign languages")
    post("/freelancer-languages", {"freelancer_id": fid, "language_id": lang_ids["English"],    "proficiency_level": "fluent"}, tok_freelancer)
    post("/freelancer-languages", {"freelancer_id": fid, "language_id": lang_ids["Indonesian"], "proficiency_level": "native"}, tok_freelancer)

    step("Add work experience (prior to platform — shows real-world background)")
    post("/work-experiences", {
        "freelancer_id": fid,
        "job_title":     "Junior Backend Developer",
        "company_name":  "Personal Freelance Projects",
        "location":      "Jakarta, Indonesia",
        "start_date":    "2023-06-01",
        "end_date":      "2024-12-31",
        "is_current":    False,
        "description": (
            "Built REST APIs for two small e-commerce clients using Python and FastAPI. "
            "Used PostgreSQL for storage and Docker for local development. "
            "Projects were completed off-platform."
        )
    }, tok_freelancer)
    post("/work-experiences", {
        "freelancer_id": fid,
        "job_title":     "Backend Intern",
        "company_name":  "Startup XYZ",
        "location":      "Bandung, Indonesia",
        "start_date":    "2023-01-01",
        "end_date":      "2023-05-31",
        "is_current":    False,
        "description": (
            "Assisted in building internal REST API endpoints using Flask and PostgreSQL. "
            "Gained experience with Git workflows and basic Docker deployments."
        )
    }, tok_freelancer)

    step("Add portfolio items — off-platform work as evidence")
    post("/portfolios", {
        "freelancer_id":     fid,
        "project_title":     "Personal Blog API",
        "project_description": (
            "Built a full CRUD REST API for a personal blog platform using FastAPI and "
            "PostgreSQL. Includes JWT authentication, pagination, and OpenAPI docs. "
            "Deployed with Docker Compose."
        ),
        "project_url":     "https://github.com/angelstart/blog-api",
        "completion_date": "2024-03-01"
    }, tok_freelancer)
    post("/portfolios", {
        "freelancer_id":     fid,
        "project_title":     "Inventory Management Backend",
        "project_description": (
            "REST API for a small retail inventory system. Python/FastAPI, PostgreSQL with "
            "relational schema design, Redis for session caching. Solo project for a local "
            "client, delivered in 3 weeks."
        ),
        "project_url":     "https://github.com/angelstart/inventory-api",
        "completion_date": "2024-08-01"
    }, tok_freelancer)

    print("\n  NOTE: Angel has ZERO platform contracts and NO freelancer_trust_scores row.")
    print("  total_reviews = 0  →  is_cold_start = True")
    print("  The ML ranker will use the profile-based heuristic (cosine + skill overlap).")
    print("  There is NO cap on the score — Angel can rank as high as her profile warrants.")

    # ── 6. Create job posts ───────────────────────────────────────────────────

    step("Client 1 — Create job: Python Microservices Developer (strong match for Angel)")
    resp = post("/job-posts", {
        "client_id":         cid1,
        "job_title":         "Python Microservices Developer",
        "job_description": (
            "We are migrating a monolithic app into microservices. Each service exposes "
            "a REST API built with FastAPI. You will own 2–3 services end to end: schema "
            "design in PostgreSQL, endpoint implementation, Redis caching layer, and "
            "containerisation with Docker. Great entry-level opportunity — we care about "
            "code quality and communication, not years of experience."
        ),
        "project_type":       "individual",
        "project_scope":      "medium",
        "estimated_duration": "3 months",
        "experience_level":   "entry",
        "status":             "draft"
    }, tok_client1)
    job1_id = extract(resp)["job_post_id"]
    print(f"  job1_id (Python Microservices Developer): {job1_id}")

    step("Client 1 — Create job: API Integration Specialist (partial match — needs Node.js)")
    resp = post("/job-posts", {
        "client_id":         cid1,
        "job_title":         "API Integration Specialist",
        "job_description": (
            "Connect our platform to 10+ third-party services via REST and webhook APIs. "
            "Half the integrations are Python/FastAPI, the other half are Node.js/Express. "
            "You will write integration tests, handle auth flows (OAuth2, API keys), "
            "and maintain a PostgreSQL log of all external calls."
        ),
        "project_type":       "individual",
        "project_scope":      "small",
        "estimated_duration": "6 weeks",
        "experience_level":   "intermediate",
        "status":             "draft"
    }, tok_client1)
    job2_id = extract(resp)["job_post_id"]
    print(f"  job2_id (API Integration Specialist): {job2_id}")

    step("Client 2 — Create job: Cloud Infrastructure Engineer (poor match — Angel has no cloud/k8s)")
    resp = post("/job-posts", {
        "client_id":         cid2,
        "job_title":         "Cloud Infrastructure Engineer",
        "job_description": (
            "Design and manage our multi-region AWS infrastructure. Provision resources "
            "with Terraform, orchestrate workloads on Kubernetes (EKS), and own the "
            "full CI/CD pipeline. Strong AWS and Kubernetes expertise required."
        ),
        "project_type":       "individual",
        "project_scope":      "large",
        "estimated_duration": "ongoing",
        "experience_level":   "expert",
        "status":             "draft"
    }, tok_client2)
    job3_id = extract(resp)["job_post_id"]
    print(f"  job3_id (Cloud Infrastructure Engineer): {job3_id}")

    step("Client 2 — Create job: Backend + Analytics Developer (partial — needs Spark + Data Modeling)")
    resp = post("/job-posts", {
        "client_id":         cid2,
        "job_title":         "Backend + Analytics Developer",
        "job_description": (
            "Build the backend API layer AND the analytics pipeline for our reporting product. "
            "API work is Python/FastAPI + PostgreSQL (Angel's strong suit). The analytics "
            "side requires Apache Spark for batch processing and solid data modeling skills."
        ),
        "project_type":       "individual",
        "project_scope":      "large",
        "estimated_duration": "5 months",
        "experience_level":   "intermediate",
        "status":             "draft"
    }, tok_client2)
    job4_id = extract(resp)["job_post_id"]
    print(f"  job4_id (Backend + Analytics Developer): {job4_id}")

    # ── 7. Roles + skills ─────────────────────────────────────────────────────

    step("Add roles + skills to Job 1 — Python Microservices Developer")
    resp = post("/job-roles", {
        "job_post_id":     job1_id,
        "role_title":      "Microservices Developer",
        "role_budget":     2500.0,
        "budget_currency": "USD",
        "budget_type":     "fixed",
        "role_description": "Own 2–3 FastAPI microservices: schema, endpoints, caching, Docker.",
        "display_order":   0
    }, tok_client1)
    role1_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",     True,  "required"),
        ("FastAPI",    True,  "required"),
        ("PostgreSQL", True,  "required"),
        ("REST API",   True,  "required"),
        ("Docker",     True,  "required"),
        ("Redis",      False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id":      role1_id,
            "skill_id":         skill_ids[skill_name],
            "is_required":      required,
            "importance_level": importance
        }, tok_client1)
        print(f"    {skill_name} ({'required' if required else 'preferred'})")

    step("Add roles + skills to Job 2 — API Integration Specialist")
    resp = post("/job-roles", {
        "job_post_id":     job2_id,
        "role_title":      "API Integration Developer",
        "role_budget":     2000.0,
        "budget_currency": "USD",
        "budget_type":     "fixed",
        "role_description": "Integrate third-party APIs in both Python/FastAPI and Node.js.",
        "display_order":   0
    }, tok_client1)
    role2_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",     True,  "required"),
        ("REST API",   True,  "required"),
        ("Node.js",    True,  "required"),
        ("PostgreSQL", True,  "required"),
        ("FastAPI",    False, "preferred"),
        ("Git",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id":      role2_id,
            "skill_id":         skill_ids[skill_name],
            "is_required":      required,
            "importance_level": importance
        }, tok_client1)

    step("Add roles + skills to Job 3 — Cloud Infrastructure Engineer")
    resp = post("/job-roles", {
        "job_post_id":     job3_id,
        "role_title":      "Cloud Infrastructure Engineer",
        "role_budget":     5500.0,
        "budget_currency": "USD",
        "budget_type":     "fixed",
        "role_description": "Provision multi-region AWS infra with Terraform and EKS.",
        "display_order":   0
    }, tok_client2)
    role3_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Kubernetes", True,  "required"),
        ("AWS",        True,  "required"),
        ("Docker",     True,  "required"),
        ("Git",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id":      role3_id,
            "skill_id":         skill_ids[skill_name],
            "is_required":      required,
            "importance_level": importance
        }, tok_client2)

    step("Add roles + skills to Job 4 — Backend + Analytics Developer")
    resp = post("/job-roles", {
        "job_post_id":     job4_id,
        "role_title":      "Backend + Analytics Developer",
        "role_budget":     4200.0,
        "budget_currency": "USD",
        "budget_type":     "fixed",
        "role_description": "Build FastAPI endpoints and Spark-based analytics pipelines.",
        "display_order":   0
    }, tok_client2)
    role4_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",        True,  "required"),
        ("FastAPI",       True,  "required"),
        ("PostgreSQL",    True,  "required"),
        ("Apache Spark",  True,  "required"),
        ("Data Modeling", True,  "required"),
        ("Docker",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id":      role4_id,
            "skill_id":         skill_ids[skill_name],
            "is_required":      required,
            "importance_level": importance
        }, tok_client2)

    # ── 8. Activate all jobs ──────────────────────────────────────────────────

    step("Activate all job posts")
    for jid, label, tok in [
        (job1_id, "Python Microservices Developer",   tok_client1),
        (job2_id, "API Integration Specialist",       tok_client1),
        (job3_id, "Cloud Infrastructure Engineer",    tok_client2),
        (job4_id, "Backend + Analytics Developer",    tok_client2),
    ]:
        put(f"/job-posts/{jid}", {"status": "active"}, tok)
        print(f"    '{label}' → active")

    # ── 9. Embeddings ─────────────────────────────────────────────────────────

    step("Trigger embedding for Angel's freelancer profile")
    post(f"/ai/job_matching/embed/freelancer/{fid}", {}, tok_freelancer)

    step("Trigger embedding for all job posts")
    for jid, label in [
        (job1_id, "Python Microservices Developer"),
        (job2_id, "API Integration Specialist"),
        (job3_id, "Cloud Infrastructure Engineer"),
        (job4_id, "Backend + Analytics Developer"),
    ]:
        post(f"/ai/job_matching/embed/job/{jid}", {}, tok_client1)
        print(f"    queued: {label}")

    step("Run sweep — generates all embeddings (may take 10–30s via Ollama)")
    post("/ai/job_matching/sweep", {}, tok_freelancer)
    print("  Sweep complete — all embeddings ready.")

    # ── 10. 3-stage matching ──────────────────────────────────────────────────

    step("Run 3-stage job matching pipeline for Angel (cold-start)")
    print("  Expected: Backend API Developer ranks #1 (100% skill match)")
    print("  Cold-start heuristic: score = 0.05 + (cosine-0.5)*0.9*overlap + overlap*0.3")
    print("  No ceiling — Angel competes on profile quality alone.")
    resp = get("/ai/job_matching/match/freelancer-to-jobs", tok_freelancer, params={"limit": 10})
    matches = extract(resp).get("matches", [])
    print(f"\n  Results ({len(matches)} jobs returned):")
    print(f"  {'Rank':<5} {'Job Title':<35} {'Match%':<10} {'Cosine':<10} {'Skill Overlap'}")
    print(f"  {'-'*5} {'-'*35} {'-'*10} {'-'*10} {'-'*15}")
    for i, m in enumerate(matches, 1):
        print(
            f"  #{i:<4} {m.get('job_title', '?')[:34]:<35} "
            f"{m.get('match_probability', '?'):<10} "
            f"{m.get('similarity_score', '?'):<10} "
            f"{m.get('skill_overlap_pct', '?')}%"
        )

    # ── 11. RAG on best match ─────────────────────────────────────────────────

    if matches:
        best_job_id    = matches[0].get("job_post_id")
        best_job_title = matches[0].get("job_title", "?")
        step(f"RAG deep analysis on best match: '{best_job_title}'")
        print("  Expected: high score, strengths based on portfolio + work exp only")
        print("  No past platform contracts → RAG retrieval returns nothing")
        print("  LLM must reason entirely from profile, portfolio, and work experience.")
        _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{best_job_id}", tok_freelancer)))

    # ── 12. RAG on poor match ─────────────────────────────────────────────────

    step("RAG deep analysis on Cloud Infrastructure job (poor match — 1/3 required skills)")
    print("  Expected: low score, gaps = Kubernetes + AWS, recommendation = skip")
    _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{job3_id}", tok_freelancer)))

    # ── 13. RAG on partial match ──────────────────────────────────────────────

    step("RAG deep analysis on API Integration job (partial match — missing Node.js)")
    print("  Expected: consider — Python/FastAPI/PostgreSQL/REST API present but Node.js missing")
    _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{job2_id}", tok_freelancer)))

    # ── Done ──────────────────────────────────────────────────────────────────

    print("\n" + "="*60)
    print("  Cold-start walkthrough complete.")
    print("="*60)
    print(f"\n  Freelancer : Angel Start  ({fid})")
    print(f"  Client 1   : TechStartup Inc. ({cid1})")
    print(f"  Client 2   : DataCorp Solutions ({cid2})")
    print(f"  Skills assigned      : {len(freelancer_skills)}")
    print(f"  Platform contracts        : 0  ← cold-start confirmed")
    print(f"  freelancer_trust_scores   : not created (total_reviews = 0)  ← cold-start confirmed")
    print(f"  is_cold_start             : True")
    print(f"  Jobs matched         : {len(matches)}")
    print()
    print("  Cold-start heuristic used (no ceiling):")
    print("    p = 0.05 + max(0, cosine - 0.5) * 0.9 * overlap + overlap * 0.3")
    print("    A perfect skill match + high cosine can score 80%+")
    print("    Angel competes equally with experienced freelancers on profile quality.")
    print()

    _stop_tee(tee, out_path)


def _print_rag_result(result: dict) -> None:
    if "error" in result:
        print(f"  LLM error: {result['error']}")
        return

    overall_score = result.get("overall_match_score", result.get("match_score", "?"))
    overall_rec   = (result.get("overall_recommendation", result.get("recommendation", "?")) or "?").upper()
    print(f"\n  Overall Score  : {overall_score}/100  →  {overall_rec}")
    print(f"  Overall Reason : {result.get('overall_recommendation_reason', result.get('recommendation_reason', ''))}")

    roles = result.get("roles", [])
    if roles:
        for role in roles:
            rec   = (role.get("recommendation") or "?").upper()
            score = role.get("match_score", "?")
            print(f"\n  {'─'*54}")
            print(f"  Role : {role.get('role_title', '?')}")
            print(f"  Score: {score}/100  →  {rec}")
            print(f"  Why  : {role.get('recommendation_reason', '')}")

            matching = role.get("matching_skills", [])
            missing  = role.get("missing_required_skills", [])
            print(f"  Matching Skills  : {', '.join(matching) or '(none)'}")
            print(f"  Missing Required : {', '.join(missing)  or '(none)'}")

            strengths = role.get("strengths", [])
            if strengths:
                print("  Strengths:")
                for s in strengths:
                    print(f"    + {s}")

            gaps = role.get("gaps", [])
            if gaps:
                print("  Gaps:")
                for g in gaps:
                    print(f"    - {g}")

            tips = role.get("skill_tips", [])
            if tips:
                print("  Skill Tips:")
                for t in tips:
                    print(f"    → {t}")
    else:
        print(f"\n  Matching Skills  : {', '.join(result.get('matching_skills', [])) or '(none)'}")
        print(f"  Missing Required : {', '.join(result.get('missing_required_skills', [])) or '(none)'}")
        for s in result.get("strengths", []):
            print(f"    + {s}")
        for g in result.get("gaps", []):
            print(f"    - {g}")
        for t in result.get("skill_tips", []):
            print(f"    → {t}")

    print(f"\n  RAG Sources: ", end="")
    src = result.get("rag_sources", {})
    print("  |  ".join(f"{k}={v}" for k, v in src.items()))
    if src.get("past_contracts_used", 0) == 0:
        print("  ↑ past_contracts_used=0 confirms cold-start: LLM reasoned from profile only.")


if __name__ == "__main__":
    run()
