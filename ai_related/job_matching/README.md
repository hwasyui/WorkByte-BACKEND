# Job Matching — AI Pipeline

## What is this?

This module is the AI core of the freelance platform. It implements **three distinct AI techniques** across two user journeys:

| Journey | AI technique | When | Latency |
|---|---|---|---|
| Homepage (freelancer) | Semantic search (pgvector) | Every page load | ~50–100ms |
| Homepage (freelancer) | LightGBM re-ranker | Every page load | +~5ms |
| Job detail (freelancer) | RAG + LLM deep analysis | User clicks "Analyse" | 5–30s |

---

## Architecture Overview

There are **two completely separate AI systems** that work together:

```
┌──────────────────────────────────────────────────────────────────┐
│  SYSTEM 1 — Semantic + ML Homepage Feed                          │
│  (job_matching_routes.py + ml_ranker.py)                         │
│                                                                  │
│  freelancer_embedding ──┐                                        │
│                         ├─ pgvector cosine → Stage 1             │
│  job_embedding ─────────┘                                        │
│        │                                                         │
│        └──→ skill filter → Stage 2                               │
│                  │                                               │
│        freelancer + job_role + performance_rating                │
│                  └──→ LightGBM (18 features) → Stage 3           │
│                                     │                            │
│                              Ranked job cards                    │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│  SYSTEM 2 — RAG Deep Analysis on Job Detail                      │
│  (rag_analyser.py)                                               │
│                                                                  │
│  job_post + job_role + job_role_skill                            │
│  freelancer + freelancer_skill + portfolio + work_experience     │
│  contract_embedding (ordered by cosine to target job)           │
│        │                                                         │
│        └──→ Grounded prompt → Ollama/Gemini LLM → JSON result   │
└──────────────────────────────────────────────────────────────────┘
```

---

## Stage 1 — Vector Search (pgvector cosine similarity)

### Why cosine similarity?

Cosine similarity measures the **angle between two vectors**, not their magnitude.
For text embeddings this is the right choice because:

- A freelancer profile and a job post will never be the same *length* of text —
  a 500-word bio shouldn't score lower than a 50-word bio just because it has more content.
- Cosine ignores magnitude and focuses purely on **semantic direction** — do these
  two texts talk about the same concepts?
- A freelancer whose profile says "I build REST APIs in Python with FastAPI and PostgreSQL"
  will produce a vector that points in nearly the same direction as a job description that says
  "Looking for Python/FastAPI backend developer with PostgreSQL experience", even though
  the word choices differ.

The pgvector `<=>` operator computes cosine *distance* (0 = identical, 2 = opposite).
We convert to similarity with `1 - distance`, so 1.0 = perfect match, 0.0 = no relation.

### Stage 1 SQL (from `job_matching_routes.py`)

```sql
SELECT
    jp.job_post_id,
    jp.job_title,
    jp.job_description,
    jp.project_type,
    jp.project_scope,
    jp.experience_level,
    jp.estimated_duration,
    jp.deadline,
    jp.proposal_count,
    je.source_text,
    ROUND((1 - (je.embedding_vector <=> CAST(:vec AS vector)))::numeric, 4) AS similarity_score
FROM job_embedding je
JOIN job_post jp ON jp.job_post_id = je.job_post_id
WHERE jp.status = 'active'
  AND je.embedding_vector IS NOT NULL
ORDER BY similarity_score DESC
LIMIT 100
```

**Tables read:** `job_embedding`, `job_post`
**Input:** The freelancer's `embedding_vector` from `freelancer_embedding`
**Output:** Top-100 jobs ranked by cosine similarity

The HNSW index (`idx_job_embedding_hnsw`) on `job_embedding.embedding_vector`
makes this ANN (Approximate Nearest Neighbour) search run in ~50–100ms even at scale.

---

## Stage 2 — Structured Pre-filter (skill overlap)

Before calling the ML model on all 100 candidates, we drop jobs where the
freelancer matches fewer than **20% of the required skills**. This serves two purposes:

1. Avoids wasting LightGBM inference time on obviously irrelevant jobs.
2. Prevents semantic similarity from surfacing jobs the freelancer genuinely cannot do
   (e.g. a Python developer matching a Kubernetes job because both mention "containers").

```python
# Threshold defined in job_matching_routes.py
_MIN_SKILL_OVERLAP = 0.20  # 20%
```

### Stage 2 SQL

```sql
SELECT jrs.skill_id
FROM job_role_skill jrs
JOIN job_role jr ON jr.job_role_id = jrs.job_role_id
WHERE jr.job_post_id = :jpid AND jrs.is_required = TRUE
```

**Tables read:** `job_role_skill`, `job_role`
**Logic:** For each of the 100 candidates, fetch its required skills, compute
`len(freelancer_skill_ids ∩ required_skill_ids) / len(required_skill_ids)`,
drop the job if this is < 0.20.

---

## Stage 3 — LightGBM Re-Ranker

### What it does

Takes the filtered candidates (typically 30–80 jobs after Stage 2) and
predicts `match_probability` (0–1) for each (freelancer, job) pair using 18
engineered features. Returns the top-N sorted by probability, scaled to 0–100.

### How features are computed (from `ml_ranker.py`)

All features are computed live at inference time from the live PostgreSQL DB.
The freelancer context is loaded **once** and reused for all candidate jobs.

#### Freelancer context (loaded once per request)

```sql
-- Core profile + performance
SELECT f.estimated_rate, f.rate_time, f.total_jobs,
       pr.overall_performance_score, pr.success_rate, pr.total_ratings_received
FROM freelancer f
LEFT JOIN performance_rating pr ON pr.freelancer_id = f.freelancer_id
WHERE f.freelancer_id = :fid

-- Skills (UUIDs only — matched against job skill IDs)
SELECT skill_id FROM freelancer_skill WHERE freelancer_id = :fid

-- Specialities (names lowercased for string matching)
SELECT s.speciality_name
FROM freelancer_speciality fs
JOIN speciality s ON s.speciality_id = fs.speciality_id
WHERE fs.freelancer_id = :fid

-- Languages (names lowercased)
SELECT l.language_name
FROM freelancer_language fl
JOIN language l ON l.language_id = fl.language_id
WHERE fl.freelancer_id = :fid

-- Portfolio count
SELECT COUNT(*) AS cnt FROM portfolio WHERE freelancer_id = :fid

-- Work experience count
SELECT COUNT(*) AS cnt FROM work_experience WHERE freelancer_id = :fid
```

**Tables:** `freelancer`, `performance_rating`, `freelancer_skill`, `freelancer_speciality`,
`speciality`, `freelancer_language`, `language`, `portfolio`, `work_experience`

#### Per-job features (computed for each candidate)

```sql
-- Roles + budgets
SELECT job_role_id, role_budget, budget_type
FROM job_role
WHERE job_post_id = :jpid

-- Required + preferred skills per role
SELECT skill_id, is_required
FROM job_role_skill
WHERE job_role_id = :rid
```

**Tables:** `job_role`, `job_role_skill`

#### All 18 features explained

| Feature | Tables used | Computation |
|---|---|---|
| `cosine_sim` | `job_embedding` | Raw cosine similarity from Stage 1 |
| `skill_overlap_pct` | `job_role_skill`, `freelancer_skill` | `len(f_skills ∩ required_skills) / len(required_skills)` |
| `skill_required_matched` | same | Raw count of required skills matched |
| `skill_required_total` | same | Total required skills in the job |
| `skill_preferred_pct` | same | `len(f_skills ∩ preferred_skills) / len(preferred_skills)` |
| `experience_level_match` | `job_post`, `freelancer` | 1 if `infer_exp_level(total_jobs) ≥ job.experience_level` |
| `exp_delta` | same | `freelancer_exp_num - job_exp_num`, clipped to [-2, +2] |
| `rate_in_budget` | `freelancer`, `job_role` | 1 if `estimated_rate ≤ avg_role_budget × 1.1` |
| `rate_ratio` | same | `estimated_rate / avg_role_budget`, capped at 3.0 |
| `language_match` | `freelancer_language`, `language` | 1 if freelancer speaks English or Indonesian |
| `speciality_match` | `freelancer_speciality`, `speciality` | 1 if any speciality name appears in `job_post.job_title` |
| `domain_match` | same | 1 if any speciality name appears in `job_embedding.source_text` |
| `has_portfolio` | `portfolio` | 1 if COUNT > 0 |
| `work_exp_count` | `work_experience` | COUNT of entries |
| `performance_score` | `performance_rating` | `overall_performance_score`, or NaN if cold start |
| `success_rate_hist` | `performance_rating` | `success_rate`, or NaN if cold start |
| `total_projects` | `freelancer` | `total_jobs` (column renamed for ML; feature name kept to match `feature_cols.json`) |
| `is_cold_start` | `performance_rating` | 1 if `total_ratings_received == 0` |

**Experience level mapping** (no `experience_level` column on `freelancer` table):
```python
def _infer_exp_level(total_projects: int) -> int:
    if total_projects >= 10: return 3  # expert
    if total_projects >= 3:  return 2  # intermediate
    return 1                           # entry_level
```

### Cold-start fallback

When `is_cold_start = 1` (no completed+rated contracts) OR the model's max
predicted probability is below 5%, a **heuristic** replaces the LightGBM output:

```python
p = 0.05 + max(0.0, cosine - 0.5) * 0.4 * overlap
p = min(0.45, p)  # never exceed 45% for cold start
```

This caps new freelancers at 45% match probability — they can't beat experienced
freelancers on ML score alone. The cosine and skill signals still differentiate
between good and poor fits within the cold-start cohort.

---

## Embedding System

### Overview

Both freelancers and jobs are represented as **768-dimensional dense vectors**
generated by `nomic-embed-text` (Ollama, local) or Google Vertex AI `text-embedding-005`.
The vectors are stored in dedicated tables and updated lazily via a dirty-flag sweep.

```
Profile create/update
      │
      ▼
mark_freelancer_dirty()  ──→  freelancer_embedding.embedding_dirty = TRUE
                                         │
                   (sweep every 5 min)   ▼
                   run_sweep_once()  ──→  upsert_freelancer_embedding()
                                          │
                                          ├── build_freelancer_source_text()
                                          │     └── DB reads from 8 tables
                                          ├── get_embedding(source_text)
                                          │     └── Ollama / Vertex AI
                                          └── UPDATE freelancer_embedding
                                                SET embedding_vector = ...
                                                    embedding_dirty = FALSE
```

### Embedding tables

| Table | PK | Unique key | Purpose |
|---|---|---|---|
| `freelancer_embedding` | `embedding_id` | `freelancer_id` | One vector per freelancer |
| `job_embedding` | `embedding_id` | `job_post_id` | One vector per job post |
| `contract_embedding` | `embedding_id` | `contract_id` | One vector per completed contract (RAG context) |

All three have:
- `embedding_vector VECTOR(768)` — the pgvector column
- `source_text TEXT` — the exact text that was embedded (for auditing)
- `embedding_dirty BOOLEAN DEFAULT TRUE` — sweep flag
- `embedding_metadata JSONB` — currently stores `{"dim": 768}`
- HNSW index on `embedding_vector` with `WHERE embedding_vector IS NOT NULL`
- B-tree index on `embedding_dirty WHERE embedding_dirty = TRUE`

### What goes into the freelancer embedding (`source_text_builder.py`)

The freelancer profile is denormalised across **8 tables** into one natural-language document:

```
Specialities: Backend Development (primary), Data Engineering
Skills: Python (advanced), PostgreSQL (advanced), REST API (advanced), ...
Languages: English (fluent), Indonesian (native)
Rate: 35.0 USD/hourly
Bio: Backend developer with 4 years of experience...
Work Experience:
  - Backend Engineer at Gojek (2022-03-01 - 2024-01-31): Built REST APIs...
  - Junior Backend Developer at Tokopedia (2020-06-01 - 2022-02-28): ...
Education:
  - Bachelor of Computer Science from Universitas Indonesia
Portfolio:
  - E-commerce Order API: Designed and built a high-throughput order management API...
  - Data Pipeline Framework: Built a generic ETL framework in Python...
```

**Tables read:** `freelancer`, `freelancer_speciality` + `speciality`,
`freelancer_skill` + `skill`, `freelancer_language` + `language`,
`work_experience`, `education`, `portfolio`

### What goes into the job embedding (`source_text_builder.py`)

The job post is denormalised across **4 tables**:

```
Job Title: Backend API Developer
Description: We're looking for an experienced backend developer...
Type: individual | Scope: medium | Duration: 3 months | Experience Required: intermediate
Role: Backend Developer
Role Description: Own the API layer — design, build, and maintain FastAPI endpoints.
Budget: 3000.0 USD (fixed)
Required Skills: Python, FastAPI, PostgreSQL, REST API
Preferred Skills: Redis (preferred), Docker (nice_to_have)
```

**Tables read:** `job_post`, `job_role`, `job_role_skill` + `skill`

### What goes into the contract embedding (`source_text_builder.py`)

Completed contracts are embedded for the **RAG retrieval** step (not for the homepage feed).
The source text captures what work was done and how it was received:

```
Completed Role: Backend Developer
Job: REST API Backend Service
Description: Build a production REST API backend service using Python/FastAPI...
Client Rating: 5.0/5
Client Review: Excellent backend developer. Built a clean, well-documented REST API...
```

**Tables read:** `contract` → `job_post` (JOIN), `rating` (LEFT JOIN)

---

## LightGBM Training Data

### Why synthetic data?

At launch, there are no real `proposal → accepted → completed → rated` chains.
LightGBM needs labelled (freelancer, job) pairs with a binary outcome (`label = 1`
means "good match", `label = 0` means "poor match"). We generate this synthetically
using the **same feature computation logic that runs in production** (`ml_ranker.py`).

### The training pipeline (`machine_learning/`)

```
01_generate_data.ipynb
      │
      ├── Generates ~700 freelancers (fake but realistic)
      │     → data/freelancers.csv, freelancer_skills.csv, freelancer_specialities.csv
      │       freelancer_languages.csv, work_experiences.csv, educations.csv, portfolios.csv
      │
      ├── Generates ~600 job posts
      │     → data/job_posts.csv, job_roles.csv, job_role_skills.csv
      │
      ├── Generates ~3,000 proposals + contracts + ratings
      │     → data/proposals.csv, contracts.csv, ratings.csv, performance_ratings.csv
      │
      └── Computes pre-built features for every (freelancer, job) pair
            → data/ml_training_pairs.csv  (7,000+ rows × 21 columns)

02_train_model.ipynb
      │
      ├── Loads ml_training_pairs.csv
      ├── Splits train/test (80/20)
      ├── Trains LightGBM with early stopping (best_iteration: 617 trees)
      ├── Evaluates AUC-ROC vs cosine similarity baseline
      └── Saves:
            models/lgbm_job_matcher.pkl   ← the model
            models/feature_cols.json      ← ordered feature list (exact column order)
            models/model_summary.json     ← metrics snapshot
```

### What `ml_training_pairs.csv` looks like

Each row is one (freelancer, job) pair with all 18 features pre-computed plus the outcome label:

```
cosine_sim, skill_overlap_pct, skill_required_matched, skill_required_total,
skill_preferred_pct, experience_level_match, exp_delta, rate_in_budget, rate_ratio,
language_match, speciality_match, domain_match, has_portfolio, work_exp_count,
performance_score, success_rate_hist, total_projects, is_cold_start,
freelancer_id, job_post_id, label
```

Example rows:
```
0.762, 0.75, 3, 4, 0.50, 1, 1, 0, 2.96, 0, 1, 1, 0, 2, 76.76, 60.0,  42, 0, ..., ..., 1
0.573, 0.25, 1, 4, 0.50, 0,-1, 0, 3.00, 1, 1, 1, 0, 3, 87.70, 100.0, 0,  0, ..., ..., 0
```

**Label definition:** `label = 1` if the proposal for this (freelancer, job) pair was **accepted
AND the resulting contract was completed with a rating ≥ 4.0**. Everything else is `label = 0`.
This makes the model predict "will this freelancer actually complete the job well?" — not just
"did they apply?".

### How training mirrors production exactly

The key design constraint: **the same 18 features computed in `ml_ranker.py` must be
computable from the synthetic CSVs during training**. The notebook generates freelancers
with the exact same schema as the `freelancer` table, computes skills the same way,
infers experience level with the same `_infer_exp_level()` heuristic, etc.

| Training (synthetic CSV) | Production (live DB) |
|---|---|
| `freelancers.csv` → `estimated_rate`, `total_jobs` | `SELECT estimated_rate, total_jobs FROM freelancer` |
| `freelancer_skills.csv` → skill UUID set | `SELECT skill_id FROM freelancer_skill WHERE freelancer_id = :fid` |
| `performance_ratings.csv` → `overall_performance_score`, `success_rate` | `SELECT ... FROM performance_rating WHERE freelancer_id = :fid` |
| `job_roles.csv` + `job_role_skills.csv` → required/preferred skill sets | `SELECT skill_id, is_required FROM job_role_skill WHERE job_role_id = :rid` |
| Pre-computed cosine sim (from synthetic embedding scores) | Live pgvector `<=>` operator on `job_embedding` |

The `feature_cols.json` file is the bridge — it records the **exact column order** that
the model was trained with. At inference time, `ml_ranker.py` reads this file and calls
`feat_df[feat_cols]` to ensure column order matches before `model.predict_proba(X)`.

### Model performance

| Metric | Value |
|---|---|
| AUC-ROC | **0.9679** |
| Baseline cosine AUC | 0.9570 |
| AUC improvement over cosine-only | +0.0109 |
| 5-fold CV AUC | 0.9696 ± 0.006 |
| Best iteration | 617 trees |
| Training size | 5,160 pairs |
| Model size | 1,398 KB |
| Inference (100 jobs) | **4.6ms** |

### Why LightGBM over other models?

| Model | Speed | Cold start | Why not |
|---|---|---|---|
| Collaborative filtering | Fast | Fails (no interaction history) | No interaction data at launch |
| Neural network | Slow (needs GPU) | Requires imputation | Too heavy for page load |
| Random Forest | Medium | Native NaN support | Slower than LightGBM |
| **LightGBM** | **4.6ms / 100 jobs** | **Native NaN routing** | ← chosen |
| XGBoost | ~2× slower | Native NaN support | Slower than LightGBM |

LightGBM's C++ tree traversal and histogram-based splitting make it the fastest
gradient boosted tree implementation at inference time. Its native NaN handling
routes cold-start samples (no performance history) to the optimal tree split
learned during training — no imputation or separate model branch needed.

---

## RAG Deep Analysis

### What is RAG here?

RAG (Retrieval-Augmented Generation) means the LLM prompt is **dynamically assembled
from live DB data** at request time — it's not a fixed template. Every call produces
a different prompt because the freelancer's skills, the job's requirements, and the
past contracts retrieved all come from the DB at that moment.

The "retrieval" is the DB reads. The "augmentation" is injecting that retrieved
context into the prompt. The "generation" is the LLM producing structured JSON.

### Full RAG pipeline (`rag_analyser.py`)

```
GET /ai/job_matching/analyse/job/{job_post_id}
          │
          ▼
 ┌─────────────────────────────────────────────┐
 │  Step 1: Retrieve job context               │
 │  Tables: job_post, job_role, job_role_skill, │
 │          skill                              │
 │  SQL: one query for job post + aggregated   │
 │       array_agg of skills per role          │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 2: Retrieve freelancer context        │
 │  Tables: freelancer, performance_rating,    │
 │          freelancer_skill, skill,           │
 │          freelancer_speciality, speciality, │
 │          freelancer_language, language,     │
 │          portfolio (top 3),                 │
 │          work_experience (top 3)            │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 3: Retrieve past contracts (RAG docs) │
 │                                             │
 │  Primary: cosine similarity                 │
 │  (if contract_embedding vectors exist)      │
 │  SELECT ... ORDER BY                        │
 │    contract_embedding <=> job_embedding     │
 │  → most relevant past work to this job      │
 │                                             │
 │  Fallback: recency order                    │
 │  SELECT ... ORDER BY c.end_date DESC        │
 │  (used if sweep hasn't run yet)             │
 │                                             │
 │  Tables: contract_embedding, contract,      │
 │          job_post, job_embedding, rating    │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 4: Pre-compute skill matching         │
 │                                             │
 │  For each job role:                         │
 │  - required = skills with (required) tag    │
 │  - matched_req = required ∩ freelancer skills│
 │  - missing_req = required − freelancer skills│
 │  - coverage_pct = matched/required × 100    │
 │                                             │
 │  Done in Python (no extra DB query)         │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 5: Build grounded prompt              │
 │                                             │
 │  Sections:                                  │
 │  1. JOB POST (background context)           │
 │  2. FREELANCER PROFILE                      │
 │  3. PAST COMPLETED CONTRACTS (RAG context)  │
 │  4. PER-ROLE SKILL MATCH (pre-computed)     │
 │  5. JSON template + scoring guidance        │
 │                                             │
 │  ~2,000–4,000 characters                    │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 6: Call LLM                           │
 │                                             │
 │  mode=local → Ollama (gemma4:e2b)           │
 │            → on failure, fall back to       │
 │               Google Gemini (Vertex AI)     │
 │  mode=api  → Gemini directly                │
 │                                             │
 │  temperature=0.15, max_tokens=4096          │
 │  timeout=90s                                │
 └────────────────────┬────────────────────────┘
                      │
                      ▼
 ┌─────────────────────────────────────────────┐
 │  Step 7: Post-process LLM output            │
 │                                             │
 │  - Parse JSON (handles markdown fences,     │
 │    preamble text, plain JSON)               │
 │  - Overwrite matching_skills &              │
 │    missing_required_skills with             │
 │    server-computed values (LLM can't        │
 │    be trusted to copy them correctly)       │
 │  - Apply score ceiling:                     │
 │    max_score = min(100, coverage_pct + 25)  │
 │    (prevents 0%-coverage role scoring 90)  │
 │  - Derive recommendation from capped score  │
 │    ≥65 → apply, 40–64 → consider, <40 skip │
 │  - overall_match_score = best role score    │
 └─────────────────────────────────────────────┘
```

### RAG past-contract retrieval SQL

When contract embeddings are available (primary path):

```sql
SELECT jp.job_title,
       jp.job_description,
       c.status AS contract_status,
       r.overall_rating,
       r.review_text,
       r.result_quality_score,
       r.communication_score,
       1 - (ce.embedding_vector <=> je.embedding_vector) AS similarity
FROM contract_embedding ce
JOIN contract c  ON c.contract_id  = ce.contract_id
JOIN job_post jp ON jp.job_post_id = c.job_post_id
JOIN job_embedding je ON je.job_post_id = :jpid
LEFT JOIN rating r ON r.contract_id = c.contract_id
WHERE ce.freelancer_id = :fid
  AND ce.embedding_vector IS NOT NULL
  AND c.status = 'completed'
ORDER BY ce.embedding_vector <=> je.embedding_vector   -- cosine distance ASC = most similar first
LIMIT 5
```

**Tables:** `contract_embedding`, `contract`, `job_post`, `job_embedding`, `rating`

This retrieves the freelancer's past completed contracts that are **most semantically
similar to the target job** — not just the most recent ones. A freelancer who built an
API 2 years ago has that contract retrieved when analysing an API role, even if their
most recent contract was a data pipeline.

### RAG response format

```json
{
  "overall_match_score": 78,
  "overall_recommendation": "apply",
  "overall_recommendation_reason": "Strong fit for Backend Developer role; poor fit for DevOps role.",
  "roles": [
    {
      "role_title": "Backend Developer",
      "match_score": 78,
      "recommendation": "apply",
      "recommendation_reason": "4/4 required skills matched at advanced level. Past contract 'REST API Backend Service' rated 5/5 directly demonstrates this role's core work.",
      "matching_skills": ["Python", "FastAPI", "PostgreSQL", "REST API"],
      "missing_required_skills": [],
      "strengths": ["..."],
      "gaps": [],
      "skill_tips": ["..."]
    }
  ],
  "job_post_id": "...",
  "freelancer_id": "...",
  "rag_sources": {
    "past_contracts_used": 2,
    "portfolio_items": 3,
    "work_experience": 3,
    "freelancer_skills": 8,
    "job_roles": 1
  }
}
```

---

## Embedding Dirty Flag + Sweep Worker

### When embeddings get marked dirty

| Event | What gets marked dirty |
|---|---|
| `PUT /freelancers/{id}` | `freelancer_embedding.embedding_dirty = TRUE` |
| `POST /freelancer-skills` | `freelancer_embedding.embedding_dirty = TRUE` |
| `PUT /freelancer-skills/{id}` | `freelancer_embedding.embedding_dirty = TRUE` |
| `DELETE /freelancer-skills/{id}` | `freelancer_embedding.embedding_dirty = TRUE` |
| `POST /job-posts` | `job_embedding.embedding_dirty = TRUE` (on creation) |
| `PUT /job-posts/{id}` | `job_embedding.embedding_dirty = TRUE` |
| `POST /job-role-skills` | `job_embedding.embedding_dirty = TRUE` (via role → post lookup) |
| `PUT /contracts/{id}` with `status=completed` | `contract_embedding.embedding_dirty = TRUE` (new row) |
| `POST /ratings` | `contract_embedding.embedding_dirty = TRUE` (review text changes the vector) |

### Sweep worker (`sweep_worker.py`)

Runs every **5 minutes** as an asyncio background task (started in `main.py`).
Processes dirty records in batches of 50 per type:

```
run_sweep_once()
    │
    ├── SELECT freelancer_id FROM freelancer_embedding
    │   WHERE embedding_dirty = TRUE LIMIT 50
    │   → for each: upsert_freelancer_embedding(fid)
    │
    ├── SELECT job_post_id FROM job_embedding
    │   WHERE embedding_dirty = TRUE LIMIT 50
    │   → for each: upsert_job_embedding(jpid)
    │
    └── SELECT contract_id FROM contract_embedding
        WHERE embedding_dirty = TRUE LIMIT 50
        → for each: upsert_contract_embedding(cid)
```

### Manual trigger

```
POST /ai/job_matching/embed/freelancer/{id}   → marks dirty + creates row if missing
POST /ai/job_matching/embed/job/{id}          → marks dirty + creates row if missing
POST /ai/job_matching/sweep                   → runs sweep immediately (synchronous)
```

---

## Client-side: Job-to-Freelancers Search

The reverse direction — client posts a job and wants to find matching freelancers.
Simpler than the freelancer feed: **one-stage cosine search only**, no LightGBM.

```sql
SELECT
    f.freelancer_id, f.full_name, f.bio,
    f.estimated_rate, f.rate_time, f.rate_currency,
    f.total_jobs,
    pr.overall_performance_score, pr.success_rate, pr.average_result_quality,
    fe.source_text,
    ROUND((1 - (fe.embedding_vector <=> CAST(:vec AS vector)))::numeric, 4) AS similarity_score
FROM freelancer_embedding fe
JOIN freelancer f ON f.freelancer_id = fe.freelancer_id
LEFT JOIN performance_rating pr ON pr.freelancer_id = fe.freelancer_id
WHERE fe.embedding_vector IS NOT NULL
  [AND f.estimated_rate >= :min_rate]
  [AND f.estimated_rate <= :max_rate]
  [AND pr.overall_performance_score >= :min_perf]
ORDER BY similarity_score DESC
LIMIT :limit
```

**Tables:** `freelancer_embedding`, `freelancer`, `performance_rating`
**Auth:** Client Bearer token; must own the job post.

---

## API Endpoints

All under prefix `/ai/job_matching`.

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/match/freelancer-to-jobs` | Freelancer token | 3-stage ranked job feed |
| GET | `/match/job-to-freelancers/{job_post_id}` | Client token (must own job) | Top freelancers for a job |
| GET | `/analyse/job/{job_post_id}` | Freelancer token | RAG + LLM deep analysis |
| POST | `/embed/freelancer/{freelancer_id}` | Any token | Queue freelancer re-embed |
| POST | `/embed/job/{job_post_id}` | Any token | Queue job re-embed |
| POST | `/sweep` | Any token | Run full sweep now |
| GET | `/test_ai_local` | None | Test Ollama connectivity |

### `/match/freelancer-to-jobs` query params

| Param | Default | Range | Description |
|---|---|---|---|
| `limit` | 10 | 1–50 | Number of results to return |
| `experience_level` | — | entry/intermediate/expert | Optional filter |

### `/match/job-to-freelancers/{id}` query params

| Param | Default | Description |
|---|---|---|
| `limit` | 10 | Number of results |
| `min_rate` | — | Minimum hourly rate filter |
| `max_rate` | — | Maximum hourly rate filter |
| `min_performance` | — | Minimum `overall_performance_score` |

---

## Files in this module

| File | Purpose |
|---|---|
| `job_matching_routes.py` | FastAPI router — all endpoints, Stage 1 + 2 SQL |
| `ml_ranker.py` | Stage 3: load LightGBM, compute 18 features from DB, re-rank |
| `rag_analyser.py` | RAG pipeline: retrieve context, build prompt, call LLM, post-process |
| `embedding_manager.py` | dirty-flag helpers + upsert functions for all 3 embedding tables |
| `source_text_builder.py` | Denormalise freelancer/job/contract DB data into embedding source text |
| `embedding_service.py` | Call Ollama or Vertex AI to get a 768-dim float vector |
| `sweep_worker.py` | Background loop: process dirty embedding records every 5 minutes |
| `machine_learning/` | Training notebooks, generated data CSVs, trained model artefacts |

---

## How to Run / Set Up

### Prerequisites

- PostgreSQL container (`capstone-postgresql`) with `pgvector` extension
- Ollama running locally with `nomic-embed-text` and your LLM model (e.g. `gemma4:e2b`)

### Environment variables (`.env`)

```env
OLLAMA_URL=http://127.0.0.1:11434/api/generate
OLLAMA_TEXT_EMBEDDING=nomic-embed-text
OLLAMA_LLM=gemma4:e2b
LLM=local          # "local" = Ollama first, Gemini fallback; "api" = Gemini only
```

### Train the LightGBM model

If `machine_learning/models/lgbm_job_matcher.pkl` doesn't exist:

```bash
docker exec -it capstone-backend bash
apt-get update -qq && apt-get install -y -q libgomp1
pip install lightgbm scikit-learn joblib jupyter nbconvert matplotlib seaborn
cd /app/ai_related/job_matching/machine_learning

# Step 1: generate synthetic training data (produces 7,000+ labelled pairs)
jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 \
  01_generate_data.ipynb --output 01_generate_data.ipynb

# Step 2: train model, evaluate, save artefacts
jupyter nbconvert --to notebook --execute --ExecutePreprocessor.timeout=600 \
  02_train_model.ipynb --output 02_train_model.ipynb

# Verify
python3 - <<'EOF'
import joblib, json
model   = joblib.load('models/lgbm_job_matcher.pkl')
summary = json.load(open('models/model_summary.json'))
print(f"AUC-ROC:  {summary['auc_roc']}")
print(f"Baseline: {summary['baseline_cosine_auc']}")
print(f"Speed:    {summary['inference_100_jobs_ms']}ms / 100 jobs")
EOF
```

### Seed embeddings

After the server starts with populated data:

```bash
# Embed everything now (instead of waiting for the 5-min sweep)
curl -X POST http://localhost:8000/ai/job_matching/sweep \
  -H "Authorization: Bearer <any_valid_token>"
```

---

## Log output guide

All logs follow the format:
```
TIMESTAMP | LEVEL | SERVICE | ROUTE | MESSAGE
```

Key services:
- `JOB_MATCHING` — request start/end, Stage 1 + 2 timing, filter stats
- `ML_RANKER` — Stage 3 inference, feature computation, model load
- `RAG_ANALYSER` — context retrieval, LLM calls, JSON parsing, score post-processing
- `EMBEDDING_MANAGER` — embedding upsert lifecycle (created/updated/skipped)
- `SOURCE_TEXT_BUILDER` — source text assembly per section
- `SWEEP_WORKER` — background sweep cycles, batch counts
- `EMBEDDING_SERVICE` — Ollama/Google API calls, vector dimension

Example log for a homepage request:
```
INFO  | JOB_MATCHING | | freelancer-to-jobs request started | freelancer_id=abc | limit=10
INFO  | JOB_MATCHING | | Stage 1 complete | candidates=100 | cosine_range=[0.612, 0.941] | time=87ms
INFO  | JOB_MATCHING | | Stage 2 complete | passed=62 | dropped_low_overlap=38 | time=210ms
DEBUG | ML_RANKER    | | Freelancer context loaded | skills=12 | cold_start=False | performance=78.5
INFO  | ML_RANKER    | | LightGBM inference | candidates=62 | time=3.8ms | prob_range=[0.04, 0.89]
INFO  | JOB_MATCHING | | freelancer-to-jobs complete | returned=10 | stage1=87ms stage2=210ms stage3=4ms | total=301ms
```

Example log for a RAG analysis:
```
INFO  | JOB_MATCHING  | | analyse/job request started | freelancer_id=abc | job_post_id=xyz
DEBUG | RAG_ANALYSER  | | Retrieving job context | job_post_id=xyz
DEBUG | RAG_ANALYSER  | | Job context retrieved | title='Backend API Developer' | roles=1 | total_skills=6
DEBUG | RAG_ANALYSER  | | Freelancer context retrieved | name='Budi Santoso' | skills=8 | portfolio=2 | work_exp=2
DEBUG | RAG_ANALYSER  | | Using vector similarity to rank past contracts
DEBUG | RAG_ANALYSER  | | Past contracts retrieved | count=2 | rated=2 | avg_rating=4.50 | method=vector_similarity
INFO  | RAG_ANALYSER  | | Calling Ollama | model=gemma4:e2b | prompt_chars=2847
INFO  | RAG_ANALYSER  | | Ollama response received | chars=1203
INFO  | RAG_ANALYSER  | | LLM JSON parsed | source=ollama | time=8420ms | recommendation=apply
INFO  | RAG_ANALYSER  | | RAG analysis complete | status=success | total_time=8560ms | overall_score=78
INFO  | JOB_MATCHING  | | analyse/job complete | match_score=78 | recommendation=apply | total_time=8561ms
```
