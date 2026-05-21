"""
walkthrough-jobs.py — Bulk-seed ~100 job posts across multiple domains.

Domains covered: Software Development, Frontend/Mobile, Data & AI, DevOps/Cloud,
Design & UX, Marketing & Content, Writing & Translation, Finance & Business,
E-commerce, Cybersecurity.

Each job gets at least one role with required skills. Job embeddings are
queued automatically on creation; a sweep is run at the end.

Usage (from repo root):
    python walkthrough/walkthrough-jobs.py

Override the backend URL:
    BASE_URL=http://localhost:8000 python walkthrough/walkthrough-jobs.py
"""

import sys
import json
import os
import datetime
import requests

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

_EMAIL_CLIENT    = "clientinputjobs@client.com"
_PASSWORD_CLIENT = "SecurePass123"


# ── Output tee ────────────────────────────────────────────────────────────────

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


def _start_tee() -> tuple:
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir  = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(out_dir, f"walkthrough_jobs_{ts}.md")
    tee = _Tee(filepath)
    sys.stdout = tee
    return tee, filepath


def _stop_tee(tee: _Tee, filepath: str) -> None:
    sys.stdout = tee._stdout
    tee.close()
    print(f"\n  Results saved to: {filepath}")


# ── Request helpers ───────────────────────────────────────────────────────────

_step = 0


def step(title: str) -> None:
    global _step
    _step += 1
    print(f"\n{'=' * 70}")
    print(f"  Step {_step}: {title}")
    print(f"{'=' * 70}")


def _call(method: str, endpoint: str, body: dict = None, token: str = None,
          params: dict = None, silent: bool = False) -> dict | None:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{BASE_URL}{endpoint}"
    r = requests.request(method, url, json=body, headers=headers,
                         params=params, timeout=60)
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {"raw_response": r.text[:500]}
    if not silent:
        status = "OK" if r.ok else "FAIL"
        print(f"    {method:4s} {endpoint}  [{r.status_code}] {status}")
        if not r.ok:
            print(f"         {json.dumps(data)[:200]}")
    return data if r.ok else None


def _extract(resp: dict | None, *keys: str):
    """Walk details → data → direct for the given key chain."""
    if not resp:
        return None
    for envelope in ("details", "data"):
        node = resp.get(envelope)
        if isinstance(node, dict):
            val = node
            for k in keys:
                val = val.get(k) if isinstance(val, dict) else None
            if val is not None:
                return val
    # direct
    val = resp
    for k in keys:
        val = val.get(k) if isinstance(val, dict) else None
    return val


# ── Skill cache: name → skill_id ──────────────────────────────────────────────

_skill_cache: dict[str, str | None] = {}


def _lookup_skill(query: str, token: str) -> str | None:
    key = query.lower()
    if key in _skill_cache:
        return _skill_cache[key]
    resp = _call("GET", "/skills/search", token=token, params={"q": query}, silent=True)
    results = _extract(resp, "results") or []
    for s in results:
        if s.get("skill_name", "").lower() == key:
            _skill_cache[key] = s["skill_id"]
            return s["skill_id"]
    # fallback: first result
    if results:
        _skill_cache[key] = results[0]["skill_id"]
        return results[0]["skill_id"]
    _skill_cache[key] = None
    return None


# ── Job catalog ───────────────────────────────────────────────────────────────
# Each entry:
#   job_title, job_description, project_type (individual/team),
#   project_scope (small/medium/large), estimated_duration, experience_level,
#   roles: list of { role_title, budget_type, role_budget, skills: [search_term] }

JOBS = [
    # ── BACKEND DEVELOPMENT ───────────────────────────────────────────────────
    {
        "job_title": "Build FastAPI REST API with PostgreSQL",
        "job_description": (
            "We need an experienced Python engineer to design and implement a RESTful "
            "API using FastAPI. The scope includes user authentication (JWT), CRUD "
            "endpoints for our core entities, PostgreSQL schema design, Alembic "
            "migrations, and full OpenAPI documentation. "
            "Deliverable: production-ready API container image."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Backend Engineer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Python Developer", "FastAPI Developer", "PostgreSQL Developer"]}],
    },
    {
        "job_title": "Django CMS for News Portal",
        "job_description": (
            "Build a content management system for our news portal using Django and "
            "Django REST Framework. Features: article management with rich-text editor, "
            "category & tag system, author profiles, image upload, and a public-facing "
            "API consumed by a React frontend. Must include role-based permissions."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Django Developer", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["Django Developer", "Django REST Framework Developer", "PostgreSQL Developer"]}],
    },
    {
        "job_title": "Node.js Microservices Architecture",
        "job_description": (
            "Architect and implement a microservices backend using Node.js (Express/Fastify). "
            "Services: user-service, order-service, payment-service. "
            "Inter-service communication via RabbitMQ. API Gateway with JWT auth. "
            "Dockerized with Docker Compose for local dev, Kubernetes manifests for prod."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Node.js Backend Lead", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Node.js Developer", "RabbitMQ Developer", "Docker Developer"]},
            {"role_title": "DevOps Engineer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["Kubernetes Developer", "Docker Developer"]},
        ],
    },
    {
        "job_title": "Go High-Performance API for Fintech",
        "job_description": (
            "Develop a high-throughput financial transaction API in Go (Golang). "
            "Requirements: sub-10ms P99 latency, PostgreSQL with pgx driver, "
            "Redis caching layer, idempotency keys, rate limiting, and Prometheus metrics. "
            "Security: mTLS between services, audit logging."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Go Backend Engineer", "budget_type": "fixed",
                   "role_budget": 8000, "skills": ["Go Developer", "PostgreSQL Expert", "Redis Developer"]}],
    },
    {
        "job_title": "Spring Boot Monolith to Microservices Migration",
        "job_description": (
            "Migrate a legacy Spring Boot monolith to a microservices architecture. "
            "Identify bounded contexts, extract services one by one using the strangler-fig "
            "pattern, set up service discovery with Eureka, API gateway with Spring Cloud "
            "Gateway, and configure distributed tracing with Zipkin."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "16 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Java Architect", "budget_type": "negotiable",
             "role_budget": 10000, "skills": ["Spring Boot Developer", "Spring Framework Expert", "Docker Developer"]},
            {"role_title": "QA Engineer", "budget_type": "fixed",
             "role_budget": 3000, "skills": ["QA Engineer", "Selenium Developer"]},
        ],
    },
    {
        "job_title": "GraphQL API for Social Platform",
        "job_description": (
            "Build a GraphQL API (Apollo Server, Node.js) for a social networking app. "
            "Features: user graph, post/comment/like system, real-time notifications via "
            "WebSocket subscriptions, N+1 query prevention with DataLoader, "
            "and Redis pub/sub for live feeds."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "GraphQL Developer", "budget_type": "fixed",
                   "role_budget": 6000, "skills": ["GraphQL Developer", "Node.js Developer", "Redis Developer"]}],
    },
    {
        "job_title": "PHP Laravel E-learning Platform Backend",
        "job_description": (
            "Develop the backend for an e-learning platform using Laravel 11. "
            "Modules: course catalog, video lesson management (S3 storage), quiz engine, "
            "certificate generation (PDF), subscription billing via Midtrans, "
            "and learner progress tracking."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Laravel Developer", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["Laravel Developer", "MySQL Developer", "Redis Developer"]}],
    },
    {
        "job_title": "Python Data Ingestion Pipeline",
        "job_description": (
            "Build a batch + streaming data ingestion pipeline. Sources: REST APIs, "
            "PostgreSQL CDC (Debezium), and CSV uploads. Sink: data warehouse (BigQuery). "
            "Orchestrated via Apache Airflow, transformed with dbt, monitored with "
            "Great Expectations data quality checks."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Data Engineer", "budget_type": "fixed",
                   "role_budget": 7500, "skills": ["Data Engineer", "Airflow Developer", "Dbt Developer"]}],
    },
    {
        "job_title": "Rust WebAssembly Module for Browser",
        "job_description": (
            "Write a high-performance image processing module in Rust, compiled to "
            "WebAssembly for use in the browser. Operations: resize, crop, colour-correct, "
            "watermark. Expose a clean JS/TS API. Must pass a benchmark of 1000 ops/sec "
            "on a mid-range laptop."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Rust/WASM Engineer", "budget_type": "fixed",
                   "role_budget": 5500, "skills": ["Rust Developer", "JavaScript Developer"]}],
    },
    {
        "job_title": "Real-Time Chat Backend with WebSockets",
        "job_description": (
            "Build a scalable real-time chat service. Technology: Node.js + Socket.IO, "
            "Redis adapter for horizontal scaling, MongoDB for message history, "
            "rooms/DMs/group channels, read receipts, typing indicators, "
            "and file attachment support via S3."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "7 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Backend Engineer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Node.js Developer", "WebSocket Developer", "MongoDB Developer"]}],
    },

    # ── FRONTEND DEVELOPMENT ──────────────────────────────────────────────────
    {
        "job_title": "React Admin Dashboard with Charts",
        "job_description": (
            "Build an analytics dashboard in React with a rich set of charts (Recharts / "
            "Chart.js). Features: date-range filter, drilldown tables, CSV export, "
            "user management panel, dark mode, responsive layout (Tailwind CSS), "
            "and TypeScript throughout."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "React Developer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["React Developer", "TypeScript Developer", "Tailwind CSS Developer"]}],
    },
    {
        "job_title": "Next.js Marketing Website with CMS",
        "job_description": (
            "Create a high-performance marketing website with Next.js 14 App Router. "
            "Pages: home, product, pricing, blog (MDX). Headless CMS: Sanity. "
            "Target: Lighthouse score ≥ 95 on all metrics. Includes SEO meta, "
            "OG images, and sitemap generation."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Next.js Developer", "budget_type": "fixed",
                   "role_budget": 2800, "skills": ["Next.js Developer", "React Developer", "Tailwind CSS Developer"]}],
    },
    {
        "job_title": "Vue 3 SPA for Project Management Tool",
        "job_description": (
            "Develop a Kanban-style project management SPA in Vue 3 (Composition API). "
            "Features: drag-and-drop cards (Vue Draggable), Gantt chart view, team member "
            "assignments, deadline alerts, and full offline support via service workers."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Vue Developer", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["Vue 3 Developer", "TypeScript Developer"]}],
    },
    {
        "job_title": "Angular Enterprise Resource Planning (ERP) Module",
        "job_description": (
            "Build an HR module for an existing Angular ERP system. Features: employee "
            "directory, leave management, payroll summary, org-chart visualisation, "
            "and role-based access. Must follow the project's existing NgRx state "
            "management patterns and Material Design component library."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Angular Developer", "budget_type": "fixed",
                   "role_budget": 6000, "skills": ["Angular Developer", "TypeScript Developer"]}],
    },
    {
        "job_title": "D3.js Data Visualisation Library",
        "job_description": (
            "Create a reusable D3.js visualisation library for our BI team. Charts needed: "
            "time-series, stacked bar, sankey, choropleth map, and force-directed graph. "
            "Exported as an npm package with TypeScript definitions, Storybook demos, "
            "and full test coverage."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Data Visualisation Developer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["D3.js Developer", "TypeScript Developer", "JavaScript Developer"]}],
    },

    # ── MOBILE DEVELOPMENT ────────────────────────────────────────────────────
    {
        "job_title": "iOS Swift Fitness Tracking App",
        "job_description": (
            "Build an iOS fitness app in Swift using SwiftUI. Features: workout logging, "
            "HealthKit integration (steps, heart rate, calories), custom workout plans, "
            "streak tracking, Apple Watch companion app, and iCloud sync. "
            "Target: iOS 16+, App Store submission included."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [{"role_title": "iOS Developer", "budget_type": "fixed",
                   "role_budget": 7000, "skills": ["iOS Swift Developer", "Swift Developer"]}],
    },
    {
        "job_title": "Android Kotlin Food Delivery App",
        "job_description": (
            "Develop an Android food delivery app in Kotlin with Jetpack Compose UI. "
            "Features: restaurant listing, menu, cart, real-time order tracking (Google "
            "Maps), in-app payment (Midtrans SDK), push notifications (FCM), "
            "and user review system."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Android Developer", "budget_type": "fixed",
                   "role_budget": 6500, "skills": ["Android Kotlin Developer", "Kotlin Developer"]}],
    },
    {
        "job_title": "Flutter Cross-Platform Expense Tracker",
        "job_description": (
            "Build a cross-platform expense tracking app in Flutter (iOS + Android). "
            "Features: transaction entry, recurring expenses, budget goals, charts "
            "(pie, bar, trend), bank CSV import, biometric lock, and cloud backup "
            "via Firebase Firestore."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "8 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Flutter Developer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Flutter Developer", "Firebase Developer"]}],
    },
    {
        "job_title": "React Native B2B Field Sales App",
        "job_description": (
            "Develop a React Native app for field sales agents. Offline-first architecture "
            "with WatermelonDB sync. Features: customer list, visit logging with GPS, "
            "order entry, signature capture, product catalog with images, and daily "
            "sales performance dashboard."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "React Native Developer", "budget_type": "fixed",
             "role_budget": 6000, "skills": ["React Native Developer", "React Developer"]},
            {"role_title": "Backend Engineer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["Node.js Developer", "PostgreSQL Developer"]},
        ],
    },

    # ── DATA SCIENCE & AI / ML ────────────────────────────────────────────────
    {
        "job_title": "Customer Churn Prediction Model",
        "job_description": (
            "Build a churn prediction model for our SaaS product. Dataset: 200 K user "
            "sessions. Pipeline: EDA, feature engineering (usage patterns, billing events), "
            "model selection (XGBoost, LightGBM, CatBoost), SHAP explanations, "
            "MLflow tracking, and a FastAPI inference endpoint."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "ML Engineer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Machine Learning Engineer", "Python Developer", "Data Scientist"]}],
    },
    {
        "job_title": "NLP Sentiment Analysis for Product Reviews",
        "job_description": (
            "Build a sentiment analysis pipeline for e-commerce product reviews (Indonesian "
            "language). Steps: data cleaning, fine-tune IndoBERT on labelled dataset "
            "(20 K reviews), REST API deployment, and a simple analytics dashboard "
            "showing sentiment trends over time."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "7 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "NLP Engineer", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["NLP Engineer", "Python Developer", "PyTorch Developer"]}],
    },
    {
        "job_title": "Computer Vision Defect Detection System",
        "job_description": (
            "Develop an automated defect detection system for a manufacturing line. "
            "Dataset: 50 K images (labelled). Approach: YOLOv8 fine-tuning, data "
            "augmentation, edge inference on NVIDIA Jetson Nano, dashboard for "
            "defect statistics, and integration with the factory MES via REST API."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Computer Vision Engineer", "budget_type": "fixed",
                   "role_budget": 8000, "skills": ["Computer Vision Engineer", "TensorFlow Developer", "Python Developer"]}],
    },
    {
        "job_title": "Recommendation Engine for Online Marketplace",
        "job_description": (
            "Build a personalised product recommendation engine. Techniques: "
            "collaborative filtering (ALS), content-based (TF-IDF + cosine), "
            "and a hybrid ensemble. Real-time inference via a FastAPI service "
            "with Redis feature store. A/B testing framework included."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "ML Engineer", "budget_type": "fixed",
                   "role_budget": 7000, "skills": ["Machine Learning Engineer", "Python Developer", "Redis Developer"]}],
    },
    {
        "job_title": "Business Intelligence Dashboard (Power BI)",
        "job_description": (
            "Design and build an executive BI dashboard in Power BI. Data sources: "
            "SQL Server, Google Analytics, Salesforce CRM (Power Query connectors). "
            "KPIs: revenue, CAC, LTV, churn, funnel. Automated refresh schedule, "
            "row-level security, and PDF report export."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "BI Developer", "budget_type": "fixed",
                   "role_budget": 2800, "skills": ["Power BI Developer", "Data Analyst", "SQL Server Developer"]}],
    },
    {
        "job_title": "LLM-Powered Document Q&A Chatbot",
        "job_description": (
            "Build a RAG-based document Q&A chatbot for internal knowledge base. "
            "Stack: LangChain, OpenAI GPT-4o, pgvector for embeddings, FastAPI backend, "
            "React chat UI. Supports PDF / Word ingestion, hybrid retrieval, "
            "source citation, and conversation history."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "AI/ML Engineer", "budget_type": "fixed",
             "role_budget": 6000, "skills": ["AI Engineer", "Python Developer", "FastAPI Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 2500, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Time-Series Demand Forecasting",
        "job_description": (
            "Build a demand forecasting system for a retail chain with 500 SKUs "
            "across 30 stores. Models: Prophet, LSTM, and LightGBM with lag features. "
            "Automated weekly retraining with Airflow. Forecast API consumed by the "
            "inventory management system."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Data Scientist", "budget_type": "fixed",
                   "role_budget": 6500, "skills": ["Data Scientist", "Python Developer", "Airflow Developer"]}],
    },
    {
        "job_title": "Fraud Detection ML Pipeline",
        "job_description": (
            "Design a real-time fraud detection system for a payment processor. "
            "Features: streaming data ingestion (Kafka), online feature computation, "
            "Gradient Boosting ensemble, explainability with SHAP, and a case-management "
            "dashboard for fraud analysts."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "ML Engineer", "budget_type": "fixed",
             "role_budget": 8000, "skills": ["Machine Learning Engineer", "Kafka Developer", "Python Developer"]},
            {"role_title": "Data Engineer", "budget_type": "fixed",
             "role_budget": 5000, "skills": ["Data Engineer", "Kafka Developer", "Spark Developer"]},
        ],
    },

    # ── DEVOPS & CLOUD ────────────────────────────────────────────────────────
    {
        "job_title": "Kubernetes Migration for SaaS Platform",
        "job_description": (
            "Migrate a Docker Compose-based SaaS application to Kubernetes on AWS EKS. "
            "Scope: write Helm charts, configure Horizontal Pod Autoscaler, "
            "set up Ingress with cert-manager TLS, implement GitOps with ArgoCD, "
            "and ensure zero-downtime rolling deployments."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "DevOps Engineer", "budget_type": "fixed",
                   "role_budget": 7000, "skills": ["Kubernetes Developer", "Helm Developer", "Terraform Developer"]}],
    },
    {
        "job_title": "CI/CD Pipeline Setup with GitHub Actions",
        "job_description": (
            "Set up a full CI/CD pipeline using GitHub Actions for a monorepo. "
            "Stages: lint, unit test, integration test, Docker build & push to ECR, "
            "Terraform plan/apply, smoke test. Branch protection rules, "
            "environment promotion (dev → staging → prod), and Slack notifications."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "DevOps Engineer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["GitHub Actions Developer", "Docker Developer", "Terraform Developer"]}],
    },
    {
        "job_title": "AWS Infrastructure as Code (Terraform)",
        "job_description": (
            "Write Terraform modules for our AWS infrastructure: VPC with public/private "
            "subnets, ECS Fargate services, RDS Aurora cluster, ElastiCache Redis, "
            "S3 + CloudFront CDN, Route 53, ACM certificates, IAM roles, "
            "and CloudWatch alarms. Remote state in S3 + DynamoDB lock."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Cloud/DevOps Engineer", "budget_type": "fixed",
                   "role_budget": 6500, "skills": ["Terraform Expert", "AWS Developer", "Docker Developer"]}],
    },
    {
        "job_title": "Monitoring & Observability Stack Setup",
        "job_description": (
            "Implement a full observability stack: Prometheus + Grafana for metrics, "
            "ELK Stack for centralised logging, Jaeger for distributed tracing, "
            "and PagerDuty integration for alerting. Define SLOs and error budgets "
            "for 5 critical services."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "SRE / DevOps Engineer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Monitoring Engineer", "Kubernetes Developer", "DevOps Engineer"]}],
    },
    {
        "job_title": "Multi-Region Disaster Recovery Setup",
        "job_description": (
            "Design and implement a multi-region DR strategy on GCP. "
            "Components: active-passive setup with Cloud SQL read replicas, "
            "Cloud Spanner for globally consistent data, GCS cross-region replication, "
            "Cloud Load Balancing with health checks, and runbook for RTO < 15 min."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Cloud Architect", "budget_type": "fixed",
                   "role_budget": 9000, "skills": ["Google Cloud Developer", "GCP Solutions Architect", "Terraform Developer"]}],
    },
    {
        "job_title": "Serverless Event-Driven System on AWS Lambda",
        "job_description": (
            "Refactor a batch-processing backend into an event-driven architecture "
            "using AWS Lambda, SQS, SNS, and EventBridge. DLQ handling, "
            "X-Ray tracing, Lambda Layers for shared dependencies, "
            "and CDK for infrastructure definitions. Target: 99.9% SLA."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Serverless Engineer", "budget_type": "fixed",
                   "role_budget": 5500, "skills": ["AWS Lambda Developer", "AWS Developer", "Python Developer"]}],
    },

    # ── DESIGN & UX ───────────────────────────────────────────────────────────
    {
        "job_title": "Mobile App UI/UX Design (Fintech)",
        "job_description": (
            "Design end-to-end UI/UX for a mobile banking app (iOS + Android). "
            "Deliverables: user research report, information architecture, "
            "low-fi wireframes, high-fi mockups in Figma, interactive prototype, "
            "and a design system (colours, typography, components). "
            "Accessibility: WCAG 2.1 AA."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "UX/UI Designer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["UX Designer", "UI Designer"]}],
    },
    {
        "job_title": "Brand Identity Design Package",
        "job_description": (
            "Create a comprehensive brand identity for a tech startup. Deliverables: "
            "logo (primary + variations), colour palette, typography guide, icon set, "
            "business card, letterhead, social media templates, and a brand guidelines "
            "PDF. Formats: vector (AI/SVG) + raster."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Brand/Graphic Designer", "budget_type": "fixed",
                   "role_budget": 2000, "skills": ["UI Designer", "Creativity"]}],
    },
    {
        "job_title": "E-commerce Website UX Redesign",
        "job_description": (
            "Redesign the UX of an existing e-commerce website to improve conversion rate. "
            "Process: heuristic evaluation, user interviews (10 participants), A/B test "
            "hypothesis definition, wireframes, Figma prototypes for product page, "
            "checkout flow, and homepage. Handoff to dev team with Zeplin specs."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "UX Researcher & Designer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["UX Designer", "Researcher"]}],
    },
    {
        "job_title": "SaaS Dashboard Design System",
        "job_description": (
            "Build a comprehensive design system in Figma for our SaaS dashboard. "
            "Components: 80+ atoms/molecules (buttons, forms, tables, modals, charts), "
            "light + dark mode tokens, responsive grid, and documentation site "
            "using Storybook + MDX."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Design Systems Designer", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["UI Designer", "UX Designer"]}],
    },
    {
        "job_title": "3D Product Visualisation for Online Store",
        "job_description": (
            "Create interactive 3D product models for an online furniture store. "
            "Deliver 10 product models in GLTF format, integrated with a Three.js viewer "
            "on the product page. Features: 360° rotation, zoom, colour-variant switching, "
            "and AR preview (model-viewer web component)."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "3D/WebGL Developer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Three.js Developer", "Blender", "WebGL Developer"]}],
    },

    # ── MARKETING & CONTENT ───────────────────────────────────────────────────
    {
        "job_title": "SEO Content Strategy & Blog Writing",
        "job_description": (
            "Develop a 6-month SEO content strategy for a B2B SaaS company. "
            "Deliverables: keyword research report (500+ keywords), content calendar, "
            "12 long-form blog posts (2000+ words each), on-page optimisation checklist, "
            "and monthly performance reporting."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 months", "experience_level": "intermediate",
        "roles": [{"role_title": "SEO Content Writer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["Technical Writer", "Strategist"]}],
    },
    {
        "job_title": "Social Media Management (Instagram + TikTok)",
        "job_description": (
            "Manage our Instagram and TikTok accounts for 3 months. "
            "Deliverables: monthly content calendar, 20 posts/month per platform, "
            "5 Reels/TikToks per month, community management (respond within 2 h), "
            "and bi-weekly analytics report."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 months", "experience_level": "entry",
        "roles": [{"role_title": "Social Media Manager", "budget_type": "fixed",
                   "role_budget": 1500, "skills": ["Creativity", "Communication Specialist"]}],
    },
    {
        "job_title": "Email Marketing Automation Campaign",
        "job_description": (
            "Design and implement a full email marketing funnel in Klaviyo. "
            "Flows: welcome series (5 emails), abandoned cart (3 emails), "
            "post-purchase (2 emails), win-back (3 emails). "
            "Includes copywriting, HTML templates, segmentation, and A/B tests."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Email Marketing Specialist", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["Communication Specialist", "Strategist"]}],
    },
    {
        "job_title": "Google & Meta Ads Campaign Management",
        "job_description": (
            "Run paid advertising campaigns across Google Search/Display and Meta (FB/IG) "
            "for a D2C fashion brand. Monthly budget: IDR 50 M. "
            "Scope: audience research, ad copy, creative briefs, campaign setup, "
            "weekly optimisation, and monthly ROI reporting."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 months", "experience_level": "intermediate",
        "roles": [{"role_title": "Performance Marketing Specialist", "budget_type": "fixed",
                   "role_budget": 2000, "skills": ["Strategist", "Consultant"]}],
    },
    {
        "job_title": "Product Demo Video Production",
        "job_description": (
            "Produce a 2-minute product demo video for a B2B SaaS tool. "
            "Deliverables: script, screen recording, voiceover, motion graphics "
            "for key feature callouts, background music, and final render in "
            "1080p MP4 + 4K export."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "3 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Video Editor", "budget_type": "fixed",
                   "role_budget": 1500, "skills": ["DaVinci Resolve", "Creativity"]}],
    },

    # ── WRITING & TRANSLATION ─────────────────────────────────────────────────
    {
        "job_title": "API Documentation (OpenAPI + Developer Guide)",
        "job_description": (
            "Write comprehensive API documentation for our REST API. "
            "Deliverables: OpenAPI 3.1 spec review and improvements, "
            "developer quickstart guide, 10 tutorial pages (code examples in "
            "Python, JS, curl), error code reference, and changelog."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Technical Writer", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["Technical Writer", "REST API Developer"]}],
    },
    {
        "job_title": "Indonesian–English Software Localisation",
        "job_description": (
            "Translate and localise a mobile app from English to Indonesian (Bahasa). "
            "Scope: ~5000 strings from Android/iOS string files, in-app tooltips, "
            "help centre articles (30 pages), and app store listing. "
            "Maintain consistent tone matching our brand voice guide."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Translator (EN-ID)", "budget_type": "fixed",
                   "role_budget": 1800, "skills": ["Technical Writer", "Attention to Detail"]}],
    },
    {
        "job_title": "User Manual for Industrial IoT Device",
        "job_description": (
            "Write a user manual and quick-start guide for an industrial IoT gateway device. "
            "Audience: field technicians with no software background. "
            "Deliverables: installation guide, configuration guide, troubleshooting section, "
            "and a laminated quick-reference card. Format: PDF + web version."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "3 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Technical Writer", "budget_type": "fixed",
                   "role_budget": 1200, "skills": ["Technical Writer", "Attention to Detail"]}],
    },
    {
        "job_title": "Whitepaper: AI in Supply Chain (8000 words)",
        "job_description": (
            "Research and write an 8000-word whitepaper on AI applications in supply chain "
            "management. Target audience: C-suite decision makers. "
            "Structure: executive summary, 5 use-case deep-dives, ROI framework, "
            "implementation roadmap, and bibliography. Final deliverable: designed PDF."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Technical Writer / Researcher", "budget_type": "fixed",
                   "role_budget": 2200, "skills": ["Technical Writer", "Researcher"]}],
    },
    {
        "job_title": "Legal Contract Proofreading & Editing",
        "job_description": (
            "Proofread and edit a set of 15 standard legal contracts for a legal tech "
            "company. Scope: grammar, clarity, consistency of terminology, and flagging "
            "potentially ambiguous clauses. Deliverable: tracked-changes Word documents "
            "with comments within 5 business days."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "2 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Editor / Proofreader", "budget_type": "fixed",
                   "role_budget": 800, "skills": ["Technical Writer", "Attention to Detail"]}],
    },

    # ── FINANCE & BUSINESS ────────────────────────────────────────────────────
    {
        "job_title": "Financial Model for Series A Fundraising",
        "job_description": (
            "Build a detailed 5-year financial model in Excel for a Series A pitch. "
            "Includes: P&L, balance sheet, cash flow statement, unit economics "
            "(CAC, LTV, payback period), scenario analysis (base / bull / bear), "
            "and investor-ready presentation deck."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Financial Analyst", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["Business Analyst", "Excel"]}],
    },
    {
        "job_title": "Startup Business Plan (Full Document)",
        "job_description": (
            "Write a comprehensive business plan for a healthtech startup. "
            "Sections: executive summary, problem/solution, market sizing (TAM/SAM/SOM), "
            "business model canvas, competitive analysis, go-to-market, team, "
            "financials, and risk analysis. ~40 pages."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Business Consultant", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["Business Analyst", "Strategist"]}],
    },
    {
        "job_title": "Monthly Bookkeeping & Accounting (SME)",
        "job_description": (
            "Handle monthly bookkeeping for a small business (50–100 transactions/month). "
            "Scope: data entry in QuickBooks Online, bank reconciliation, accounts "
            "payable/receivable management, monthly P&L + balance sheet, and "
            "quarterly tax preparation guidance."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "ongoing", "experience_level": "intermediate",
        "roles": [{"role_title": "Bookkeeper / Accountant", "budget_type": "fixed",
                   "role_budget": 500, "skills": ["Business Analyst", "Attention to Detail"]}],
    },
    {
        "job_title": "Market Research Report: Edtech in Southeast Asia",
        "job_description": (
            "Produce a market research report on the edtech sector in Southeast Asia. "
            "Deliverables: market size and growth projections, key players analysis, "
            "regulatory landscape per country, customer segment profiling, "
            "and strategic recommendations. ~30 pages with charts."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Market Research Analyst", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["Researcher", "Business Analyst", "Strategist"]}],
    },
    {
        "job_title": "Investor Pitch Deck Design (15 slides)",
        "job_description": (
            "Design a compelling 15-slide investor pitch deck for a pre-seed startup. "
            "Input: written content and data from founding team. "
            "Output: professionally designed Figma/PowerPoint deck with custom "
            "data visualisations, on-brand illustrations, and print-ready PDF."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "2 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Pitch Deck Designer", "budget_type": "fixed",
                   "role_budget": 1500, "skills": ["UI Designer", "Creativity"]}],
    },

    # ── E-COMMERCE ────────────────────────────────────────────────────────────
    {
        "job_title": "Shopify Custom Theme Development",
        "job_description": (
            "Develop a custom Shopify theme from scratch using Liquid, HTML5, CSS3, "
            "and vanilla JS. Features: mega menu, sticky header, quick-view modal, "
            "infinite scroll collection page, sticky cart drawer, "
            "loyalty points badge, and Google PageSpeed score ≥ 90."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Shopify Developer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["JavaScript Developer", "CSS3 Developer", "HTML5 Developer"]}],
    },
    {
        "job_title": "WooCommerce Plugin: Subscription Billing",
        "job_description": (
            "Build a custom WooCommerce plugin for subscription billing. Features: "
            "flexible billing intervals (weekly/monthly/annual), trial periods, "
            "dunning management, subscription dashboard for customers, "
            "PayPal + Stripe gateways, and WooCommerce admin order integration."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "7 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "WooCommerce / PHP Developer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["WordPress Developer", "PHP Developer"]}],
    },
    {
        "job_title": "Marketplace Platform (Multi-Vendor)",
        "job_description": (
            "Build a multi-vendor marketplace platform. Backend: Laravel 11 + REST API. "
            "Frontend: Next.js. Features: vendor onboarding, product listings with "
            "variants, order routing, split payments (Stripe Connect / Midtrans), "
            "review system, dispute management, and admin panel."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "16 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Laravel Developer", "MySQL Developer", "Redis Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["Next.js Developer", "React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Product Catalogue Migration to Shopify",
        "job_description": (
            "Migrate 5000 products with variants, images, and metafields from Magento 2 "
            "to Shopify Plus. Includes: data mapping, CSV transformation script, "
            "301 redirect map, SEO metadata preservation, "
            "and post-migration QA checklist."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "E-commerce Data Specialist", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["Python Developer", "Data Engineer"]}],
    },
    {
        "job_title": "Headless Commerce with Medusa.js",
        "job_description": (
            "Set up a headless commerce stack using Medusa.js (Node.js) as the backend "
            "and Next.js as the storefront. Features: product/collection management, "
            "cart, checkout, Stripe payment, fulfilment webhook, "
            "and Docker + Railway deployment."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Fullstack Developer", "budget_type": "fixed",
                   "role_budget": 5500, "skills": ["Node.js Developer", "Next.js Developer", "PostgreSQL Developer"]}],
    },

    # ── CYBERSECURITY ─────────────────────────────────────────────────────────
    {
        "job_title": "Web Application Penetration Test",
        "job_description": (
            "Conduct a black-box penetration test on a web application (OWASP Top 10 scope). "
            "Deliverables: reconnaissance report, vulnerability findings (CVSS scored), "
            "exploitation proof-of-concept, remediation recommendations, "
            "and executive summary. Re-test included after fixes."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Penetration Tester", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Security Engineer", "Security Tester"]}],
    },
    {
        "job_title": "Cloud Security Audit (AWS)",
        "job_description": (
            "Perform a security audit of our AWS environment. Scope: IAM policy review "
            "(least privilege), S3 bucket policies, Security Group rules, CloudTrail "
            "logging, GuardDuty findings, and Secrets Manager usage. "
            "Deliverable: findings report with priority-ranked remediation roadmap."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Cloud Security Engineer", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["Security Architect", "AWS Developer"]}],
    },
    {
        "job_title": "SAST/DAST Integration into CI Pipeline",
        "job_description": (
            "Integrate Static and Dynamic Application Security Testing into our "
            "GitHub Actions pipeline. Tools: SonarQube (SAST), OWASP ZAP (DAST), "
            "Snyk for dependency scanning. Configure severity thresholds, "
            "PR annotations, and a security dashboard."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Security DevOps Engineer", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["Security Engineer", "GitHub Actions Developer", "DevOps Engineer"]}],
    },

    # ── FULLSTACK / MIXED ─────────────────────────────────────────────────────
    {
        "job_title": "SaaS Appointment Booking Platform",
        "job_description": (
            "Build a multi-tenant appointment booking SaaS. Stack: FastAPI backend, "
            "React frontend, PostgreSQL. Features: business profile setup, "
            "staff calendars, online booking widget (embeddable), email/SMS reminders, "
            "Stripe subscription billing, and an analytics dashboard."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 6000, "skills": ["FastAPI Developer", "Python Developer", "PostgreSQL Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["React Developer", "TypeScript Developer", "Tailwind CSS Developer"]},
        ],
    },
    {
        "job_title": "Online Learning Management System (LMS)",
        "job_description": (
            "Develop a lightweight LMS for a corporate training department. "
            "Features: course creation (video + quiz), learner enrolment, "
            "progress tracking, certificate of completion, SCORM 1.2 import, "
            "manager dashboard, and SSO (SAML 2.0)."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "16 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Python Developer", "Django Developer", "PostgreSQL Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 5000, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Healthcare Patient Portal (HIPAA-Aware)",
        "job_description": (
            "Build a patient portal for a telemedicine startup. Features: appointment "
            "scheduling, secure video consultation (WebRTC), medical record upload, "
            "prescription history, billing summary. Architecture must consider "
            "HIPAA-equivalent data handling practices and audit logging."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "18 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Engineer", "budget_type": "fixed",
             "role_budget": 9000, "skills": ["Python Developer", "FastAPI Developer", "PostgreSQL Expert"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 5000, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Property Listing Platform (Real Estate)",
        "job_description": (
            "Build a real estate listing platform similar to Rumah123. "
            "Backend: Django REST Framework + PostGIS for geo queries. "
            "Frontend: Next.js with Mapbox integration. Features: property search with "
            "filters, image galleries, mortgage calculator, agent profiles, "
            "and lead management system."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 6500, "skills": ["Django Developer", "PostgreSQL Developer", "Python Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["Next.js Developer", "React Developer"]},
        ],
    },
    {
        "job_title": "HR Management System with Payroll",
        "job_description": (
            "Develop an HRMS with payroll module for a 200-person company. "
            "Modules: employee records, attendance (biometric integration), "
            "leave management, performance review, payroll calculation "
            "(Indonesian PPh 21 rules), payslip generation (PDF), and bank transfer file export."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "20 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 8000, "skills": ["Python Developer", "FastAPI Developer", "PostgreSQL Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 5000, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "IoT Dashboard for Smart Building Management",
        "job_description": (
            "Build a real-time IoT dashboard for a smart building system. "
            "Sensor data: temperature, humidity, energy, occupancy (MQTT broker). "
            "Backend: Python + Kafka stream processing. Frontend: React + WebSocket "
            "live charts. Alerts, historical reports, and floor-plan heatmap overlay."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "IoT Backend Engineer", "budget_type": "fixed",
             "role_budget": 6000, "skills": ["IoT Developer", "Kafka Developer", "Python Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 3500, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Automated Testing Framework for Web App",
        "job_description": (
            "Set up a comprehensive test automation framework for a React web app and "
            "FastAPI backend. Scope: Playwright E2E tests (30+ scenarios), "
            "pytest unit + integration tests, code coverage gates (≥ 80%), "
            "performance budget tests, and integration into the CI pipeline."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "QA Automation Engineer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Test Automation Engineer", "Playwright Developer", "Pytest Developer"]}],
    },
    {
        "job_title": "PDF Report Generation Microservice",
        "job_description": (
            "Build a PDF generation microservice using Python (WeasyPrint / ReportLab). "
            "Features: HTML-to-PDF conversion with custom CSS, header/footer, "
            "page numbering, watermark, digital signature placeholder, "
            "and an async job queue (Redis + Celery) for large batch reports."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "3 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Python Developer", "budget_type": "fixed",
                   "role_budget": 1800, "skills": ["Python Developer", "Redis Developer", "PDF Generation Developer"]}],
    },
    {
        "job_title": "Logistics Route Optimisation API",
        "job_description": (
            "Build a route optimisation API for a last-mile delivery company. "
            "Algorithm: Vehicle Routing Problem (VRP) solver using OR-Tools. "
            "Input: depot, list of delivery stops, vehicle capacity. "
            "Output: optimised routes with ETA. FastAPI service, "
            "containerised and deployable on GCP Cloud Run."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Algorithm / Backend Engineer", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["Python Developer", "FastAPI Developer", "Google Cloud Developer"]}],
    },
    {
        "job_title": "Custom CRM for Sales Team",
        "job_description": (
            "Build a lightweight CRM tailored for a 15-person B2B sales team. "
            "Features: contact & company records, pipeline view (Kanban), activity log "
            "(calls/emails/meetings), email integration (Gmail API), deal forecasting, "
            "and a mobile-responsive React UI."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "intermediate",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 5000, "skills": ["Python Developer", "FastAPI Developer", "PostgreSQL Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 3500, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Blockchain-based Supply Chain Traceability",
        "job_description": (
            "Build a supply chain traceability system on Ethereum (or Polygon). "
            "Smart contracts: product registration, transfer of custody, QR-code-based "
            "verification. Backend: Node.js + Web3.js. Frontend: React with MetaMask "
            "integration. Includes unit tests for all contracts (Hardhat)."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "14 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Blockchain Developer", "budget_type": "fixed",
             "role_budget": 8000, "skills": ["Solidity Developer", "Smart Contract Developer", "Web3 Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 3500, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Game: 2D Platformer in Unity",
        "job_description": (
            "Develop a 2D platformer game in Unity (C#). Scope: 5 levels, "
            "2 enemy types with AI (state machine), collectables, checkpoint system, "
            "parallax background, sound effects + background music, "
            "and WebGL + Android builds."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "10 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Unity Game Developer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Unity Developer", "C# Developer", "Game Developer"]}],
    },
    {
        "job_title": "Internal Developer Portal (Backstage)",
        "job_description": (
            "Set up and customise Spotify Backstage as an internal developer portal. "
            "Plugins: software catalogue, TechDocs, CI/CD status (GitHub Actions), "
            "Kubernetes cluster view, and a custom onboarding workflow plugin. "
            "Deploy on Kubernetes with SSO (Okta)."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Platform Engineer", "budget_type": "fixed",
                   "role_budget": 7500, "skills": ["Platform Engineer", "Kubernetes Developer", "TypeScript Developer"]}],
    },

    # ── ADDITIONAL JOBS (to reach ~100) ───────────────────────────────────────
    {
        "job_title": "Django REST API for Multi-Tenant SaaS",
        "job_description": (
            "Build a multi-tenant Django REST Framework API with row-level tenancy "
            "using django-tenants. Features: tenant onboarding, isolated schemas, "
            "per-tenant custom domains, shared public schema, "
            "and Celery task isolation per tenant."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Django Backend Developer", "budget_type": "fixed",
                   "role_budget": 6000, "skills": ["Django Developer", "Django REST Framework Developer", "PostgreSQL Developer"]}],
    },
    {
        "job_title": "Kotlin Multiplatform Mobile SDK",
        "job_description": (
            "Build a shared business logic SDK in Kotlin Multiplatform for iOS and Android. "
            "Modules: network layer (Ktor), local storage (SQLDelight), analytics events. "
            "Expose clean API to Swift (via KMM bridge) and Android Compose UI layer."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Kotlin Multiplatform Developer", "budget_type": "fixed",
                   "role_budget": 7500, "skills": ["Kotlin Developer", "Android Kotlin Developer", "iOS Swift Developer"]}],
    },
    {
        "job_title": "PostgreSQL Database Performance Tuning",
        "job_description": (
            "Audit and optimise a PostgreSQL 15 database with 50+ tables and 200 M rows. "
            "Scope: slow query analysis (pg_stat_statements), index strategy, "
            "table partitioning for time-series data, VACUUM/ANALYZE tuning, "
            "connection pooling (PgBouncer), and query plan documentation."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "PostgreSQL DBA", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["PostgreSQL Expert", "Database Tuning Expert", "Query Optimization Specialist"]}],
    },
    {
        "job_title": "Web Scraping & Data Extraction Pipeline",
        "job_description": (
            "Build a web scraping pipeline to collect product pricing data from "
            "10 e-commerce websites daily. Stack: Playwright (JS-rendered sites) + "
            "Scrapy, rotating proxies, captcha handling, de-duplication, "
            "PostgreSQL storage, and a Grafana price-trend dashboard."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Python Data Engineer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["Python Developer", "Playwright Developer", "Data Engineer"]}],
    },
    {
        "job_title": "Chrome Extension for Productivity Tracking",
        "job_description": (
            "Build a Chrome Extension that tracks active tab time, groups by domain, "
            "and shows a daily/weekly productivity summary. Options page for "
            "category configuration, cloud sync via a lightweight REST API, "
            "and a React popup UI."
        ),
        "project_type": "individual", "project_scope": "small",
        "estimated_duration": "3 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Chrome Extension Developer", "budget_type": "fixed",
                   "role_budget": 1500, "skills": ["JavaScript Developer", "React Developer", "TypeScript Developer"]}],
    },
    {
        "job_title": "Search Engine Optimisation Technical Audit",
        "job_description": (
            "Perform a full technical SEO audit for an e-commerce website (5000+ pages). "
            "Tools: Screaming Frog, Google Search Console, SEMrush. "
            "Deliverables: crawl report, Core Web Vitals optimisation plan, "
            "structured data implementation, and a 90-day remediation roadmap."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 weeks", "experience_level": "expert",
        "roles": [{"role_title": "SEO Specialist", "budget_type": "fixed",
                   "role_budget": 2000, "skills": ["Strategist", "Consultant", "Attention to Detail"]}],
    },
    {
        "job_title": "Svelte + SvelteKit E-commerce Storefront",
        "job_description": (
            "Build a fast, lightweight e-commerce storefront in SvelteKit. "
            "Backend: headless Shopify Storefront API. Features: SSR product pages, "
            "cart with localStorage persistence, checkout redirect to Shopify, "
            "Lighthouse score ≥ 98, and Tailwind CSS styling."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "SvelteKit Developer", "budget_type": "fixed",
                   "role_budget": 3000, "skills": ["SvelteKit Developer", "Svelte Developer", "Tailwind CSS Developer"]}],
    },
    {
        "job_title": "Embedded Firmware for Smart Thermostat",
        "job_description": (
            "Develop firmware for an ESP32-based smart thermostat. Features: "
            "temperature/humidity sensor reading (DHT22), PID control loop, "
            "MQTT over WiFi for cloud connectivity, OTA firmware updates, "
            "touch display UI (LVGL), and power-saving deep-sleep modes."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Embedded Systems Engineer", "budget_type": "fixed",
                   "role_budget": 4500, "skills": ["Embedded Systems Engineer", "IoT Developer", "Firmware Developer"]}],
    },
    {
        "job_title": "WordPress Plugin: Advanced Custom Fields Integration",
        "job_description": (
            "Develop a WordPress plugin that extends Advanced Custom Fields (ACF) "
            "with a custom Gutenberg block library (10 blocks), REST API endpoints "
            "for headless use, admin import/export tool for field groups, "
            "and automated field documentation generator."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "5 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "WordPress Plugin Developer", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["WordPress Developer", "PHP Developer", "JavaScript Developer"]}],
    },
    {
        "job_title": "Ruby on Rails API Backend for Mobile App",
        "job_description": (
            "Build a Rails 7 API-mode backend for a mobile application. "
            "Features: Devise + JWT auth, ActiveRecord with PostgreSQL, "
            "background jobs (Sidekiq), image uploads (ActiveStorage + S3), "
            "push notifications (Firebase Cloud Messaging), "
            "and Rspec test suite (90%+ coverage)."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "7 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Rails Backend Developer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Ruby on Rails Developer", "Ruby Developer", "PostgreSQL Developer"]}],
    },
    {
        "job_title": "Event Management Platform",
        "job_description": (
            "Build an online event management platform for conferences and workshops. "
            "Features: event creation with custom registration forms, ticket tiers, "
            "QR-code check-in app (Flutter), speaker management, schedule builder, "
            "attendee networking (match by interest), and live Q&A."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "16 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Developer", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Python Developer", "FastAPI Developer", "PostgreSQL Developer"]},
            {"role_title": "Mobile Developer", "budget_type": "fixed",
             "role_budget": 3500, "skills": ["Flutter Developer", "Firebase Developer"]},
        ],
    },
    {
        "job_title": "Automated Invoice Processing with OCR",
        "job_description": (
            "Build an automated invoice processing system. Pipeline: PDF/image upload, "
            "OCR extraction (Tesseract + AWS Textract), field classification "
            "(vendor, amount, date, line items), validation rules, "
            "ERP integration (REST), and a review dashboard for exceptions."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "7 weeks", "experience_level": "expert",
        "roles": [{"role_title": "AI/Backend Engineer", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["Python Developer", "Machine Learning Engineer", "FastAPI Developer"]}],
    },
    {
        "job_title": "Figma to React Component Library",
        "job_description": (
            "Convert an existing Figma design system (80 components) into a "
            "production-ready React component library. TypeScript, styled-components "
            "or Tailwind, Storybook documentation, Chromatic visual testing, "
            "and npm package publication with semantic versioning."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Frontend Engineer", "budget_type": "fixed",
                   "role_budget": 5500, "skills": ["React Developer", "TypeScript Developer", "Tailwind CSS Developer"]}],
    },
    {
        "job_title": "Podcast Production & Editing (10 Episodes)",
        "job_description": (
            "Edit and produce 10 podcast episodes for a tech startup's "
            "thought-leadership podcast. Each episode is 30–45 minutes raw. "
            "Deliverables: noise removal, EQ, compression, intro/outro music, "
            "chapter markers, transcript (AI-assisted), and distribution to "
            "Spotify, Apple Podcasts, and YouTube."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Audio/Video Editor", "budget_type": "fixed",
                   "role_budget": 2000, "skills": ["DaVinci Resolve", "Creativity"]}],
    },
    {
        "job_title": "NFT Minting Platform (ERC-721)",
        "job_description": (
            "Build a custom NFT minting and marketplace platform. Smart contracts: "
            "ERC-721 with royalties (EIP-2981), lazy minting, and auction mechanism. "
            "Frontend: Next.js + RainbowKit wallet connection. "
            "IPFS storage via Pinata. Deployed on Polygon mainnet."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "12 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Smart Contract Developer", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Solidity Developer", "Ethereum Developer", "Web3 Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 4000, "skills": ["Next.js Developer", "React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Scala Spark ETL for Data Warehouse",
        "job_description": (
            "Build a Scala Spark ETL pipeline to ingest raw e-commerce events "
            "into a Snowflake data warehouse. Transformations: sessionisation, "
            "funnel attribution, and product affinity calculation. "
            "Airflow orchestration, Delta Lake for ACID compliance, dbt for modelling layer."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Data Engineer (Spark/Scala)", "budget_type": "fixed",
                   "role_budget": 8000, "skills": ["Scala Developer", "Spark Developer", "Airflow Developer"]}],
    },
    {
        "job_title": "Voice Assistant Integration (Alexa / Google Assistant)",
        "job_description": (
            "Build Alexa and Google Assistant skills for a smart home product. "
            "Backend: AWS Lambda (Node.js). Features: device control commands, "
            "status queries, scheduled routines, and multi-turn dialogue. "
            "Certification for both platforms included."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Voice & Cloud Developer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Node.js Developer", "AWS Lambda Developer", "AWS Developer"]}],
    },
    {
        "job_title": "Agile Project Management Consultancy",
        "job_description": (
            "Provide Agile coaching for a 20-person software team transitioning from "
            "waterfall. Scope: 3-month engagement with weekly workshops, "
            "Jira board setup, sprint cadence definition, retrospective facilitation, "
            "OKR alignment, and a final maturity assessment report."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "3 months", "experience_level": "expert",
        "roles": [{"role_title": "Agile Coach / Scrum Master", "budget_type": "fixed",
                   "role_budget": 5000, "skills": ["Scrum Master", "Agile Coach", "Project Manager"]}],
    },
    {
        "job_title": "Tableau Dashboard for Operations KPIs",
        "job_description": (
            "Build 5 interactive Tableau dashboards for an operations team. "
            "Data sources: PostgreSQL and Google Sheets. KPIs: on-time delivery rate, "
            "warehouse utilisation, order fill rate, returns rate, and NPS trend. "
            "Includes calculated fields, parameters, and scheduled data extract refresh."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "BI / Tableau Developer", "budget_type": "fixed",
                   "role_budget": 2500, "skills": ["Tableau Developer", "Data Analyst", "PostgreSQL Developer"]}],
    },
    {
        "job_title": "API Gateway & Rate Limiting Implementation",
        "job_description": (
            "Implement an API Gateway layer using Kong for our microservices cluster. "
            "Plugins: JWT auth, rate limiting (per-consumer and per-IP), "
            "request/response transformation, Prometheus metrics, "
            "and canary routing for A/B deployments."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "API Gateway Engineer", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["API Gateway Specialist", "Kubernetes Developer", "DevOps Engineer"]}],
    },
    {
        "job_title": "Headless CMS Integration (Contentful + Next.js)",
        "job_description": (
            "Integrate Contentful as a headless CMS into an existing Next.js 14 site. "
            "Content types: blog posts, landing pages, case studies, team members. "
            "Features: ISR/on-demand revalidation, preview mode, rich-text renderer, "
            "image optimisation (next/image), and a content editor onboarding guide."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "intermediate",
        "roles": [{"role_title": "Next.js / CMS Developer", "budget_type": "fixed",
                   "role_budget": 2800, "skills": ["Next.js Developer", "React Developer", "TypeScript Developer"]}],
    },
    {
        "job_title": "Kubernetes Operator for Custom Resource Management",
        "job_description": (
            "Develop a Kubernetes Operator in Go using the controller-runtime SDK. "
            "Custom Resource: DatabaseCluster (provisions RDS instances from "
            "a Kubernetes manifest). Reconciliation loop, status conditions, "
            "event recording, and Helm chart for operator deployment."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "10 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Kubernetes / Go Engineer", "budget_type": "fixed",
                   "role_budget": 8000, "skills": ["Kubernetes Architect", "Go Developer", "Terraform Developer"]}],
    },
    {
        "job_title": "Cybersecurity Awareness Training Programme",
        "job_description": (
            "Design and deliver a cybersecurity awareness programme for 200 employees. "
            "Deliverables: 4 e-learning modules (phishing, social engineering, "
            "password hygiene, data classification), phishing simulation campaign, "
            "pre/post knowledge assessment, and a risk reduction report."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "6 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Cybersecurity Trainer", "budget_type": "fixed",
                   "role_budget": 3500, "skills": ["Security Engineer", "Trainer", "Technical Writer"]}],
    },
    {
        "job_title": "Apache Kafka Streaming Platform Setup",
        "job_description": (
            "Set up a production-grade Apache Kafka cluster on Kubernetes (Strimzi). "
            "Topics: event ingestion, CDC (Debezium connectors for PostgreSQL/MySQL), "
            "Kafka Streams processing jobs, schema registry (Confluent), "
            "monitoring with Prometheus + Grafana, and runbook documentation."
        ),
        "project_type": "individual", "project_scope": "large",
        "estimated_duration": "8 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Kafka / Data Infrastructure Engineer", "budget_type": "fixed",
                   "role_budget": 7000, "skills": ["Kafka Architect", "Kubernetes Developer", "Data Engineer"]}],
    },
    {
        "job_title": "Online Tutoring Platform (WebRTC + Whiteboard)",
        "job_description": (
            "Build an online 1-on-1 tutoring platform. Features: tutor/student matching, "
            "session booking calendar, video call (WebRTC via mediasoup), "
            "collaborative whiteboard (Konva.js), session recording, "
            "payment escrow per session (Stripe), and tutor payout management."
        ),
        "project_type": "team", "project_scope": "large",
        "estimated_duration": "16 weeks", "experience_level": "expert",
        "roles": [
            {"role_title": "Backend Engineer", "budget_type": "fixed",
             "role_budget": 7000, "skills": ["Node.js Developer", "WebSocket Developer", "PostgreSQL Developer"]},
            {"role_title": "Frontend Developer", "budget_type": "fixed",
             "role_budget": 4500, "skills": ["React Developer", "TypeScript Developer"]},
        ],
    },
    {
        "job_title": "Data Privacy & GDPR Compliance Audit",
        "job_description": (
            "Conduct a GDPR / PDPA compliance audit for a B2C web application. "
            "Scope: data mapping (data inventory), consent management review, "
            "privacy policy gap analysis, DSAR workflow, data retention policy, "
            "and remediation roadmap with prioritised action items."
        ),
        "project_type": "individual", "project_scope": "medium",
        "estimated_duration": "4 weeks", "experience_level": "expert",
        "roles": [{"role_title": "Privacy & Compliance Consultant", "budget_type": "fixed",
                   "role_budget": 4000, "skills": ["Security Architect", "Business Analyst", "Consultant"]}],
    },
]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tee, out_path = _start_tee()

    print("\n" + "=" * 70)
    print("  Capstone API — Bulk Job Seeder")
    print("=" * 70)
    print(f"  Target     : {BASE_URL}")
    print(f"  Client     : {_EMAIL_CLIENT}")
    print(f"  Job count  : {len(JOBS)}")
    print(f"  Output     : {out_path}")

    # ── 1. Login using the existing seed client ───────────────────────────────
    step("Login as client")
    login_resp = _call("POST", "/auth/login",
                       {"email": _EMAIL_CLIENT, "password": _PASSWORD_CLIENT})
    if not login_resp:
        print(f"  ERROR: login failed for existing seed client {_EMAIL_CLIENT}")
        print("  Create or verify this account first, then rerun the walkthrough.")
        _stop_tee(tee, out_path)
        return

    tok = _extract(login_resp, "access_token")
    if not tok:
        print("  ERROR: no access_token in login response")
        _stop_tee(tee, out_path)
        return
    print(f"  token: {tok[:30]}...")

    # ── 2. Get client_id ──────────────────────────────────────────────────────
    step("Resolve client_id")
    me = _call("GET", "/auth/me", token=tok)
    client_id = _extract(me, "client_id")
    if not client_id:
        print("  ERROR: could not resolve client_id from /auth/me")
        _stop_tee(tee, out_path)
        return
    print(f"  client_id : {client_id}")

    # ── 3. Create jobs ────────────────────────────────────────────────────────
    step(f"Create {len(JOBS)} job posts with roles and skills")

    created   = 0
    skipped   = 0
    job_ids   = []

    for idx, job in enumerate(JOBS, 1):
        print(f"\n  [{idx:03d}/{len(JOBS)}] {job['job_title']}")

        # Create job post
        post_body = {
            "job_title":          job["job_title"],
            "job_description":    job["job_description"],
            "project_type":       job["project_type"],
            "project_scope":      job["project_scope"],
            "estimated_duration": job["estimated_duration"],
            "experience_level":   job["experience_level"],
            "status":             "active",
        }
        post_resp = _call("POST", "/job-posts", post_body, token=tok, silent=True)
        if not post_resp:
            print(f"        job post FAILED — skipping")
            skipped += 1
            continue

        job_post_id = _extract(post_resp, "job_post_id")
        if not job_post_id:
            print(f"        no job_post_id in response — skipping")
            skipped += 1
            continue

        job_ids.append(job_post_id)
        print(f"        job_post_id: {job_post_id}")

        # Create each role
        for role in job.get("roles", []):
            role_body = {
                "job_post_id":        job_post_id,
                "role_title":         role["role_title"],
                "budget_type":        role["budget_type"],
                "role_budget":        role["role_budget"],
                "positions_available": 1,
                "is_required":        True,
            }
            role_resp = _call("POST", "/job-roles", role_body, token=tok, silent=True)
            job_role_id = _extract(role_resp, "job_role_id") if role_resp else None
            if not job_role_id:
                print(f"          role '{role['role_title']}' — FAILED")
                continue
            print(f"          role '{role['role_title']}' → {job_role_id}")

            # Add required skills to role
            for skill_query in role.get("skills", []):
                skill_id = _lookup_skill(skill_query, tok)
                if not skill_id:
                    print(f"            skill '{skill_query}' — not found, skipping")
                    continue
                skill_resp = _call("POST", "/job-role-skills", {
                    "job_role_id":      job_role_id,
                    "skill_id":         skill_id,
                    "is_required":      True,
                    "importance_level": "required",
                }, token=tok, silent=True)
                mark = "ok" if skill_resp else "fail"
                print(f"            skill '{skill_query}' [{mark}]")

        created += 1

    # ── 4. Run embedding sweep ────────────────────────────────────────────────
    step("Trigger embedding sweep for all new jobs")
    sweep = _call("POST", "/ai/job_matching/sweep", {}, token=tok)
    if sweep:
        print("  Sweep triggered successfully")
    else:
        print("  Sweep request failed or endpoint unavailable")

    # ── 5. Summary ────────────────────────────────────────────────────────────
    step("Summary")
    print(f"\n  Total jobs in catalog : {len(JOBS)}")
    print(f"  Jobs created          : {created}")
    print(f"  Jobs skipped (errors) : {skipped}")
    print(f"  Skill cache entries   : {len(_skill_cache)}")
    print(f"\n  Sample job_post_ids (first 5):")
    for jid in job_ids[:5]:
        print(f"    {jid}")

    _stop_tee(tee, out_path)


if __name__ == "__main__":
    main()
