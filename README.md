# 🚀 WorkByte Backend

Backend service for **WorkByte**, a platform connecting clients and freelancers with intelligent job matching and seamless collaboration.

---

## 📌 Overview

This repository contains the backend system powering WorkByte, including:

* Authentication & authorization (JWT-based)
* User management (Client & Freelancer)
* Job posting & proposal system
* API services for frontend integration
* AI-powered freelancer experience features

---

## 🏗️ Tech Stack

* **Framework**: FastAPI
* **Language**: Python
* **Database**: PostgreSQL
* **ORM**: SQLAlchemy
* **Containerization**: Docker

---

## 🐳 Development

All development is done inside the Docker container environment.

---

## 🤖 AI Features

### 1. Job Matching

**Owner:** Angelica Suti Whiharto `[ASW]` (`hwasyui`)

**Source:** [ai_related/job_matching/README.md](/home/capstone/backend/ai_related/job_matching/README.md)

**Status:** Implemented

**Architecture:**

* **System 1 - Homepage ranking**
  * Stage 1: semantic search with `pgvector`
  * Stage 2: required skill-overlap filter
  * Stage 3: LightGBM re-ranking
* **System 2 - Job detail analysis**
  * RAG retrieval from freelancer, job, and contract context
  * LLM-based deep analysis for detailed match explanation

**Homepage ranking flow:**

```text
Freelancer profile
   ↓
Freelancer embedding
   ↓
Stage 1: pgvector cosine similarity search
   ↓
Top 100 candidate jobs
   ↓
Stage 2: required skill overlap filter
   ↓
Stage 3: LightGBM re-ranking
   ↓
Final ranked jobs on homepage
```

**Job detail analysis flow:**

```text
Selected job
   ↓
Retrieve job context
   ↓
Retrieve freelancer context
   ↓
Retrieve past contracts
   ↓
Fallback: use recent contracts if vector retrieval is unavailable
   ↓
Build grounded prompt
   ↓
LLM analysis
   ↓
Detailed match explanation
```

**Technical details:**

* **Stage 1 - Vector search**
  * Uses `freelancer_embedding` and `job_embedding`
  * Uses cosine similarity through `pgvector`
  * Returns the top candidate jobs before structured filtering
* **Stage 2 - Skill filter**
  * Checks freelancer skill coverage against required job skills
  * Drops jobs below the minimum required overlap threshold
  * Prevents semantically similar but operationally invalid matches
* **Stage 3 - LightGBM re-ranker**
  * Re-ranks filtered candidates using engineered features
  * Features include semantic similarity, skill overlap, experience, budget fit, portfolio/work history, and performance history
  * Produces final match probability for homepage ranking
* **Cold-start / low-confidence fallback**
  * Used when freelancer rating history is missing, or predicted probability is too low
  * Falls back to a heuristic score based on cosine similarity and skill overlap
  * Keeps new freelancers rankable without letting cold-start profiles dominate experienced freelancers
* **RAG analysis**
  * Retrieves job context, freelancer context, and relevant past contracts
  * Past contracts are primarily retrieved with vector similarity against the target job
  * If contract embeddings are not ready yet, retrieval falls back to recency-based ordering
  * The final output is generated from a grounded prompt instead of a static template

### 2. CV Analysis

**Owner:** Intan Kumala Pasya `[IKP]` (`tannpsy`)

**Status:** Planned / not yet implemented

**Planned architecture:**

* CV / resume text input
* RAG-based context retrieval
* OpenAI-based analysis

**Planned flow:**

```text
CV upload / CV text
   ↓
Context retrieval with RAG
   ↓
OpenAI processing
   ↓
CV analysis / recommendation output
```

**Notes:**

* Current documentation indicates this feature will use RAG with OpenAI
* Detailed pipeline, stages, and implementation flow are not yet documented in this repository

### 3. Rating System

**Owner:** Sarah Kimberly Fischer `[SKF]` (`sarahkimberlyy`)

**Status:** Planned / not yet implemented

**Planned architecture:**

* Freelancer / project rating data
* ML-related scoring or evaluation logic
* Performance signal for platform decisions

**Planned flow:**

```text
Ratings + project results
   ↓
ML / scoring process
   ↓
Freelancer performance score
   ↓
Support for ranking / recommendation
```

**Notes:**

* Current documentation indicates this feature is ML-related
* Detailed model design, feature pipeline, and implementation flow are not yet documented in this repository

---

## 🔗 Related Repositories

* Frontend: https://github.com/hwasyui/WorkByte-FRONTEND
* Database: https://github.com/hwasyui/WorkByte-DATABASE

---

## 👥 Team Members & Commit Codes

| Code  | Name           | GitHub                            |
| ----- | -------------- | --------------------------------- |
| [ASW] | hwasyui        | https://github.com/hwasyui        |
| [IKP] | tannpsy        | https://github.com/tannpsy        |
| [SKF] | sarahkimberlyy | https://github.com/sarahkimberlyy |

---

## 📝 Notes

* Use commit prefixes:

  * `[ASW]`, `[IKP]`, `[SKF]`
* API documentation available via `/docs`

---

## 📄 License

This project is for academic (capstone) purposes.
