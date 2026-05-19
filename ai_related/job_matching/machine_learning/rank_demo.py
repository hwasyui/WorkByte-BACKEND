#!/usr/bin/env python3
"""
rank_demo.py
============
Standalone job-ranking demo for the homepage job recommendation system.

Ranks 40 synthetic job posts (across 15 domains) against a single freelancer
profile using the trained CatBoost model (job_recommender.pkl).

- No backend routes touched.
- No API calls. Embeddings computed locally with BAAI/bge-base-en-v1.5
  (sentence-transformers). Same model as GENERATE_DATA.ipynb and production.

Run from any directory:
    python3 rank_demo.py

Requirements (already in Docker):
    pip install catboost sentence-transformers numpy pandas
"""

import math
import pickle
import time
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

# Locate files
_HERE  = Path(__file__).resolve().parent
_PKL   = _HERE / "models" / "job_recommender.pkl"
_FEATS = _HERE / "models" / "feature_cols.json"

# Sentence-transformers embedding
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    sys.exit("ERROR: sentence-transformers not installed.\nRun: pip install sentence-transformers")

_EMBED_MODEL_NAME = "BAAI/bge-base-en-v1.5"
_embed_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        print(f"  Loading {_EMBED_MODEL_NAME} (downloads ~120 MB on first run) …")
        _embed_model = SentenceTransformer(_EMBED_MODEL_NAME)
        print(f"  ✓ Model loaded (dim=768)")
    return _embed_model


def _embed_batch(texts: list[str]) -> list[np.ndarray]:
    """Embed a list of texts locally with BAAI/bge-base-en-v1.5."""
    model = _get_model()
    vecs = model.encode(
        texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [np.array(v, dtype=np.float32) for v in vecs]


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


# Constants
TODAY  = date.today()
LAMBDA = math.log(2) / 24   # 24-month half-life for recency decay

PROFICIENCY = {
    "expert": 1.00, "advanced": 0.75,
    "intermediate": 0.50, "beginner": 0.30,
}

# Freelancer profile
#
# Python / FastAPI backend developer — 4 years experience, 7 platform jobs.
# Rate: $45/hr → $7,200/month.
#
FREELANCER = {
    "bio": (
        "Backend developer with 4 years of experience building scalable REST APIs "
        "and microservices using Python and FastAPI. Proficient in PostgreSQL database "
        "design and query optimisation, Redis caching, and containerised deployments "
        "with Docker. Skilled with SQLAlchemy ORM and writing clean, well-tested code. "
        "Previously worked 2 years at a fintech startup on payment processing APIs."
    ),
    # skill → proficiency label (used to look up weight)
    "skills": {
        "Python":          "advanced",
        "FastAPI":         "advanced",
        "PostgreSQL":      "advanced",
        "REST API":        "expert",
        "SQLAlchemy":      "intermediate",
        "Redis":           "intermediate",
        "Docker":          "intermediate",
        "Git":             "advanced",
        "Python Testing":  "intermediate",
        "SQL":             "advanced",
    },
    "specialities":    ["Backend Development", "API Development"],
    "hourly_rate_usd": 45.0,     # → 45 × 160 = $7,200 / month
    "total_jobs":      7,         # platform-completed jobs → intermediate
    "work_exp_count":  2,         # employment history entries
    "portfolio": [
        {
            "text": (
                "Built a high-throughput order-processing microservice using FastAPI and "
                "PostgreSQL for a fintech client — handled 10K events/day, sub-50ms p99 "
                "latency, deployed on Docker with health checks and auto-restart."
            ),
            "completion_date": date(2025, 8, 15),
            "is_manual": True,
        },
        {
            "text": (
                "Optimised slow PostgreSQL queries for a SaaS analytics platform — "
                "reduced average query time from 800 ms to 40 ms using composite "
                "indexes and materialised views. Delivered as a code audit + PR."
            ),
            "completion_date": date(2025, 3, 10),
            "is_manual": True,
        },
    ],
}

# Derived fields
def _exp_level(total_jobs: int) -> int:
    if total_jobs < 3:  return 1   # entry
    if total_jobs < 10: return 2   # intermediate
    return 3                        # expert

FREELANCER["exp_num"]      = _exp_level(FREELANCER["total_jobs"])
FREELANCER["monthly_usd"]  = FREELANCER["hourly_rate_usd"] * 160.0
FREELANCER["skill_weights"] = {
    s: PROFICIENCY.get(p, 0.50)
    for s, p in FREELANCER["skills"].items()
}

# Recency: most recent portfolio completion date
_latest = max(p["completion_date"] for p in FREELANCER["portfolio"])
_months_ago = (TODAY.year - _latest.year) * 12 + TODAY.month - _latest.month
FREELANCER["recency_score"] = math.exp(-LAMBDA * _months_ago)

# 40 job posts
JOBS = [
    # Backend Python / FastAPI (high fit)
    {
        "id": 1, "domain": "Backend",
        "title": "Python FastAPI Backend Developer",
        "description": (
            "We need an experienced Python FastAPI developer to build scalable RESTful "
            "APIs for our B2B SaaS platform. Work with PostgreSQL, implement Redis "
            "caching, deploy with Docker. Strong async Python and test coverage required."
        ),
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis"],
        "preferred_skills": ["SQLAlchemy", "Python Testing", "Git"],
        "budget_monthly_usd": 8000, "exp_level": 2,
    },
    {
        "id": 2, "domain": "Backend",
        "title": "Senior Python Backend Engineer",
        "description": (
            "Senior backend role building Python microservices and REST APIs for a "
            "high-traffic e-commerce platform. Deep expertise in PostgreSQL, API design, "
            "and production deployment expected. Mentor junior developers."
        ),
        "required_skills": ["Python", "PostgreSQL", "REST API", "Git", "Docker"],
        "preferred_skills": ["FastAPI", "Redis", "SQLAlchemy"],
        "budget_monthly_usd": 9500, "exp_level": 3,
    },
    {
        "id": 3, "domain": "Backend",
        "title": "API Developer (Python / FastAPI)",
        "description": (
            "Build and maintain REST APIs using Python and FastAPI. SQLAlchemy ORM "
            "for all database interactions, PostgreSQL backend, containerised with "
            "Docker. Clean code, type hints, and unit test coverage expected."
        ),
        "required_skills": ["Python", "FastAPI", "SQLAlchemy", "REST API"],
        "preferred_skills": ["PostgreSQL", "Redis", "Git", "Python Testing"],
        "budget_monthly_usd": 6500, "exp_level": 2,
    },
    {
        "id": 4, "domain": "Backend",
        "title": "Python Microservices Engineer",
        "description": (
            "Design and implement microservices architecture in Python. Must be "
            "proficient in containerisation with Docker, asynchronous programming, "
            "and PostgreSQL database management. Performance-critical systems."
        ),
        "required_skills": ["Python", "Docker", "PostgreSQL", "REST API"],
        "preferred_skills": ["FastAPI", "Redis", "SQLAlchemy", "Python Testing"],
        "budget_monthly_usd": 10000, "exp_level": 3,
    },
    {
        "id": 5, "domain": "Backend",
        "title": "Junior Backend Developer (Python)",
        "description": (
            "Entry-level Python developer to help build RESTful APIs for an internal "
            "tool. Knowledge of Python, basic SQL, and Git version control. "
            "Mentorship provided by senior engineers."
        ),
        "required_skills": ["Python", "REST API", "SQL", "Git"],
        "preferred_skills": ["FastAPI", "PostgreSQL"],
        "budget_monthly_usd": 3500, "exp_level": 1,
    },

    # Python-adjacent domains (partial semantic + skill fit)
    {
        "id": 6, "domain": "Data Engineering",
        "title": "Data Engineer (Python / SQL)",
        "description": (
            "Build ETL pipelines and data infrastructure using Python and SQL. "
            "PostgreSQL experience essential. Apache Airflow for orchestration, "
            "Apache Spark for large-scale processing preferred."
        ),
        "required_skills": ["Python", "SQL", "PostgreSQL"],
        "preferred_skills": ["Redis", "Docker", "Git"],
        "budget_monthly_usd": 8500, "exp_level": 2,
    },
    {
        "id": 7, "domain": "Backend",
        "title": "Django Backend Developer",
        "description": (
            "Looking for a Django developer experienced with PostgreSQL. REST API "
            "design, Redis caching layer, Docker deployments. Python proficiency "
            "and clean code practices required."
        ),
        "required_skills": ["Python", "PostgreSQL", "REST API", "Redis"],
        "preferred_skills": ["Docker", "Git", "SQL"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },
    {
        "id": 8, "domain": "Machine Learning",
        "title": "ML Engineer (Python)",
        "description": (
            "Build and deploy machine learning models using Python. Requires hands-on "
            "TensorFlow or PyTorch experience, strong Python proficiency, and SQL data "
            "access. PostgreSQL and Docker preferred for serving infrastructure."
        ),
        "required_skills": ["Python", "Machine Learning", "PostgreSQL", "SQL"],
        "preferred_skills": ["Docker", "Git", "REST API"],
        "budget_monthly_usd": 10000, "exp_level": 3,
    },
    {
        "id": 9, "domain": "Full-Stack",
        "title": "Full-Stack Developer (Python + React)",
        "description": (
            "Full-stack role: FastAPI backend and React frontend. PostgreSQL, Redis, "
            "Docker. Good test coverage expected. Will lead the API design decisions."
        ),
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "REST API"],
        "preferred_skills": ["Redis", "Docker", "Git", "SQLAlchemy"],
        "budget_monthly_usd": 8000, "exp_level": 2,
    },
    {
        "id": 10, "domain": "DevOps",
        "title": "DevOps Engineer (Python / Docker)",
        "description": (
            "Automate CI/CD pipelines, manage Docker containers, write Python tooling "
            "scripts. Cloud deployment on AWS/GCP. PostgreSQL DBA knowledge a bonus. "
            "Strong scripting and Git workflow required."
        ),
        "required_skills": ["Docker", "Python", "Git"],
        "preferred_skills": ["PostgreSQL", "REST API", "Redis"],
        "budget_monthly_usd": 7500, "exp_level": 2,
    },

    # Other backend languages (semantic match but skill mismatch)
    {
        "id": 11, "domain": "Backend",
        "title": "Node.js Backend Developer",
        "description": (
            "Build REST APIs with Node.js and Express. MongoDB for primary storage, "
            "Redis for caching. Microservices architecture, Docker containerisation. "
            "TypeScript preferred."
        ),
        "required_skills": ["Node.js", "Express", "MongoDB", "REST API", "Docker"],
        "preferred_skills": ["Redis", "TypeScript", "Git"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },
    {
        "id": 12, "domain": "Backend",
        "title": "Go Backend Engineer",
        "description": (
            "Develop high-performance gRPC and REST services in Go. PostgreSQL "
            "database, Docker deployments, strong engineering fundamentals. "
            "Experience with concurrency patterns required."
        ),
        "required_skills": ["Go", "gRPC", "PostgreSQL", "Docker"],
        "preferred_skills": ["REST API", "Redis", "Git"],
        "budget_monthly_usd": 9000, "exp_level": 3,
    },
    {
        "id": 13, "domain": "Backend",
        "title": "Java Spring Boot Developer",
        "description": (
            "Senior Java Spring Boot developer for enterprise-grade APIs. PostgreSQL, "
            "Docker, REST API design. Microservices architecture. Hibernate ORM."
        ),
        "required_skills": ["Java", "Spring Boot", "PostgreSQL", "REST API", "Docker"],
        "preferred_skills": ["Git", "Redis"],
        "budget_monthly_usd": 8500, "exp_level": 2,
    },
    {
        "id": 14, "domain": "Backend",
        "title": "Ruby on Rails Developer",
        "description": (
            "Build web applications using Ruby on Rails. PostgreSQL as the database, "
            "Redis for caching, Docker for containerised deployment. TDD practices."
        ),
        "required_skills": ["Ruby", "Rails", "PostgreSQL", "Redis"],
        "preferred_skills": ["Docker", "Git"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },
    {
        "id": 15, "domain": "Backend",
        "title": "PHP Laravel Developer",
        "description": (
            "Laravel developer for REST API backend. MySQL, Redis, basic Docker setup. "
            "Entry to mid-level, collaborative team environment."
        ),
        "required_skills": ["PHP", "Laravel", "MySQL", "Redis"],
        "preferred_skills": ["Docker", "Git"],
        "budget_monthly_usd": 4500, "exp_level": 1,
    },

    # Frontend (semantic mismatch + near-zero skill overlap)
    {
        "id": 16, "domain": "Frontend",
        "title": "React Frontend Developer",
        "description": (
            "Build responsive, accessible React applications with TypeScript. "
            "Component libraries, CSS animations, and REST API integration. "
            "Next.js and Storybook preferred."
        ),
        "required_skills": ["React", "TypeScript", "CSS", "HTML"],
        "preferred_skills": ["Next.js", "Git", "REST API"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },
    {
        "id": 17, "domain": "Frontend",
        "title": "Vue.js Frontend Developer",
        "description": (
            "Develop Vue 3 SPAs with Pinia state management. JavaScript, CSS, HTML, "
            "REST API consumption. Entry to mid level. Design system experience helpful."
        ),
        "required_skills": ["Vue.js", "JavaScript", "CSS", "HTML"],
        "preferred_skills": ["REST API", "Git", "TypeScript"],
        "budget_monthly_usd": 5500, "exp_level": 1,
    },
    {
        "id": 18, "domain": "Frontend",
        "title": "Angular Developer",
        "description": (
            "Angular 17 applications with TypeScript and RxJS. Strong frontend "
            "engineering, REST API integration, NgRx state management. Enterprise scale."
        ),
        "required_skills": ["Angular", "TypeScript", "RxJS", "HTML"],
        "preferred_skills": ["REST API", "Git", "CSS"],
        "budget_monthly_usd": 7500, "exp_level": 2,
    },
    {
        "id": 19, "domain": "Frontend",
        "title": "UI Developer (HTML / CSS / JS)",
        "description": (
            "Pixel-perfect HTML/CSS/JavaScript implementation from Figma designs. "
            "Responsive design, cross-browser compatibility. React or Vue a bonus."
        ),
        "required_skills": ["HTML", "CSS", "JavaScript"],
        "preferred_skills": ["React", "Figma", "Git"],
        "budget_monthly_usd": 5000, "exp_level": 1,
    },

    # Mobile (completely different domain)
    {
        "id": 20, "domain": "Mobile",
        "title": "iOS Developer (Swift)",
        "description": (
            "Expert iOS developer using Swift and Xcode. UIKit and SwiftUI for UI, "
            "Combine for reactive patterns, App Store submission experience required."
        ),
        "required_skills": ["Swift", "Xcode", "iOS SDK", "UIKit"],
        "preferred_skills": ["SwiftUI", "Core Data", "Combine"],
        "budget_monthly_usd": 9000, "exp_level": 3,
    },
    {
        "id": 21, "domain": "Mobile",
        "title": "Android Developer (Kotlin)",
        "description": (
            "Build native Android applications with Kotlin and Jetpack Compose. "
            "MVVM architecture, Room database, REST API integration via Retrofit."
        ),
        "required_skills": ["Kotlin", "Android SDK", "Jetpack Compose", "Java"],
        "preferred_skills": ["REST API", "Git", "Room"],
        "budget_monthly_usd": 8000, "exp_level": 2,
    },
    {
        "id": 22, "domain": "Mobile",
        "title": "React Native Developer",
        "description": (
            "Cross-platform mobile app with React Native and TypeScript. iOS and "
            "Android deployment, Redux state management, push notifications."
        ),
        "required_skills": ["React Native", "JavaScript", "TypeScript", "iOS"],
        "preferred_skills": ["Android", "Redux", "REST API"],
        "budget_monthly_usd": 7500, "exp_level": 2,
    },
    {
        "id": 23, "domain": "Mobile",
        "title": "Flutter Developer",
        "description": (
            "Build beautiful Flutter apps for iOS and Android. Dart language, "
            "Firebase backend integration, BLoC pattern for state management."
        ),
        "required_skills": ["Flutter", "Dart", "Firebase"],
        "preferred_skills": ["REST API", "Git", "iOS"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },

    # Data / Analytics (Python knowledge helps, but domain diverges)
    {
        "id": 24, "domain": "Data Science",
        "title": "Data Scientist (Python / ML)",
        "description": (
            "Build predictive models and data pipelines using Python. Requires "
            "hands-on ML experience, strong statistical knowledge, and SQL data access. "
            "TensorFlow or PyTorch preferred. PostgreSQL for feature storage."
        ),
        "required_skills": ["Python", "Machine Learning", "Statistics", "SQL"],
        "preferred_skills": ["TensorFlow", "PostgreSQL", "R"],
        "budget_monthly_usd": 9000, "exp_level": 3,
    },
    {
        "id": 25, "domain": "Analytics",
        "title": "Data Analyst",
        "description": (
            "Analyse business data using SQL, Python, and Tableau. Excel proficiency, "
            "automated report creation, and stakeholder communication. "
            "PostgreSQL data warehouse queries."
        ),
        "required_skills": ["SQL", "Python", "Excel", "Tableau"],
        "preferred_skills": ["PostgreSQL", "Power BI", "Git"],
        "budget_monthly_usd": 5500, "exp_level": 2,
    },
    {
        "id": 26, "domain": "Analytics",
        "title": "Business Intelligence Developer",
        "description": (
            "Build BI dashboards with Power BI and Tableau. SQL queries and ETL "
            "pipelines, executive reporting. Python scripting for automation a bonus."
        ),
        "required_skills": ["SQL", "Power BI", "Tableau"],
        "preferred_skills": ["Python", "PostgreSQL", "ETL"],
        "budget_monthly_usd": 7000, "exp_level": 2,
    },

    # Design (completely different domain)
    {
        "id": 27, "domain": "Design",
        "title": "UI/UX Designer",
        "description": (
            "Design user interfaces and experiences using Figma and Adobe XD. "
            "User research, wireframing, interactive prototyping. Collaborate "
            "closely with frontend developers on implementation."
        ),
        "required_skills": ["Figma", "Adobe XD", "Prototyping", "User Research"],
        "preferred_skills": ["Sketch", "Wireframing", "CSS"],
        "budget_monthly_usd": 6000, "exp_level": 2,
    },
    {
        "id": 28, "domain": "Design",
        "title": "Graphic Designer",
        "description": (
            "Create brand assets, marketing materials, and social media graphics "
            "using Adobe Photoshop and Illustrator. Typography and layout skills."
        ),
        "required_skills": ["Photoshop", "Illustrator", "Typography", "Canva"],
        "preferred_skills": ["InDesign", "Branding"],
        "budget_monthly_usd": 3500, "exp_level": 1,
    },
    {
        "id": 29, "domain": "Design",
        "title": "Motion Graphics Designer",
        "description": (
            "Create animated explainer videos and motion graphics for marketing "
            "campaigns. After Effects, Premiere Pro, Cinema 4D. Fast turnaround."
        ),
        "required_skills": ["After Effects", "Premiere Pro", "Cinema 4D", "Animation"],
        "preferred_skills": ["Photoshop", "Illustrator"],
        "budget_monthly_usd": 6000, "exp_level": 2,
    },
    {
        "id": 30, "domain": "Design",
        "title": "Brand Designer",
        "description": (
            "Develop complete visual brand identities: logos, colour systems, "
            "typography guidelines. Figma and Adobe Suite. Strong portfolio required."
        ),
        "required_skills": ["Branding", "Logo Design", "Figma", "Adobe Illustrator"],
        "preferred_skills": ["Typography", "Photoshop"],
        "budget_monthly_usd": 5000, "exp_level": 2,
    },

    # Marketing / Content
    {
        "id": 31, "domain": "Content",
        "title": "Technical Content Writer",
        "description": (
            "Write technical blog posts, API documentation, and SEO-optimised "
            "content for a software company. Strong technical curiosity required. "
            "Developer audience."
        ),
        "required_skills": ["Writing", "SEO", "Content Marketing", "Research"],
        "preferred_skills": ["WordPress", "Google Analytics", "Markdown"],
        "budget_monthly_usd": 3000, "exp_level": 1,
    },
    {
        "id": 32, "domain": "Marketing",
        "title": "SEO Specialist",
        "description": (
            "Improve organic search rankings for a SaaS product. Keyword research, "
            "on-page optimisation, link building, and Google Analytics reporting."
        ),
        "required_skills": ["SEO", "Google Analytics", "Keyword Research", "Content Marketing"],
        "preferred_skills": ["Ahrefs", "Semrush", "HTML"],
        "budget_monthly_usd": 5000, "exp_level": 2,
    },
    {
        "id": 33, "domain": "Marketing",
        "title": "Social Media Manager",
        "description": (
            "Create and schedule content across Instagram, LinkedIn, and Twitter. "
            "Grow follower base and engagement. Monthly analytics reporting."
        ),
        "required_skills": ["Social Media", "Content Creation", "Marketing", "Analytics"],
        "preferred_skills": ["Canva", "Hootsuite", "Copywriting"],
        "budget_monthly_usd": 3500, "exp_level": 1,
    },
    {
        "id": 34, "domain": "Marketing",
        "title": "Email Marketing Specialist",
        "description": (
            "Design and execute email campaigns using HubSpot and Mailchimp. "
            "List segmentation, A/B testing, and open-rate optimisation."
        ),
        "required_skills": ["Email Marketing", "HubSpot", "Mailchimp", "Copywriting"],
        "preferred_skills": ["HTML", "Analytics", "CRM"],
        "budget_monthly_usd": 4500, "exp_level": 2,
    },

    # Finance
    {
        "id": 35, "domain": "Finance",
        "title": "Financial Analyst",
        "description": (
            "Build financial models and forecasts in Excel. SQL for data extraction, "
            "Bloomberg terminal, investment reporting. Presentation to management."
        ),
        "required_skills": ["Excel", "Financial Modeling", "SQL", "Bloomberg"],
        "preferred_skills": ["Python", "Power BI", "Accounting"],
        "budget_monthly_usd": 8000, "exp_level": 2,
    },
    {
        "id": 36, "domain": "Finance",
        "title": "Bookkeeper / Accountant",
        "description": (
            "Manage accounts payable/receivable, payroll processing, and month-end "
            "close using QuickBooks. Excel proficiency required. Remote, part-time."
        ),
        "required_skills": ["QuickBooks", "Bookkeeping", "Excel", "Accounting"],
        "preferred_skills": ["Xero", "Financial Reporting"],
        "budget_monthly_usd": 3000, "exp_level": 1,
    },

    # Legal / Support / Other
    {
        "id": 37, "domain": "Legal",
        "title": "Legal Document Reviewer",
        "description": (
            "Review and summarise contracts, NDAs, and compliance documents. "
            "Law degree or paralegal background required. Attention to detail essential."
        ),
        "required_skills": ["Legal Research", "Contract Review", "Writing", "Compliance"],
        "preferred_skills": ["Legal Software", "Document Management"],
        "budget_monthly_usd": 6000, "exp_level": 2,
    },
    {
        "id": 38, "domain": "Support",
        "title": "Customer Support Specialist",
        "description": (
            "Handle customer queries via live chat, email, and phone. CRM software, "
            "empathy, clear written communication. Zendesk experience a plus."
        ),
        "required_skills": ["Customer Service", "CRM", "Communication", "Problem Solving"],
        "preferred_skills": ["Zendesk", "Salesforce"],
        "budget_monthly_usd": 3000, "exp_level": 1,
    },
    {
        "id": 39, "domain": "Admin",
        "title": "Virtual Assistant",
        "description": (
            "Administrative support: calendar management, email handling, data entry, "
            "travel booking. Excel and Google Workspace proficiency required."
        ),
        "required_skills": ["Administrative Support", "Excel", "Communication", "Scheduling"],
        "preferred_skills": ["Google Workspace", "Data Entry"],
        "budget_monthly_usd": 2500, "exp_level": 1,
    },
    {
        "id": 40, "domain": "Video",
        "title": "Video Editor",
        "description": (
            "Edit YouTube content and marketing videos using Premiere Pro and Final "
            "Cut Pro. Motion graphics with After Effects. Fast turnaround required. "
            "Colour grading and audio clean-up experience valued."
        ),
        "required_skills": ["Premiere Pro", "Final Cut Pro", "Video Editing", "After Effects"],
        "preferred_skills": ["Color Grading", "Sound Design", "Motion Graphics"],
        "budget_monthly_usd": 4000, "exp_level": 2,
    },
]

# Feature computation
FEATURE_COLS = [
    "cosine_sim", "portfolio_relevance",
    "skill_overlap_pct", "skill_required_matched", "skill_required_total",
    "skill_depth",
    "experience_level_match", "exp_delta",
    "rate_in_budget", "rate_ratio",
    "speciality_match",
    "work_exp_count", "total_jobs",
]


def compute_features(job: dict, bio_emb: np.ndarray,
                     job_emb: np.ndarray,
                     portfolio_embs: list[np.ndarray]) -> list[float]:
    fl = FREELANCER

    # Semantic
    cosine_sim = max(0.0, _cosine(bio_emb, job_emb))

    port_rel = 0.0
    for i, item in enumerate(fl["portfolio"]):
        months = (TODAY.year  - item["completion_date"].year) * 12 \
               + (TODAY.month - item["completion_date"].month)
        decay = math.exp(-LAMBDA * months)
        sim   = max(0.0, _cosine(portfolio_embs[i], job_emb))
        port_rel = max(port_rel, sim * decay)

    # Skill fit: best-matching role (single role per job in demo)
    fl_skills = set(fl["skills"].keys())
    req_skills = set(job["required_skills"])

    matched_req            = fl_skills & req_skills
    skill_overlap_pct      = len(matched_req) / len(req_skills) if req_skills else 0.0
    skill_required_matched = float(len(matched_req))
    skill_required_total   = float(len(req_skills))

    # Proficiency-weighted depth on required skills
    depth_sum   = sum(fl["skill_weights"].get(s, 0.50) for s in matched_req)
    skill_depth = depth_sum / len(req_skills) if req_skills else 0.0

    # Experience fit
    fl_exp  = fl["exp_num"]
    job_exp = job["exp_level"]  # 1=entry, 2=intermediate, 3=expert
    experience_level_match = float(fl_exp >= job_exp)
    exp_delta = float(max(-2, min(2, fl_exp - job_exp)))

    # Budget fit: freelancer monthly rate vs job monthly budget
    fl_monthly = fl["monthly_usd"]
    budget     = float(job["budget_monthly_usd"])
    rate_in_budget = float(fl_monthly <= budget * 1.10)
    rate_ratio     = min(fl_monthly / budget if budget > 0 else 3.0, 3.0)

    # Speciality match: any speciality keyword in the job title
    title_words = {w.lower() for w in job["title"].split() if len(w) > 3}
    spec_words  = set()
    for s in fl["specialities"]:
        spec_words.update(w.lower() for w in s.split() if len(w) > 3)
    speciality_match = float(bool(spec_words & title_words))

    # Profile depth
    work_exp_count = float(fl["work_exp_count"])
    total_jobs     = float(fl["total_jobs"])

    return [
        cosine_sim, port_rel,
        skill_overlap_pct, skill_required_matched, skill_required_total,
        skill_depth,
        experience_level_match, exp_delta,
        rate_in_budget, rate_ratio,
        speciality_match,
        work_exp_count, total_jobs,
    ]


# Formatting helpers
_EXP_LABELS = {1: "entry", 2: "intermediate", 3: "expert"}

def _bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)

def _budget_label(budget: int) -> str:
    return f"${budget:,}/mo"


# Main
def main() -> None:
    print("=" * 70)
    print("  Homepage Job Recommendation — Rank Demo")
    print("=" * 70)
    print(f"  Freelancer : Python / FastAPI Backend Developer")
    print(f"  Rate       : ${FREELANCER['hourly_rate_usd']:.0f}/hr  "
          f"(${FREELANCER['monthly_usd']:,.0f}/mo)")
    print(f"  Exp level  : {_EXP_LABELS[FREELANCER['exp_num']]}  "
          f"({FREELANCER['total_jobs']} platform jobs)")
    print(f"  Skills     : {', '.join(list(FREELANCER['skills'])[:5])} …")
    print("=" * 70)
    print()

    # Load model
    print(f"Loading model from {_PKL.name} …")
    if not _PKL.exists():
        sys.exit(f"ERROR: model file not found at {_PKL}")
    with open(_PKL, "rb") as f:
        model = pickle.load(f)
    print(f"  ✓ {type(model).__name__} loaded\n")

    # Embed everything
    total_texts = 1 + len(FREELANCER["portfolio"]) + len(JOBS)
    print(f"Embedding {total_texts} texts with {_EMBED_MODEL_NAME} (local) …")

    bio_text   = FREELANCER["bio"]
    port_texts = [p["text"] for p in FREELANCER["portfolio"]]
    job_texts  = [f"{j['title']}. {j['description']}" for j in JOBS]

    all_texts = [bio_text] + port_texts + job_texts
    t0 = time.perf_counter()
    all_embs = _embed_batch(all_texts)
    elapsed  = time.perf_counter() - t0
    print(f"  ✓ Done in {elapsed:.1f} s  (dim={len(all_embs[0])})\n")

    bio_emb        = all_embs[0]
    portfolio_embs = all_embs[1 : 1 + len(FREELANCER["portfolio"])]
    job_embs       = all_embs[1 + len(FREELANCER["portfolio"]):]

    # Compute features
    print("Computing 13 features for each (freelancer, job) pair …")
    rows = []
    for job, job_emb in zip(JOBS, job_embs):
        feats = compute_features(job, bio_emb, job_emb, portfolio_embs)
        rows.append(feats)

    X = pd.DataFrame(rows, columns=FEATURE_COLS)
    print(f"  ✓ Feature matrix: {X.shape}\n")

    # Score and rank
    print("Scoring with CatBoost …")
    probs = model.predict_proba(X)[:, 1]
    print(f"  ✓ Scored {len(probs)} jobs\n")

    results = []
    for job, prob, row in zip(JOBS, probs, rows):
        results.append({
            "id":        job["id"],
            "title":     job["title"],
            "domain":    job["domain"],
            "budget":    job["budget_monthly_usd"],
            "exp":       _EXP_LABELS[job["exp_level"]],
            "match_pct": round(float(prob) * 100, 1),
            # top contributing features (for inspection)
            "cosine_sim":        round(row[0], 3),
            "skill_overlap_pct": round(row[2], 3),
            "rate_in_budget":    int(row[8]),
            "exp_match":         int(row[6]),
            "speciality_match":  int(row[10]),
        })

    results.sort(key=lambda r: r["match_pct"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # Print ranked table
    print("=" * 90)
    print(f"  RANKED RESULTS — 40 jobs × Python/FastAPI Backend Developer")
    print("=" * 90)
    hdr = (f"{'Rank':>4}  {'Match':>6}  {'Bar':<20}  "
           f"{'Domain':<16}  {'Exp':<12}  {'Budget':>10}  Title")
    print(hdr)
    print("-" * 90)

    for r in results:
        pct = r["match_pct"]
        # colour hint via rank group
        group = " " if pct >= 60 else ("·" if pct >= 35 else "×")
        bar   = _bar(pct)
        print(
            f"{r['rank']:>4}  {pct:>5.1f}%  {bar}  "
            f"{r['domain']:<16}  {r['exp']:<12}  "
            f"{_budget_label(r['budget']):>10}  {r['title']}"
        )

    print()
    print("  Legend: █ = match strength  ░ = gap to 100%")
    print()

    # Domain summary
    print("=" * 55)
    print("  DOMAIN SUMMARY  (peak match % per domain)")
    print("=" * 55)
    domain_data: dict[str, list[float]] = {}
    for r in results:
        domain_data.setdefault(r["domain"], []).append(r["match_pct"])
    domain_rows = [
        (d, max(v), sum(v) / len(v), len(v))
        for d, v in domain_data.items()
    ]
    domain_rows.sort(key=lambda x: -x[1])
    print(f"  {'Domain':<18}  {'Peak':>6}  {'Avg':>6}  {'Jobs':>5}")
    print("  " + "-" * 45)
    for d, peak, avg, n in domain_rows:
        bar = _bar(peak, width=12)
        print(f"  {d:<18}  {peak:>5.1f}%  {avg:>5.1f}%  {n:>5}   {bar}")

    print()

    # Top-5 spotlight
    print("=" * 55)
    print("  TOP 5 — key feature breakdown")
    print("=" * 55)
    for r in results[:5]:
        print(
            f"  #{r['rank']}  {r['title']}")
        print(
            f"      match={r['match_pct']}%  "
            f"cosine={r['cosine_sim']}  "
            f"skill_overlap={r['skill_overlap_pct']:.0%}  "
            f"rate_in_budget={r['rate_in_budget']}  "
            f"exp_match={r['exp_match']}  "
            f"spec_match={r['speciality_match']}"
        )

    print()

    # Bottom-5 spotlight
    print("=" * 55)
    print("  BOTTOM 5 — why the model ranked them low")
    print("=" * 55)
    for r in results[-5:]:
        print(f"  #{r['rank']}  {r['title']}")
        print(
            f"      match={r['match_pct']}%  "
            f"cosine={r['cosine_sim']}  "
            f"skill_overlap={r['skill_overlap_pct']:.0%}  "
            f"rate_in_budget={r['rate_in_budget']}  "
            f"exp_match={r['exp_match']}  "
            f"spec_match={r['speciality_match']}"
        )

    print()
    print("Done.")


if __name__ == "__main__":
    main()
