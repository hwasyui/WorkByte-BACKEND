# Job Matching — AI Pipeline

## What is this?

This module is the AI core of the freelance platform. It implements **three distinct AI techniques** across two user journeys:

| Journey | AI technique | When | Latency |
|---|---|---|---|
| Homepage (freelancer) | Semantic search (pgvector) | Every page load | ~50–100ms |
| Homepage (freelancer) | LightGBM re-ranker | Every page load | +~5ms |
| Job detail (freelancer) | RAG + LLM deep analysis | User clicks "Analyse" | 5–30s |

---

## Architecture

### Homepage — 3-Stage Pipeline

```
GET /ai/job_matching/match/freelancer-to-jobs
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  Stage 1 — Vector Search (pgvector)                 │
│  Cosine similarity on 768-dim embedding vectors     │
│  → top-100 semantically relevant active jobs        │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  Stage 2 — Structured Pre-Filter                    │
│  Drop jobs where skill overlap < 20%                │
│  Purpose: remove obviously irrelevant jobs          │
│  before expensive ML inference                      │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────┐
│  Stage 3 — LightGBM Re-Ranker (ml_ranker.py)        │
│  Predict match_probability for each candidate       │
│  using 18 features → sorted top-N returned          │
└─────────────────────┬───────────────────────────────┘
                      │
                      ▼
         Ranked job cards + match %
         (match_probability, similarity_score, skill_overlap_pct)
```

### Job Detail — RAG + LLM

```
GET /ai/job_matching/analyse/job/{job_post_id}
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  RAG — Retrieval (rag_analyser.py)                  │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐  │
│  │ Job roles + │ │ Freelancer   │ │ Past         │  │
│  │ skills from │ │ profile:     │ │ completed    │  │
│  │ DB          │ │ skills,      │ │ contracts +  │  │
│  │             │ │ portfolio,   │ │ ratings      │  │
│  │             │ │ work exp     │ │ (RAG context)│  │
│  └─────────────┘ └──────────────┘ └──────────────┘  │
└─────────────────────┬───────────────────────────────┘
                      │ grounded prompt
                      ▼
┌─────────────────────────────────────────────────────┐
│  LLM — Ollama (local) via rag_analyser.py           │
│  Returns structured JSON:                           │
│    match_score (0-100)                              │
│    strengths (list)                                 │
│    gaps (list)                                      │
│    recommendation: apply / consider / skip          │
│    recommendation_reason                            │
│    skill_tips (list)                                │
└─────────────────────────────────────────────────────┘
```

---

## Files in this module

| File | Purpose |
|---|---|
| `job_matching_routes.py` | FastAPI router — all endpoints, orchestrates the 3-stage pipeline |
| `ml_ranker.py` | Stage 3: loads LightGBM model, computes 18 features, re-ranks jobs |
| `rag_analyser.py` | Job detail: retrieves context from DB, builds prompt, calls Ollama LLM |
| `embedding_manager.py` | Builds source text, generates 768-dim vectors, upserts to `freelancer_embedding` / `job_embedding` |
| `sweep_worker.py` | Background loop: re-embeds dirty records every 5 minutes |
| `source_text_builder.py` | Builds natural-language source text from DB for embedding |
| `embedding_service.py` | Calls Ollama (nomic-embed-text) or Google Vertex AI for embedding vectors |
| `machine_learning/` | Training notebooks, trained model, feature definitions |

---

## API Endpoints

All under prefix `/ai/job_matching`.

### `GET /match/freelancer-to-jobs`
**Auth:** Freelancer bearer token required.

Returns ranked job recommendations for the authenticated freelancer.
Uses the full 3-stage pipeline. Each result includes:
- `match_probability` — LightGBM score (0–100)
- `similarity_score` — raw cosine similarity from pgvector (0–1)
- `skill_overlap_pct` — % of required skills matched (0–100)

**Query params:**
- `limit` (1–50, default 10) — how many results to return
- `experience_level` (optional) — filter by job experience level

---

### `GET /match/job-to-freelancers/{job_post_id}`
**Auth:** Client bearer token required. Client must own the job post.

Returns top freelancers for a given job, ranked by cosine similarity.

**Query params:**
- `limit` (1–50, default 10)
- `min_rate`, `max_rate` (optional) — filter by hourly rate
- `min_performance` (optional) — minimum performance score

---

### `GET /analyse/job/{job_post_id}`
**Auth:** Freelancer bearer token required.

Triggers the RAG + LLM deep analysis for a specific job. This is the
"Analyse Match" button on the job detail page.

**Response:**
```json
{
  "match_score": 78,
  "strengths": ["Strong Python backend skills", "Portfolio matches project scope", "Rate within budget"],
  "gaps": ["No experience with Kubernetes"],
  "recommendation": "apply",
  "recommendation_reason": "Strong skill alignment and proven track record in similar backend projects.",
  "skill_tips": ["Learn Kubernetes basics", "Get Docker certification"],
  "rag_sources": {
    "past_contracts_used": 3,
    "portfolio_items": 2,
    "work_experience": 3,
    "freelancer_skills": 12,
    "job_roles": 1
  }
}
```

**Note:** LLM calls take 5–30 seconds depending on model size. This is acceptable
because it's user-triggered, not on page load.

---

### `POST /embed/freelancer/{freelancer_id}`
**Auth:** Any authenticated user.

Manually triggers re-embedding for a freelancer profile (background task).

---

### `POST /embed/job/{job_post_id}`
**Auth:** Any authenticated user.

Manually triggers re-embedding for a job post (background task).

---

### `POST /sweep`
**Auth:** Any authenticated user.

Manually runs one full sweep cycle (re-embeds all dirty records immediately).

---

### `GET /test_ai_local`
**Auth:** None required.

Tests the Ollama connection. Returns the LLM's response to a simple greeting.

---

## LightGBM Model — 18 Features

The model predicts **match_probability** using these features:

| Feature | Description | Source |
|---|---|---|
| `cosine_sim` | pgvector cosine similarity | Stage 1 output |
| `skill_overlap_pct` | % of required skills matched | DB join |
| `skill_required_matched` | count of required skills matched | DB join |
| `skill_required_total` | total required skills in job | DB join |
| `skill_preferred_pct` | % of preferred skills matched | DB join |
| `experience_level_match` | 1 if freelancer exp ≥ job requirement | inferred from total_projects |
| `exp_delta` | over/under qualification (-2 to +2) | inferred |
| `rate_in_budget` | 1 if rate fits job budget (±10% tolerance) | DB |
| `rate_ratio` | freelancer_rate / avg_role_budget (capped at 3) | DB |
| `language_match` | 1 if shares English or Indonesian | DB |
| `speciality_match` | 1 if speciality appears in job title | DB |
| `domain_match` | 1 if speciality appears in source_text | DB |
| `has_portfolio` | 1 if has any portfolio items | DB |
| `work_exp_count` | number of work experience entries | DB |
| `performance_score` | overall_performance_score (NaN = cold start) | performance_rating |
| `success_rate_hist` | historical success rate (NaN = cold start) | performance_rating |
| `total_projects` | total completed projects | freelancer |
| `is_cold_start` | 1 if no performance history | computed |

### Cold Start Handling
New freelancers with no completed contracts get `NaN` for `performance_score`
and `success_rate_hist`. LightGBM handles NaN natively — it routes NaN samples
to the optimal split direction learned during training. The model still uses
all 16 non-history features for cold-start freelancers.

### Model Performance (from training on synthetic data)
- **AUC-ROC:** 0.9679
- **Baseline cosine AUC:** 0.9570 (+0.0109 improvement)
- **5-fold CV AUC:** 0.9696 ± 0.006
- **Inference time:** 4.6ms for 100 jobs (after warm-up)

---

## RAG — Why it's genuine RAG

The "Analyse Match" endpoint is genuine RAG (Retrieval-Augmented Generation), not prompt engineering:

| Aspect | This implementation |
|---|---|
| **Retrieval** | Job requirements, freelancer profile, and past contracts are fetched from the DB at request time |
| **Dynamic** | The LLM sees different context every time — it reflects the current DB state |
| **Grounded** | Past contracts are the "retrieved documents" — they ground the LLM's reasoning in real history |
| **Not hard-coded** | The LLM prompt contains no static context — everything is retrieved |

If this were prompt engineering, the context would be fixed/known in advance and the LLM would just format it. In RAG, the retrieval step is what changes the output.

---

## Embedding System

### How embeddings are built

**Freelancer source text** (from `source_text_builder.py`):
```
Specialities: Backend Development, API Design
Skills: Python [advanced], Go [intermediate], PostgreSQL [expert], ...
Languages: English [fluent], Indonesian [native]
Rate: 50 USD/hour
Bio: ...
Work Experience:
  - Senior Backend Engineer at Tokopedia (2022–2024): ...
Portfolio:
  - E-commerce API: Built REST API handling 10k req/s ...
```

**Job source text** (from `source_text_builder.py`):
```
Job: Senior Backend Engineer
Description: ...
Type: individual / large
Experience: expert
Duration: 3 months

Role: Backend Developer
Budget: 3000 USD (fixed)
Skills Required: Python, PostgreSQL, Redis
Skills Preferred: Go, Kubernetes
```

Both are encoded by `embedding_service.py` using:
- **Primary:** Ollama `nomic-embed-text` (local, 768 dims)
- **Fallback:** Google Vertex AI `text-embedding-005` (768 dims)

### Dirty flag + sweep worker

When a freelancer updates their profile or a client updates a job post, the corresponding
embedding record is marked `embedding_dirty = TRUE`. The sweep worker (`sweep_worker.py`)
runs every **5 minutes** and re-embeds all dirty records in batches of 50.

```
Profile update → mark_freelancer_dirty() → embedding_dirty = TRUE
                                                    │
                              (within 5 min)        ▼
                              sweep_worker → upsert_freelancer_embedding()
                                           → new vector stored, dirty = FALSE
```

---

## How to Run / Set Up

### 1. Prerequisites
Ensure the following are running:
- PostgreSQL container (`capstone-postgresql`) with `pgvector` extension
- Ollama running locally with `nomic-embed-text` and your LLM model

### 2. Environment variables (`.env`)
```env
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_TEXT_EMBEDDING=nomic-embed-text
OLLAMA_LLM=gemma4:e2b
```

### 3. Train the LightGBM model
If the model file doesn't exist at `machine_learning/models/lgbm_job_matcher.pkl`:

```bash
# Inside the backend Docker container:
docker exec -it capstone-backend bash
apt-get update -qq && apt-get install -y -q libgomp1
pip install lightgbm scikit-learn joblib jupyter nbconvert matplotlib seaborn
cd /app/ai_related/job_matching/machine_learning

# Generate training data
jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 \
  01_generate_data.ipynb --output 01_generate_data.ipynb

# Train model
jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 \
  02_train_model.ipynb --output 02_train_model.ipynb

# Verify
cat models/model_summary.json
```

Alternatively, run the notebooks in Google Colab and copy
`machine_learning/models/lgbm_job_matcher.pkl` and `machine_learning/models/feature_cols.json`
into the container.

### 4. Run the DB migration
```bash
docker exec -it capstone-postgresql psql -U capstone -d capstone
\i /query/alter_table.sql
```

This creates `freelancer_embedding`, `job_embedding`, `contract_embedding` tables with HNSW indexes.

### 5. Seed initial embeddings
After the app starts, trigger the sweep to embed all existing profiles and jobs:
```
POST /ai/job_matching/sweep
Authorization: Bearer <any_valid_token>
```

Or embed individually:
```
POST /ai/job_matching/embed/freelancer/{freelancer_id}
POST /ai/job_matching/embed/job/{job_post_id}
```

---

## Why LightGBM over other models?

| Model | Speed | Cold start | Why not |
|---|---|---|---|
| Collaborative filtering | Fast | Fails (needs interaction history) | No interaction data |
| Neural network | Slow (GPU needed) | Requires imputation | Too heavy for page load |
| Random Forest | Medium | Native NaN support | Slower than LightGBM |
| **LightGBM** | **4.6ms for 100 jobs** | **Native NaN routing** | ← chosen |
| XGBoost | ~2× slower than LightGBM | Native NaN support | Slower |

LightGBM's C++ tree traversal makes it ideal for the page-load use case where
the model runs on every request. The NaN routing for cold-start freelancers
(no history) is a native feature — no imputation required.

---

## Log output guide

All logs follow the format:
```
TIMESTAMP | LEVEL | SERVICE | ROUTE | MESSAGE
```

Key services to watch in logs:
- `ML_RANKER` — Stage 3 inference, feature computation, model load
- `RAG_ANALYSER` — context retrieval, LLM calls, JSON parsing
- `JOB_MATCHING` — request start/end, stage timing, filter stats
- `EMBEDDING_MANAGER` — embedding upsert lifecycle
- `SWEEP_WORKER` — background sweep cycles
- `EMBEDDING_SERVICE` — Ollama/Google embedding API calls

Example log for a homepage request:
```
INFO  | JOB_MATCHING | | freelancer-to-jobs request started | freelancer_id=abc | limit=10
INFO  | JOB_MATCHING | | Stage 1 complete | candidates=100 | cosine_range=[0.612, 0.941] | time=87ms
INFO  | JOB_MATCHING | | Stage 2 complete | passed=62 | dropped_low_overlap=38 | time=210ms
DEBUG | ML_RANKER    | | Freelancer context loaded | skills=12 | cold_start=False | performance=78.5
INFO  | ML_RANKER    | | LightGBM inference | candidates=62 | time=3.8ms | prob_range=[0.04, 0.89]
INFO  | JOB_MATCHING | | freelancer-to-jobs complete | returned=10 | stage1=87ms stage2=210ms stage3=4ms | total=301ms
```
