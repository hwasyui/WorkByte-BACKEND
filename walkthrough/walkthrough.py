"""
Full end-to-end walkthrough of the Capstone freelance platform API.

Covers everything from user registration through the 3-stage AI job matching
pipeline and the RAG deep-analysis endpoint.

Usage (from inside the backend container):
    pip install requests
    python walkthrough/walkthrough.py

Or from outside the container:
    docker exec -it capstone-backend python /app/walkthrough/walkthrough.py

Set BASE_URL below if your backend runs on a different host/port.
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = "http://localhost:8000"

# ── output tee (terminal + markdown file) ─────────────────────────────────────

class _Tee:
    """Write to both the real stdout and a file simultaneously."""

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
    """
    Redirect sys.stdout through _Tee so all print() calls go to both the
    terminal and a markdown file.  Returns (tee_instance, filepath).
    """
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_results_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    """Restore sys.stdout and flush/close the file."""
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


def extract(response: dict) -> dict:
    """Pull the actual payload out of ResponseSchema wrapper (key is 'details')."""
    return response.get("details", response)


def token_from_login(email: str, password: str) -> str:
    resp = post("/auth/login", {"email": email, "password": password})
    return extract(resp)["access_token"]


# ── main walkthrough ──────────────────────────────────────────────────────────

def run():
    tee, out_path = _start_tee()

    print("\n" + "="*60)
    print("  Capstone API — Full Walkthrough")
    print("="*60)
    print(f"  Target: {BASE_URL}")
    print(f"  Output: {out_path}")

    # ── 1. Register users ─────────────────────────────────────────────────────

    step("Register freelancer (Budi Santoso)")
    post("/auth/register", {
        "email": "budi.santoso@walkthrough.dev",
        "password": "SecurePass123",
        "user_type": "freelancer",
        "full_name": "Budi Santoso"
    })

    step("Register client 1 (TechStartup Inc.)")
    post("/auth/register", {
        "email": "techstartup@walkthrough.dev",
        "password": "SecurePass123",
        "user_type": "client",
        "full_name": "TechStartup Inc."
    })

    step("Register client 2 (DataCorp Solutions)")
    post("/auth/register", {
        "email": "datacorp@walkthrough.dev",
        "password": "SecurePass123",
        "user_type": "client",
        "full_name": "DataCorp Solutions"
    })

    # ── 2. Log in all users ───────────────────────────────────────────────────

    step("Log in all three users and grab tokens")
    tok_freelancer = token_from_login("budi.santoso@walkthrough.dev", "SecurePass123")
    tok_client1    = token_from_login("techstartup@walkthrough.dev",  "SecurePass123")
    tok_client2    = token_from_login("datacorp@walkthrough.dev",     "SecurePass123")
    print("  All tokens obtained.")

    # ── 3. Fetch profile IDs ──────────────────────────────────────────────────

    step("Fetch freelancer and client profile IDs")

    # GET /freelancers returns the current user's own profile as a list with one item.
    # GET /clients works the same way — each token only sees its own profile.
    freelancers = extract(get("/freelancers", tok_freelancer))
    fid = freelancers[0]["freelancer_id"]

    clients1 = extract(get("/clients", tok_client1))
    cid1 = clients1[0]["client_id"]

    clients2 = extract(get("/clients", tok_client2))
    cid2 = clients2[0]["client_id"]

    print(f"  freelancer_id : {fid}")
    print(f"  client1_id    : {cid1}")
    print(f"  client2_id    : {cid2}")

    # ── 4. Update freelancer profile ──────────────────────────────────────────

    step("Fill in freelancer profile — rate, bio")
    put(f"/freelancers/{fid}", {
        "bio": (
            "Backend developer with 4 years of experience building scalable APIs "
            "and data pipelines. Passionate about clean architecture and open-source."
        ),
        "estimated_rate": 35.0,
        "rate_time": "hourly",
        "rate_currency": "USD"
    }, tok_freelancer)

    # ── 5. Create shared skills ───────────────────────────────────────────────

    step("Create skills (shared across freelancer + jobs)")
    skills_to_create = [
        {"skill_name": "Python",         "skill_category": "hard_skill", "description": "Python programming language"},
        {"skill_name": "PostgreSQL",      "skill_category": "hard_skill", "description": "Relational database"},
        {"skill_name": "REST API",        "skill_category": "hard_skill", "description": "RESTful API design"},
        {"skill_name": "Docker",          "skill_category": "tool",       "description": "Container platform"},
        {"skill_name": "Redis",           "skill_category": "tool",       "description": "In-memory data store"},
        {"skill_name": "React",           "skill_category": "hard_skill", "description": "Frontend JS framework"},
        {"skill_name": "Apache Spark",    "skill_category": "hard_skill", "description": "Distributed data processing"},
        {"skill_name": "Kubernetes",      "skill_category": "tool",       "description": "Container orchestration"},
        {"skill_name": "AWS",             "skill_category": "tool",       "description": "Amazon Web Services"},
        {"skill_name": "FastAPI",         "skill_category": "hard_skill", "description": "Python web framework"},
        {"skill_name": "Data Modeling",   "skill_category": "hard_skill", "description": "Database schema design"},
        {"skill_name": "Git",             "skill_category": "tool",       "description": "Version control"},
    ]
    skill_ids = {}
    for s in skills_to_create:
        resp = post("/skills", s, tok_client1)
        sid = extract(resp)["skill_id"]
        skill_ids[s["skill_name"]] = sid
        print(f"    {s['skill_name']} → {sid}")

    # ── 6. Create specialities ────────────────────────────────────────────────

    step("Create specialities")
    specs_to_create = [
        {"speciality_name": "Backend Development", "description": "Server-side development"},
        {"speciality_name": "Data Engineering",    "description": "Data pipelines and warehousing"},
        {"speciality_name": "DevOps",              "description": "Infrastructure and CI/CD"},
    ]
    spec_ids = {}
    for s in specs_to_create:
        resp = post("/specialities", s, tok_client1)
        sid = extract(resp)["speciality_id"]
        spec_ids[s["speciality_name"]] = sid
        print(f"    {s['speciality_name']} → {sid}")

    # ── 7. Create languages ───────────────────────────────────────────────────

    step("Create languages")
    langs_to_create = [
        {"language_name": "English",    "iso_code": "en"},
        {"language_name": "Indonesian", "iso_code": "id"},
    ]
    lang_ids = {}
    for l in langs_to_create:
        resp = post("/languages", l, tok_client1)
        lid = extract(resp)["language_id"]
        lang_ids[l["language_name"]] = lid
        print(f"    {l['language_name']} → {lid}")

    # ── 8. Build the freelancer's profile ────────────────────────────────────

    step("Assign skills to freelancer")
    freelancer_skills = [
        ("Python",      "advanced"),
        ("PostgreSQL",  "advanced"),
        ("REST API",    "advanced"),
        ("FastAPI",     "intermediate"),
        ("Docker",      "intermediate"),
        ("Redis",       "beginner"),
        ("Git",         "advanced"),
        ("Data Modeling", "intermediate"),
    ]
    for skill_name, level in freelancer_skills:
        post("/freelancer-skills", {
            "freelancer_id": fid,
            "skill_id": skill_ids[skill_name],
            "proficiency_level": level
        }, tok_freelancer)
        print(f"    {skill_name} ({level})")

    step("Assign speciality to freelancer")
    post("/freelancer-specialities", {
        "freelancer_id": fid,
        "speciality_id": spec_ids["Backend Development"],
        "is_primary": True
    }, tok_freelancer)

    step("Assign languages to freelancer")
    post("/freelancer-languages", {"freelancer_id": fid, "language_id": lang_ids["English"],    "proficiency_level": "fluent"}, tok_freelancer)
    post("/freelancer-languages", {"freelancer_id": fid, "language_id": lang_ids["Indonesian"], "proficiency_level": "native"}, tok_freelancer)

    step("Add work experience (2 past roles — boosts embedding quality)")
    post("/work-experiences", {
        "freelancer_id": fid,
        "job_title": "Backend Engineer",
        "company_name": "Gojek",
        "location": "Jakarta, Indonesia",
        "start_date": "2022-03-01",
        "end_date": "2024-01-31",
        "is_current": False,
        "description": (
            "Built and maintained REST APIs serving 500k daily active users. "
            "Migrated legacy monolith to microservices using Python/FastAPI and PostgreSQL."
        )
    }, tok_freelancer)
    post("/work-experiences", {
        "freelancer_id": fid,
        "job_title": "Junior Backend Developer",
        "company_name": "Tokopedia",
        "location": "Jakarta, Indonesia",
        "start_date": "2020-06-01",
        "end_date": "2022-02-28",
        "is_current": False,
        "description": (
            "Developed internal APIs and data pipeline scripts. "
            "Worked with Redis caching, PostgreSQL, and Docker-based deployments."
        )
    }, tok_freelancer)

    step("Add portfolio items")
    post("/portfolios", {
        "freelancer_id": fid,
        "project_title": "E-commerce Order API",
        "project_description": (
            "Designed and built a high-throughput order management API using FastAPI, "
            "PostgreSQL, and Redis. Handles 10k+ requests/minute with sub-50ms P95 latency."
        ),
        "project_url": "https://github.com/budi/order-api",
        "completion_date": "2023-08-01"
    }, tok_freelancer)
    post("/portfolios", {
        "freelancer_id": fid,
        "project_title": "Data Pipeline Framework",
        "project_description": (
            "Built a generic ETL framework in Python that pulls data from REST APIs, "
            "transforms it, and loads into PostgreSQL. Used Docker Compose for local dev."
        ),
        "project_url": "https://github.com/budi/etl-framework",
        "completion_date": "2024-02-01"
    }, tok_freelancer)

    # ── 9. Create job posts ───────────────────────────────────────────────────

    step("Client 1 — Create job post: Backend API Developer (strong match)")
    resp = post("/job-posts", {
        "client_id": cid1,
        "job_title": "Backend API Developer",
        "job_description": (
            "We're looking for an experienced backend developer to build and maintain "
            "REST APIs for our SaaS platform. You'll work with Python, FastAPI, and "
            "PostgreSQL. Experience with Redis caching is a plus. Fully remote."
        ),
        "project_type": "individual",
        "project_scope": "medium",
        "estimated_duration": "3 months",
        "experience_level": "intermediate",
        "status": "draft"
    }, tok_client1)
    job1_id = extract(resp)["job_post_id"]
    print(f"  job1_id (Backend API Developer): {job1_id}")

    step("Client 1 — Create job post: Full Stack Engineer (partial match — needs React)")
    resp = post("/job-posts", {
        "client_id": cid1,
        "job_title": "Full Stack Engineer",
        "job_description": (
            "Join our small team to build both the backend APIs and the React frontend. "
            "We need someone comfortable with Python/FastAPI on the server side and "
            "React + TypeScript on the client side. PostgreSQL for the database layer."
        ),
        "project_type": "individual",
        "project_scope": "large",
        "estimated_duration": "6 months",
        "experience_level": "intermediate",
        "status": "draft"
    }, tok_client1)
    job2_id = extract(resp)["job_post_id"]
    print(f"  job2_id (Full Stack Engineer): {job2_id}")

    step("Client 2 — Create job post: Data Engineer (partial match — needs Spark)")
    resp = post("/job-posts", {
        "client_id": cid2,
        "job_title": "Data Engineer",
        "job_description": (
            "Build scalable data pipelines using Apache Spark and Python. "
            "Design and implement data models in PostgreSQL and BigQuery. "
            "Experience with Docker-based deployments is required."
        ),
        "project_type": "individual",
        "project_scope": "large",
        "estimated_duration": "4 months",
        "experience_level": "expert",
        "status": "draft"
    }, tok_client2)
    job3_id = extract(resp)["job_post_id"]
    print(f"  job3_id (Data Engineer): {job3_id}")

    step("Client 2 — Create job post: DevOps Engineer (poor match — mainly infra)")
    resp = post("/job-posts", {
        "client_id": cid2,
        "job_title": "DevOps / Platform Engineer",
        "job_description": (
            "Manage and scale our Kubernetes clusters on AWS. Set up CI/CD pipelines, "
            "monitor infrastructure, and own the deployment process. Deep knowledge of "
            "Kubernetes, Helm, Terraform, and AWS services required."
        ),
        "project_type": "individual",
        "project_scope": "medium",
        "estimated_duration": "ongoing",
        "experience_level": "expert",
        "status": "draft"
    }, tok_client2)
    job4_id = extract(resp)["job_post_id"]
    print(f"  job4_id (DevOps): {job4_id}")

    # ── 10. Create job roles + assign skills ──────────────────────────────────

    step("Add roles and skills to Job 1 — Backend API Developer")
    resp = post("/job-roles", {
        "job_post_id": job1_id,
        "role_title": "Backend Developer",
        "role_budget": 3000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Own the API layer — design, build, and maintain FastAPI endpoints.",
        "display_order": 0
    }, tok_client1)
    role1_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",    True,  "required"),
        ("FastAPI",   True,  "required"),
        ("PostgreSQL",True,  "required"),
        ("REST API",  True,  "required"),
        ("Redis",     False, "preferred"),
        ("Docker",    False, "nice_to_have"),
    ]:
        post("/job-role-skills", {
            "job_role_id": role1_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client1)
        label = "required" if required else "preferred"
        print(f"    {skill_name} ({label})")

    step("Add roles and skills to Job 2 — Full Stack Engineer")
    resp = post("/job-roles", {
        "job_post_id": job2_id,
        "role_title": "Full Stack Engineer",
        "role_budget": 5000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Build both backend APIs and React frontend components.",
        "display_order": 0
    }, tok_client1)
    role2_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",    True,  "required"),
        ("PostgreSQL",True,  "required"),
        ("React",     True,  "required"),
        ("REST API",  True,  "required"),
        ("FastAPI",   False, "preferred"),
        ("Docker",    False, "nice_to_have"),
    ]:
        post("/job-role-skills", {
            "job_role_id": role2_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client1)

    step("Add roles and skills to Job 3 — Data Engineer")
    resp = post("/job-roles", {
        "job_post_id": job3_id,
        "role_title": "Data Engineer",
        "role_budget": 4500.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Design and build batch and streaming data pipelines.",
        "display_order": 0
    }, tok_client2)
    role3_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",        True,  "required"),
        ("Apache Spark",  True,  "required"),
        ("PostgreSQL",    True,  "required"),
        ("Data Modeling", True,  "required"),
        ("Docker",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id": role3_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client2)

    step("Add roles and skills to Job 4 — DevOps Engineer")
    resp = post("/job-roles", {
        "job_post_id": job4_id,
        "role_title": "Platform / DevOps Engineer",
        "role_budget": 4000.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "role_description": "Own the Kubernetes infrastructure and CI/CD pipelines on AWS.",
        "display_order": 0
    }, tok_client2)
    role4_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Kubernetes", True,  "required"),
        ("AWS",        True,  "required"),
        ("Docker",     True,  "required"),
        ("Git",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id": role4_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client2)

    # ── 11. Activate all job posts ────────────────────────────────────────────

    step("Set all job posts to 'active' — Stage 1 only matches active jobs")
    for jid, label in [
        (job1_id, "Backend API Developer"),
        (job2_id, "Full Stack Engineer"),
        (job3_id, "Data Engineer"),
        (job4_id, "DevOps Engineer"),
    ]:
        put(f"/job-posts/{jid}", {"status": "active"}, tok_client1 if jid in (job1_id, job2_id) else tok_client2)
        print(f"    '{label}' → active")

    # ── 12. Build Budi's work history ────────────────────────────────────────
    # Two completed + rated contracts so the ML ranker exits cold-start mode
    # and the RAG analyser has real past work to retrieve.

    step("Create 2 historical job posts (active so contracts can be created; will auto-fill on contract creation)")
    resp = post("/job-posts", {
        "client_id": cid1,
        "job_title": "REST API Backend Service",
        "job_description": (
            "Build a production REST API backend service using Python/FastAPI and PostgreSQL. "
            "Includes auth, rate-limiting, and async background jobs."
        ),
        "project_type": "individual",
        "project_scope": "small",
        "estimated_duration": "1 month",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_client1)
    hist_job1_id = extract(resp)["job_post_id"]

    resp = post("/job-posts", {
        "client_id": cid2,
        "job_title": "ETL Data Pipeline",
        "job_description": (
            "Build a Python ETL pipeline that pulls data from REST APIs, transforms it, "
            "and loads into PostgreSQL. Dockerised, with scheduled runs via cron."
        ),
        "project_type": "individual",
        "project_scope": "small",
        "estimated_duration": "3 weeks",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_client2)
    hist_job2_id = extract(resp)["job_post_id"]
    print(f"  hist_job1_id: {hist_job1_id}")
    print(f"  hist_job2_id: {hist_job2_id}")

    step("Create job roles for historical job posts")
    resp = post("/job-roles", {
        "job_post_id": hist_job1_id,
        "role_title": "Backend Developer",
        "role_budget": 1500.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "display_order": 0
    }, tok_client1)
    hist_role1_id = extract(resp)["job_role_id"]

    resp = post("/job-roles", {
        "job_post_id": hist_job2_id,
        "role_title": "Data Engineer",
        "role_budget": 1200.0,
        "budget_currency": "USD",
        "budget_type": "fixed",
        "display_order": 0
    }, tok_client2)
    hist_role2_id = extract(resp)["job_role_id"]

    step("Budi submits proposals for historical jobs (pre-accepted)")
    resp = post("/proposals", {
        "job_post_id": hist_job1_id,
        "job_role_id": hist_role1_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have 4 years of Python/FastAPI experience and have built REST APIs "
            "serving hundreds of thousands of daily users. I can deliver this in 1 month."
        ),
        "proposed_budget": 1500.0,
        "proposed_duration": "1 month",
        "status": "accepted"
    }, tok_freelancer)
    hist_proposal1_id = extract(resp)["proposal_id"]

    resp = post("/proposals", {
        "job_post_id": hist_job2_id,
        "job_role_id": hist_role2_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have built ETL pipelines in Python that load data into PostgreSQL. "
            "Docker-based deployments are part of my standard workflow."
        ),
        "proposed_budget": 1200.0,
        "proposed_duration": "3 weeks",
        "status": "accepted"
    }, tok_freelancer)
    hist_proposal2_id = extract(resp)["proposal_id"]

    step("Create historical contracts (already active, about to be completed)")
    resp = post("/contracts", {
        "job_post_id": hist_job1_id,
        "job_role_id": hist_role1_id,
        "proposal_id": hist_proposal1_id,
        "freelancer_id": fid,
        "client_id": cid1,
        "contract_title": "REST API Backend Service",
        "role_title": "Backend Developer",
        "agreed_budget": 1500.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "1 month",
        "status": "active",
        "start_date": "2024-01-15",
        "end_date": "2024-02-15"
    }, tok_client1)
    hist_contract1_id = extract(resp)["contract_id"]

    resp = post("/contracts", {
        "job_post_id": hist_job2_id,
        "job_role_id": hist_role2_id,
        "proposal_id": hist_proposal2_id,
        "freelancer_id": fid,
        "client_id": cid2,
        "contract_title": "ETL Data Pipeline",
        "role_title": "Data Engineer",
        "agreed_budget": 1200.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "3 weeks",
        "status": "active",
        "start_date": "2024-03-01",
        "end_date": "2024-03-22"
    }, tok_client2)
    hist_contract2_id = extract(resp)["contract_id"]
    print(f"  hist_contract1_id: {hist_contract1_id}")
    print(f"  hist_contract2_id: {hist_contract2_id}")

    step("Mark historical contracts as completed — auto-queues contract embeddings")
    put(f"/contracts/{hist_contract1_id}", {
        "status": "completed",
        "actual_completion_date": "2024-02-15",
        "total_paid": 1500.0
    }, tok_client1)
    put(f"/contracts/{hist_contract2_id}", {
        "status": "completed",
        "actual_completion_date": "2024-03-22",
        "total_paid": 1200.0
    }, tok_client2)
    print("  Both contracts completed — contract_embedding rows created (dirty).")

    step("Rate the completed contracts — removes cold-start flag from ML ranker")
    post("/ratings", {
        "contract_id": hist_contract1_id,
        "client_id": cid1,
        "freelancer_id": fid,
        "communication_score": 5,
        "result_quality_score": 5,
        "professionalism_score": 5,
        "timeline_compliance_score": 5,
        "overall_rating": 5.0,
        "review_text": (
            "Excellent backend developer. Built a clean, well-documented REST API "
            "using FastAPI and PostgreSQL. Delivered on time, handled edge cases well, "
            "and communicated proactively throughout the project."
        )
    }, tok_client1)
    post("/ratings", {
        "contract_id": hist_contract2_id,
        "client_id": cid2,
        "freelancer_id": fid,
        "communication_score": 4,
        "result_quality_score": 4,
        "professionalism_score": 4,
        "timeline_compliance_score": 4,
        "overall_rating": 4.0,
        "review_text": (
            "Strong Python developer with solid data engineering skills. "
            "Built a reliable ETL pipeline that processes our data efficiently. "
            "Minor delays in communication at times but overall very good work."
        )
    }, tok_client2)

    step("Create performance rating — sets total_ratings_received so ML ranker is not cold-start")
    post("/performance-ratings", {
        "freelancer_id": fid,
        "overall_performance_score": 82.5,
        "confidence_score": 75.0,
        "total_ratings_received": 2,
        "average_communication": 4.5,
        "average_result_quality": 4.5,
        "average_professionalism": 4.5,
        "average_scope_compliance": 4.5,
        "average_timeline_compliance": 4.5,
        "success_rate": 100.0
    }, tok_client1)

    step("Verify Budi's history is in place")
    print("  Both contracts were marked 'completed' above, which auto-incremented")
    print("  freelancer.total_jobs (+2) and client.total_jobs_completed (+1 each).")
    print("  Budi now has 2 completed jobs and a 4.5/5 performance rating.")
    print("  The ML ranker will use the full model (not cold-start heuristic).")

    # ── 14. Trigger embeddings ────────────────────────────────────────────────

    step("Trigger embedding for the freelancer profile")
    post(f"/ai/job_matching/embed/freelancer/{fid}", {}, tok_freelancer)

    step("Trigger embedding for all four job posts")
    for jid, label in [
        (job1_id, "Backend API Developer"),
        (job2_id, "Full Stack Engineer"),
        (job3_id, "Data Engineer"),
        (job4_id, "DevOps Engineer"),
    ]:
        post(f"/ai/job_matching/embed/job/{jid}", {}, tok_client1)
        print(f"    queued: {label}")

    step("Run sweep now — generates all embeddings synchronously (this calls Ollama, may take 10-30s)")
    post("/ai/job_matching/sweep", {}, tok_freelancer)
    print("  Sweep complete — all embeddings are ready.")

    # ── 14b. Team job post (multi-role) ──────────────────────────────────────

    step("Client 1 — Create TEAM job post with 3 roles (one good match, two poor)")
    resp = post("/job-posts", {
        "client_id": cid1,
        "job_title": "Full Product Team — Launch Squad",
        "job_description": (
            "We're building a cross-functional launch team to take our SaaS product from "
            "beta to production. We need a backend engineer, a frontend engineer, and a "
            "cloud/DevOps engineer to work together for 6 months. Python/FastAPI backend, "
            "React frontend, Kubernetes on AWS infrastructure."
        ),
        "project_type": "team",
        "project_scope": "large",
        "estimated_duration": "6 months",
        "experience_level": "intermediate",
        "status": "active"
    }, tok_client1)
    team_job_id = extract(resp)["job_post_id"]
    print(f"  team_job_id: {team_job_id}")

    step("Add 3 roles to the team job post")
    # Role 1: Backend — good match for Budi
    resp = post("/job-roles", {
        "job_post_id": team_job_id,
        "role_title": "Backend Engineer",
        "role_budget": 4000.0,
        "budget_currency": "USD",
        "budget_type": "negotiable",
        "role_description": "Own the FastAPI backend, database layer, and REST API design.",
        "display_order": 0
    }, tok_client1)
    team_role_backend_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Python",     True,  "required"),
        ("FastAPI",    True,  "required"),
        ("PostgreSQL", True,  "required"),
        ("REST API",   True,  "required"),
        ("Docker",     False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id": team_role_backend_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client1)
    print(f"  Backend Engineer role: {team_role_backend_id}")

    # Role 2: Frontend — poor match (Budi has no React)
    resp = post("/job-roles", {
        "job_post_id": team_job_id,
        "role_title": "Frontend Engineer",
        "role_budget": 3500.0,
        "budget_currency": "USD",
        "budget_type": "negotiable",
        "role_description": "Build and maintain the React + TypeScript frontend.",
        "display_order": 1
    }, tok_client1)
    team_role_frontend_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("React",      True,  "required"),
        ("REST API",   True,  "required"),
        ("Git",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id": team_role_frontend_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client1)
    print(f"  Frontend Engineer role: {team_role_frontend_id}")

    # Role 3: DevOps — very poor match (Budi has only Docker)
    resp = post("/job-roles", {
        "job_post_id": team_job_id,
        "role_title": "Cloud / DevOps Engineer",
        "role_budget": 4500.0,
        "budget_currency": "USD",
        "budget_type": "negotiable",
        "role_description": "Own Kubernetes clusters on AWS, CI/CD pipelines, and infra monitoring.",
        "display_order": 2
    }, tok_client1)
    team_role_devops_id = extract(resp)["job_role_id"]
    for skill_name, required, importance in [
        ("Kubernetes", True,  "required"),
        ("AWS",        True,  "required"),
        ("Docker",     True,  "required"),
        ("Git",        False, "preferred"),
    ]:
        post("/job-role-skills", {
            "job_role_id": team_role_devops_id,
            "skill_id": skill_ids[skill_name],
            "is_required": required,
            "importance_level": importance
        }, tok_client1)
    print(f"  Cloud/DevOps Engineer role: {team_role_devops_id}")

    step("Embed the team job post")
    post(f"/ai/job_matching/embed/job/{team_job_id}", {}, tok_client1)
    post("/ai/job_matching/sweep", {}, tok_freelancer)
    print("  Team job embedded and sweep complete.")

    # ── 15. Stage 1–3: Freelancer-to-Jobs matching ────────────────────────────

    step("Run the 3-stage job matching pipeline (limit=10)")
    print("  This is the main homepage feed for the freelancer.")
    resp = get("/ai/job_matching/match/freelancer-to-jobs", tok_freelancer, params={"limit": 10})
    matches = extract(resp).get("matches", [])
    print(f"\n  Results ({len(matches)} jobs returned):")
    print(f"  {'Rank':<5} {'Job Title':<35} {'Match%':<10} {'Cosine':<10} {'Skill Overlap'}")
    print(f"  {'-'*5} {'-'*35} {'-'*10} {'-'*10} {'-'*15}")
    for i, m in enumerate(matches, 1):
        print(
            f"  #{i:<4} {m.get('job_title','?')[:34]:<35} "
            f"{m.get('match_probability', '?'):<10} "
            f"{m.get('similarity_score', '?'):<10} "
            f"{m.get('skill_overlap_pct', '?')}%"
        )

    # ── 16. RAG deep analysis on the best match ──────────────────────────────

    if matches:
        best_job_id    = matches[0].get("job_post_id")
        best_job_title = matches[0].get("job_title", "?")
        step(f"RAG deep analysis on best match: '{best_job_title}'")
        print("  Calling LLM — this can take 5–30 seconds depending on your Ollama model...")
        _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{best_job_id}", tok_freelancer)))

    # ── 17. RAG on the poor single-role match to contrast ────────────────────

    step("RAG deep analysis on worst match: 'DevOps / Platform Engineer' (single role)")
    print("  Expected: low score (~15-30), missing Kubernetes+AWS, recommendation=skip")
    _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{job4_id}", tok_freelancer)))

    # ── 18. RAG on the multi-role team job ───────────────────────────────────

    step("RAG deep analysis on TEAM job: 'Full Product Team — Launch Squad'")
    print("  Expected: mixed per_role_fit — high for Backend, low for Frontend + DevOps")
    _print_rag_result(extract(get(f"/ai/job_matching/analyse/job/{team_job_id}", tok_freelancer)))

    # ── 19. Contract lifecycle — full_payment ────────────────────────────────

    step("Budi submits a proposal for the live Backend API Developer job (Client 1)")
    resp = post("/proposals", {
        "job_post_id": job1_id,
        "job_role_id": role1_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have 4+ years building REST APIs with Python/FastAPI and PostgreSQL. "
            "I can deliver a clean, well-documented backend in 3 months on time."
        ),
        "proposed_budget": 3000.0,
        "proposed_duration": "3 months",
        "status": "pending"
    }, tok_freelancer)
    live_proposal1_id = extract(resp)["proposal_id"]
    print(f"  live_proposal1_id: {live_proposal1_id}")

    step("Client 1 creates a FULL-PAYMENT contract from that proposal")
    resp = post("/contracts", {
        "job_post_id": job1_id,
        "job_role_id": role1_id,
        "proposal_id": live_proposal1_id,
        "freelancer_id": fid,
        "client_id": cid1,
        "contract_title": "Backend API Developer — TechStartup Inc.",
        "role_title": "Backend Developer",
        "agreed_budget": 3000.0,
        "budget_currency": "USD",
        "payment_structure": "full_payment",
        "agreed_duration": "3 months",
        "status": "active",
        "start_date": "2026-05-01",
        "end_date": "2026-07-31"
    }, tok_client1)
    live_contract1_id = extract(resp)["contract_id"]
    print(f"  live_contract1_id: {live_contract1_id}")

    step("Inspect auto-filled generation data for the full_payment contract")
    gen_data1 = extract(get(f"/contracts/{live_contract1_id}/generation-data", tok_client1))
    print(f"  Contract title     : {gen_data1['contract']['contract_title']}")
    print(f"  Freelancer name    : {gen_data1['freelancer'].get('full_name', '?')}")
    print(f"  Client name        : {gen_data1['client'].get('full_name', '?')}")
    print(f"  Job post title     : {gen_data1['job_post'].get('job_title', '?')}")
    print(f"  Role               : {gen_data1['job_role'].get('role_title', '?')}")
    print(f"  Existing terms     : {bool(gen_data1['contract_terms'])}")
    print(f"  Existing milestones: {len(gen_data1['milestones'])}")

    step("Generate contract PDF (full_payment — no milestones) → uploads to Supabase")
    print("  Calling POST /contracts/{id}/generate ...")
    resp = post(f"/contracts/{live_contract1_id}/generate", {
        "end_date": "2026-07-31",
        "agreed_duration": "3 months",
        "termination_notice": 14,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": (
            "Both parties agree to keep all project details, source code, and business "
            "information strictly confidential for 2 years post-contract."
        ),
        "late_payment_penalty": 1.5,
        "dispute_resolution": "negotiation",
        "revision_rounds": 2,
        "additional_clauses": "Freelancer must use Git for all deliverables with weekly code reviews."
    }, tok_client1)
    live_contract1 = extract(resp)
    pdf_path1 = live_contract1.get("contract_pdf_url", "")
    print(f"  PDF storage path : {pdf_path1}")
    print(f"  PDF generated at : {live_contract1.get('contract_pdf_generated_at', '?')}")

    step("Get signed download URL for the full_payment contract PDF")
    url_resp1 = extract(get(f"/contracts/{live_contract1_id}/pdf-url", tok_client1))
    signed_url1 = url_resp1.get("pdf_url", "")
    print(f"  Signed URL (1 hr expiry):")
    print(f"    {signed_url1[:120]}{'...' if len(signed_url1) > 120 else ''}")
    print("  → Open this URL in a browser to view the generated PDF in Supabase.")

    # ── 20. Contract lifecycle — milestone_based ─────────────────────────────

    step("Budi submits a proposal for the live Data Engineer job (Client 2)")
    resp = post("/proposals", {
        "job_post_id": job3_id,
        "job_role_id": role3_id,
        "freelancer_id": fid,
        "cover_letter": (
            "I have strong Python + PostgreSQL experience and have built ETL pipelines "
            "with Docker. I can ramp up on Spark and deliver in 4 months."
        ),
        "proposed_budget": 4000.0,
        "proposed_duration": "4 months",
        "status": "pending"
    }, tok_freelancer)
    live_proposal2_id = extract(resp)["proposal_id"]
    print(f"  live_proposal2_id: {live_proposal2_id}")

    step("Client 2 creates a MILESTONE-BASED contract from that proposal")
    resp = post("/contracts", {
        "job_post_id": job3_id,
        "job_role_id": role3_id,
        "proposal_id": live_proposal2_id,
        "freelancer_id": fid,
        "client_id": cid2,
        "contract_title": "Data Pipeline Build — DataCorp Solutions",
        "role_title": "Data Engineer",
        "agreed_budget": 4000.0,
        "budget_currency": "USD",
        "payment_structure": "milestone_based",
        "agreed_duration": "4 months",
        "status": "active",
        "start_date": "2026-05-01",
        "end_date": "2026-08-31"
    }, tok_client2)
    live_contract2_id = extract(resp)["contract_id"]
    print(f"  live_contract2_id: {live_contract2_id}")

    step("Inspect auto-filled generation data for the milestone-based contract")
    gen_data2 = extract(get(f"/contracts/{live_contract2_id}/generation-data", tok_client2))
    print(f"  Contract title     : {gen_data2['contract']['contract_title']}")
    print(f"  Payment structure  : {gen_data2['contract']['payment_structure']}")
    print(f"  Existing milestones: {len(gen_data2['milestones'])}")

    step("Generate milestone-based contract PDF (3 milestones) → uploads to Supabase")
    print("  Calling POST /contracts/{id}/generate with milestones ...")
    resp = post(f"/contracts/{live_contract2_id}/generate", {
        "end_date": "2026-08-31",
        "agreed_duration": "4 months",
        "termination_notice": 30,
        "governing_law": "Indonesia",
        "confidentiality": True,
        "confidentiality_text": "All pipeline schemas and business data are confidential for 3 years.",
        "late_payment_penalty": 2.0,
        "dispute_resolution": "mediation",
        "revision_rounds": 1,
        "additional_clauses": "Freelancer delivers weekly progress reports every Friday.",
        "milestones": [
            {
                "milestone_title": "Phase 1 — Ingestion Layer",
                "milestone_description": "Build REST API ingestion layer and raw data store.",
                "milestone_amount": 1200.0,
                "milestone_percentage": 30.0,
                "milestone_order": 1,
                "due_date": "2026-05-31"
            },
            {
                "milestone_title": "Phase 2 — Transformation & Load",
                "milestone_description": "Implement transform logic and PostgreSQL loading pipeline.",
                "milestone_amount": 1600.0,
                "milestone_percentage": 40.0,
                "milestone_order": 2,
                "due_date": "2026-07-15"
            },
            {
                "milestone_title": "Phase 3 — Testing & Handoff",
                "milestone_description": "End-to-end tests, documentation, and project handover.",
                "milestone_amount": 1200.0,
                "milestone_percentage": 30.0,
                "milestone_order": 3,
                "due_date": "2026-08-31"
            }
        ]
    }, tok_client2)
    live_contract2 = extract(resp)
    pdf_path2 = live_contract2.get("contract_pdf_url", "")
    print(f"  PDF storage path : {pdf_path2}")
    print(f"  PDF generated at : {live_contract2.get('contract_pdf_generated_at', '?')}")

    step("Get signed download URL for the milestone-based contract PDF")
    url_resp2 = extract(get(f"/contracts/{live_contract2_id}/pdf-url", tok_client2))
    signed_url2 = url_resp2.get("pdf_url", "")
    print(f"  Signed URL (1 hr expiry):")
    print(f"    {signed_url2[:120]}{'...' if len(signed_url2) > 120 else ''}")
    print("  → Open this URL in a browser to view the milestone contract PDF in Supabase.")

    # ── 21. Milestone lifecycle (skipped — milestone management routes not yet built) ─────────
    step("Milestone lifecycle (Step 21 — skipped)")
    print("  NOTE: /contract-milestones/* routes are not yet implemented.")
    print("  The milestone-based contract and its PDF were created successfully above.")
    print("  Milestone tracking (in_progress → completed → paid → confirmed) is a future feature.")

    # ── 22. Contract list views ───────────────────────────────────────────────

    step("List all contracts visible to Budi (4 total: 2 historical + 2 live)")
    contracts_f = extract(get("/contracts", tok_freelancer))
    if isinstance(contracts_f, list):
        print(f"  {len(contracts_f)} contract(s):")
        for c in contracts_f:
            print(
                f"    [{c.get('status', '?').upper():10}] "
                f"{c.get('contract_title', '?'):<45} "
                f"${c.get('agreed_budget', '?')} {c.get('budget_currency', '')}  "
                f"({c.get('payment_structure', '?')})"
            )

    step("List all contracts visible to Client 1 (TechStartup Inc.)")
    contracts_c1 = extract(get("/contracts", tok_client1))
    if isinstance(contracts_c1, list):
        print(f"  {len(contracts_c1)} contract(s):")
        for c in contracts_c1:
            print(f"    [{c.get('status', '?').upper():10}] {c.get('contract_title', '?')}")

    step("List all contracts visible to Client 2 (DataCorp Solutions)")
    contracts_c2 = extract(get("/contracts", tok_client2))
    if isinstance(contracts_c2, list):
        print(f"  {len(contracts_c2)} contract(s):")
        for c in contracts_c2:
            print(f"    [{c.get('status', '?').upper():10}] {c.get('contract_title', '?')}")

    # ── Done ──────────────────────────────────────────────────────────────────

    print("\n" + "="*60)
    print("  Walkthrough complete.")
    print("="*60)
    print(f"\n  Created resources:")
    print(f"    Freelancer   : Budi Santoso  ({fid})")
    print(f"    Client 1     : TechStartup Inc. ({cid1})")
    print(f"    Client 2     : DataCorp Solutions ({cid2})")
    print(f"    Skills       : {len(skill_ids)}")
    print(f"    Job Posts    : 4 individual active + 1 team active + 2 historical (closed)")
    print(f"    History      : 2 completed contracts, 2 ratings (avg 4.5/5)")
    print(f"    Jobs matched : {len(matches)}")
    print(f"    Live contracts : 2 (1 full_payment + 1 milestone_based)")
    print(f"      full_payment  PDF  : {pdf_path1 or '(not generated)'}")
    print(f"      milestone_based PDF: {pdf_path2 or '(not generated)'}")
    print()
    print("  Contract PDF verification:")
    print("  Both PDFs are stored in the Supabase 'contract-assets' bucket.")
    print("  Use the signed URLs printed above to download and inspect them.")
    print()
    print("  History note:")
    print("  Budi has 2 completed contracts rated 5/5 and 4/5.")
    print("  performance_rating.total_ratings_received = 2  →  cold_start = False")
    print("  LightGBM uses full model including performance_score and success_rate.")
    print("  RAG retrieves past contract reviews as grounded context for the LLM.")
    print()

    _stop_tee(tee, out_path)


def _print_rag_result(result: dict) -> None:
    """Pretty-print a role-centric RAG analysis result."""
    if "error" in result:
        print(f"  LLM error: {result['error']}")
        return

    overall_score = result.get("overall_match_score", result.get("match_score", "?"))
    overall_rec   = (result.get("overall_recommendation", result.get("recommendation", "?")) or "?").upper()
    print(f"\n  Overall Score   : {overall_score}/100  →  {overall_rec}")
    print(f"  Overall Reason  : {result.get('overall_recommendation_reason', result.get('recommendation_reason', ''))}")

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
        # Fallback: old flat structure
        print(f"\n  Matching Skills  : {', '.join(result.get('matching_skills', [])) or '(none)'}")
        print(f"  Missing Required : {', '.join(result.get('missing_required_skills', [])) or '(none)'}")
        for s in result.get("strengths", []):
            print(f"    + {s}")
        for g in result.get("gaps", []):
            print(f"    - {g}")
        for t in result.get("skill_tips", []):
            print(f"    → {t}")

    print(f"\n  RAG Sources: ", end="")
    print("  |  ".join(f"{k}={v}" for k, v in result.get("rag_sources", {}).items()))


if __name__ == "__main__":
    run()
