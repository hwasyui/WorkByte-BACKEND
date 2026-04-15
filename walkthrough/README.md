# Walkthrough — End-to-End API Demo

This script hits the real running backend and walks through every major feature,
from user registration to the 3-stage AI job matching pipeline.

---

## How to Run

Make sure the backend and PostgreSQL containers are up, then:

```bash
# Enter the backend container
docker exec -it capstone-backend bash

# Install requests if not already installed
pip install requests

# Run from the project root
python walkthrough/walkthrough.py
```

Or run it from outside the container if your backend is exposed on localhost:8000:
```bash
pip install requests
python backend/walkthrough/walkthrough.py
```

> **Before running:** make sure the DB is fresh (run `delete_data.sql` first),
> and that Ollama is running with `nomic-embed-text` and your LLM model loaded.

---

## What the Script Does — Step by Step

### 1. Register Users
Three users are created via `POST /auth/register`:
- **Budi Santoso** — freelancer (cold start, no completed contracts)
- **TechStartup Inc.** — client 1
- **DataCorp Solutions** — client 2

Registration auto-creates the linked `freelancer` / `client` profile rows.

### 2. Login + Get Tokens
Each user logs in via `POST /auth/login` and gets a JWT bearer token.
All subsequent requests use these tokens.

### 3. Fetch Profile IDs
`GET /auth/me` returns the `user_id`, then `GET /freelancers` and `GET /clients`
are used to find the matching `freelancer_id` / `client_id` needed for later calls.

### 4. Fill in Freelancer Profile
`PUT /freelancers/{id}` sets Budi's rate ($35/hour), bio, and currency.

### 5. Create Skills, Specialities, Languages
These are shared lookup records used by both the freelancer and the job posts:

| Skills created | Category |
|---|---|
| Python, PostgreSQL, REST API, FastAPI, Data Modeling | hard_skill |
| Docker, Redis, Kubernetes, AWS, Git | tool |
| React, Apache Spark | hard_skill |

Specialities: `Backend Development`, `Data Engineering`, `DevOps`
Languages: `English`, `Indonesian`

### 6. Build the Freelancer Profile

**Skills assigned to Budi:**
| Skill | Level |
|---|---|
| Python | advanced |
| PostgreSQL | advanced |
| REST API | advanced |
| FastAPI | intermediate |
| Docker | intermediate |
| Git | advanced |
| Data Modeling | intermediate |
| Redis | beginner |

**Speciality:** Backend Development (primary)
**Languages:** English (fluent), Indonesian (native)
**Work Experience:** Backend Engineer @ Gojek, Junior Dev @ Tokopedia
**Portfolio:** 2 projects (Order API, ETL Framework)

### 7. Create Job Posts

Four jobs are created across the two clients, designed to produce a range of match scores:

| Job | Client | Expected Match | Why |
|---|---|---|---|
| Backend API Developer | TechStartup | **Strong** | Python, FastAPI, PostgreSQL — Budi's core skills |
| Full Stack Engineer | TechStartup | **Partial** | Needs React — Budi doesn't have it |
| Data Engineer | DataCorp | **Partial** | Needs Apache Spark — Budi doesn't have it |
| DevOps / Platform Engineer | DataCorp | **Poor** | Requires Kubernetes + AWS — not in Budi's skillset |

### 8. Create Job Roles and Assign Skills
Each job post gets one `job_role` via `POST /job-roles`, then skills are
attached via `POST /job-role-skills` with `is_required` and `importance_level`.

### 9. Activate All Job Posts
`PUT /job-posts/{id}` sets `status = "active"`.
**Stage 1 only matches active jobs** — draft jobs are invisible to the matcher.

### 10. Trigger Embeddings
- `POST /ai/job_matching/embed/freelancer/{id}` — queues Budi's profile for embedding
- `POST /ai/job_matching/embed/job/{id}` — queues each job post for embedding

These are background tasks. The sweep call below makes them run synchronously.

### 11. Run the Sweep
`POST /ai/job_matching/sweep` forces all dirty embedding records to be
generated right now instead of waiting for the 5-minute background loop.

The sweep calls Ollama (`nomic-embed-text`) to generate 768-dim vectors for:
- `freelancer_embedding` — Budi's profile vector
- `job_embedding` — one vector per job post

### 12. Stage 1–3 Job Matching
`GET /ai/job_matching/match/freelancer-to-jobs` runs the full pipeline:

```
Stage 1 — pgvector cosine search
  Compares Budi's embedding vector against all active job vectors.
  Returns top-100 by cosine similarity.

Stage 2 — Skill pre-filter
  Drops any job where Budi's required-skill overlap < 20%.
  DevOps job may get filtered here if Budi matches < 1/5 required skills.

Stage 3 — LightGBM re-ranker
  Predicts match_probability for each surviving job using 18 features.
  Cold-start note: performance_score and success_rate_hist are NaN for Budi
  (no completed contracts). LightGBM handles NaN natively — no imputation needed.
  Returns top-N jobs sorted by match_probability.
```

Output shows: rank, job title, match_probability, cosine similarity, skill overlap %.

### 13. RAG Deep Analysis — Best Match
`GET /ai/job_matching/analyse/job/{id}` runs on the top-ranked job.

The RAG pipeline:
1. Retrieves job requirements from DB (roles, skills, budget)
2. Retrieves Budi's full profile from DB (skills, specialities, work exp, portfolio)
3. Retrieves past contracts — Budi has none (cold start), so this section is empty
4. Builds a grounded prompt combining all retrieved context
5. Calls the LLM (Ollama or Gemini fallback) to analyse the match
6. Returns structured JSON

LLM output includes:
- `match_score` (0–100)
- `strengths` — what Budi does well for this specific job
- `gaps` — what's missing
- `recommendation` — apply / consider / skip
- `recommendation_reason` — one sentence with evidence
- `skill_tips` — concrete things Budi could do to improve fit

### 14. RAG Deep Analysis — Poor Match
Same endpoint but on the DevOps job, to contrast with the strong match.
The LLM should score this much lower and recommend "skip".

---

## Cold Start Behaviour

Budi has no completed contracts, so:
- `performance_score` → `NaN` in the feature matrix
- `success_rate_hist` → `NaN` in the feature matrix
- `is_cold_start` → `1`

LightGBM routes NaN samples to the optimal split direction learned during
training — the model uses all 16 non-history features and still produces
a meaningful `match_probability`. No imputation, no fallback logic.

---

## Expected Output Shape

```
============================================================
  Step 12: Run the 3-stage job matching pipeline (limit=10)
============================================================

  Results (3 jobs returned):
  Rank  Job Title                           Match%     Cosine     Skill Overlap
  ----- ----------------------------------- ---------- ---------- ---------------
  #1    Backend API Developer               82.4       0.871      100.0%
  #2    Full Stack Engineer                 61.2       0.743      75.0%
  #3    Data Engineer                       48.7       0.698      50.0%
```

The DevOps job either gets filtered in Stage 2 or scores very low in Stage 3.

---

## Files

| File | Purpose |
|---|---|
| `walkthrough.py` | The main script — runs all steps sequentially |
| `walkthrough.sh` | Original shell-based walkthrough (broader API coverage) |
| `job_matching.md` | Notes on the job matching architecture |
| `README.md` | This file |
