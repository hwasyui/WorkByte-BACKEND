# RAG Job Matching — Complete Walkthrough

## Quick answer: Job-level or Role-level embedding?

We embed at the **job post level** — one vector per `job_post_id`. However, the source text aggregates **all roles and their required skills** under that job post, so the single vector captures the full job context. This keeps similarity search simple (one query against `job_embedding`) while still encoding per-role skill requirements into the text.

---

## The Three-Phase Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 1 — BLOB GENERATION                                              │
│  JOIN query across 7+ tables → natural language "profile document"      │
│  source_text_builder.py                                                  │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │ source_text (plain text)
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 2 — EMBEDDING                                                     │
│  source_text → float vector (768 dimensions)                             │
│  embedding_service.py  →  embedding_manager.py                           │
│                                                                          │
│  Local (Ollama):  nomic-embed-text  → 768-dim                            │
│  API   (Google):  text-embedding-005 → 768-dim                           │
│  Mode controlled by LLM env var ("local" / "api")                        │
└─────────────────────────┬───────────────────────────────────────────────┘
                          │ VECTOR(768)
                          ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  PHASE 3 — SIMILARITY SEARCH                                             │
│  Cosine distance via pgvector  +  SQL hard filters                       │
│  job_matching_routes.py                                                   │
│                                                                          │
│  SELECT ... FROM freelancer_embedding fe                                  │
│  JOIN freelancer f ...                                                   │
│  WHERE <hard filters>                                                    │
│  ORDER BY fe.embedding_vector <=> :job_vec::vector                       │
│  LIMIT 10                                                                │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Embedding Update Strategy

```
User mutates profile data (skill added, bio updated, etc.)
           │
           ▼  (FastAPI BackgroundTask — fires AFTER response sent)
   upsert_*_embedding(id)
           │
    ┌──────┴──────────────────────────────────────────────────────┐
    │  build_*_source_text()  →  JOIN query across all tables     │
    │  get_embedding(text)    →  Ollama or Google                 │
    │  UPDATE/INSERT embedding table  →  dirty = FALSE            │
    └─────────────────────────────────────────────────────────────┘

If the embedding call fails (Ollama down, network error):
    mark_dirty = TRUE  ──►  SWEEP WORKER picks it up in ≤5 min
```

### What triggers re-embedding

| Action | Freelancer embedding updated | Job embedding updated |
|--------|-----------------------------|-----------------------|
| Create / update freelancer profile | ✓ | |
| Add / update / remove skill | ✓ | |
| Add / update / remove speciality | ✓ | |
| Add / update / remove language | ✓ | |
| Add / update / remove work experience | ✓ | |
| Add / update / remove education | ✓ | |
| Add / update / remove portfolio item | ✓ | |
| Create / update job post | | ✓ |
| Add / update / remove job role | | ✓ |
| Add / update / remove job role skill | | ✓ |

---

## Source Text Structure

### Freelancer source_text

```
Specialities: Backend Development (primary), DevOps
Skills: Python (expert), Django (advanced), PostgreSQL (intermediate)
Languages: English (fluent), Indonesian (native)
Rate: 35 USD/hourly
Bio: Experienced backend developer specialising in scalable APIs.
Work Experience:
  - Senior Backend Engineer at Tokopedia (2021-01-01 - Present): Built microservices...
  - Backend Developer at Gojek (2019-01-01 - 2020-12-31): REST APIs for payments.
Education:
  - B.Sc Computer Science from Universitas Indonesia
Portfolio:
  - E-commerce API: High-performance product catalog with Redis caching.
```

Tables joined: `freelancer` + `freelancer_speciality` + `speciality` + `freelancer_skill` + `skill` + `freelancer_language` + `language` + `work_experience` + `education` + `portfolio`

### Job source_text

```
Job Title: E-commerce Platform Backend
Description: We need a backend developer for our online marketplace...
Type: team | Scope: large | Duration: 3 months | Experience Required: intermediate
Role: Backend Engineer
Role Description: Own the API design and implementation.
Budget: 3000 USD (fixed)
Required Skills: Python, Django, PostgreSQL
Preferred Skills: Redis (preferred), Docker (nice_to_have)
```

Tables joined: `job_post` + `job_role` + `job_role_skill` + `skill`

---

## File Reference

| File | Purpose |
|------|---------|
| `embedding_service.py` | Calls Ollama or Google to get a float vector from text. Handles mode switching and fallback. |
| `source_text_builder.py` | Builds the profile document strings from DB joins. Pure read — no side effects. |
| `embedding_manager.py` | Orchestrates: calls builder → calls service → upserts DB. Also exposes `mark_*_dirty()` helpers. |
| `sweep_worker.py` | Asyncio background loop that re-embeds all `embedding_dirty=TRUE` records every 5 minutes. |
| `job_matching_routes.py` | FastAPI routes for similarity search, manual embed triggers, and sweep. |
| `freelancer_embeddings/freelancer_embedding_routes.py` | Thin admin routes to view embedding metadata and queue re-embeds. |
| `job_embeddings/job_embedding_routes.py` | Same for job embeddings. |

---

## Embedding Tables Schema

```sql
-- freelancer_embedding
embedding_id       UUID  PRIMARY KEY
freelancer_id      UUID  UNIQUE  FK → freelancer
embedding_vector   VECTOR(768)   -- null until first embed
source_text        TEXT          -- exactly what was embedded
embedding_metadata JSONB         -- {"dim": 768}
embedding_dirty    BOOLEAN       -- TRUE = needs re-embed
created_at / updated_at

-- job_embedding
embedding_id       UUID  PRIMARY KEY
job_post_id        UUID  UNIQUE  FK → job_post
embedding_vector   VECTOR(768)
source_text        TEXT
embedding_metadata JSONB
embedding_dirty    BOOLEAN
created_at / updated_at
```

Indexes:
- **HNSW** on `embedding_vector` (cosine) for fast ANN search
- **Partial B-tree** on `embedding_dirty = TRUE` for fast sweep queries

---

## API Endpoints

### Job Matching  (`/ai/job_matching/...`)

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/match/freelancer-to-jobs` | Freelancer | Find best active jobs for the current freelancer |
| GET | `/match/job-to-freelancers/{job_post_id}` | Any | Find best freelancers for a job |
| POST | `/embed/freelancer/{freelancer_id}` | Any | Queue re-embed for a freelancer |
| POST | `/embed/job/{job_post_id}` | Any | Queue re-embed for a job |
| POST | `/sweep` | Any | Immediately process all dirty embeddings |
| GET | `/test_ai_local` | None | Test Ollama connectivity |

Query params for `/match/freelancer-to-jobs`:
- `limit` (1–50, default 10)
- `experience_level` (entry / intermediate / expert)

Query params for `/match/job-to-freelancers/{id}`:
- `limit` (1–50, default 10)
- `min_rate` / `max_rate` (float)
- `min_performance` (float, 0–100)

### Freelancer Embeddings (`/freelancer-embeddings/...`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/freelancer-embeddings` | My embedding metadata |
| GET | `/freelancer-embeddings/{id}` | Any freelancer's metadata |
| POST | `/freelancer-embeddings/embed` | Queue re-embed for myself |
| POST | `/freelancer-embeddings/{id}/embed` | Queue re-embed for any freelancer |

### Job Embeddings (`/job-embeddings/...`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/job-embeddings/{job_post_id}` | Job embedding metadata |
| POST | `/job-embeddings/{job_post_id}/embed` | Queue re-embed for a job |

---

## Environment Variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `LLM` | embedding_service | `"local"` (Ollama→Google fallback) or `"api"` (Google only) |
| `OLLAMA_URL` | embedding_service | Ollama base URL (auto-stripped to `/api/embeddings`) |
| `OLLAMA_TEXT_EMBEDDING` | embedding_service | Ollama model name (default: `nomic-embed-text`) |
| `GOOGLE_PROJECT_ID` | embedding_service | GCP project for Vertex AI |
| `GOOGLE_TEXT_EMBEDDING` | embedding_service | Google model name (default: `text-embedding-005`) |
| `GOOGLE_LOCATION` | embedding_service | Vertex AI region (default: `us-central1`) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Google SDK | Path to service account JSON |

---

## What NOT to embed (use for filtering/reranking instead)

| Data | Why not embed | How to use it |
|------|--------------|---------------|
| `performance_rating` scores | Numbers don't encode semantically | `LEFT JOIN` + `WHERE min_performance` filter |
| `estimated_rate` | Numeric range filter | `WHERE f.estimated_rate BETWEEN :min AND :max` |
| `experience_level` | Categorical hard filter | `WHERE jp.experience_level = :level` |
| `success_rate`, `total_projects` | Trust signals | Post-retrieval reranking |
| `proposal_count`, `view_count` | Engagement signals | Not used in matching |
| `profile_picture_url`, `cv_file_url` | Binary/URL, no semantics | Excluded entirely |

---

## Running the Job Matching Walkthrough

```bash
# Inside the container
docker exec capstone-backend bash -c "
  cd /backend &&
  ./walkthrough/walkthrough.sh
"
```

Or run the server first, then test individually:

```bash
# Embed a freelancer
curl -X POST http://localhost:8000/ai/job_matching/embed/freelancer/<id> \
  -H "Authorization: Bearer <token>"

# Match freelancer to jobs
curl "http://localhost:8000/ai/job_matching/match/freelancer-to-jobs?limit=10" \
  -H "Authorization: Bearer <freelancer_token>"

# Match job to freelancers
curl "http://localhost:8000/ai/job_matching/match/job-to-freelancers/<job_post_id>?limit=5&min_performance=70" \
  -H "Authorization: Bearer <token>"

# Trigger manual sweep
curl -X POST http://localhost:8000/ai/job_matching/sweep \
  -H "Authorization: Bearer <token>"
```
