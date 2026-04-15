
============================================================
  Capstone API — Full Walkthrough
============================================================
  Target: http://localhost:8000
  Output: /app/walkthrough/walkthrough_results_20260414_131734.md

============================================================
  Step 1: Register freelancer (Budi Santoso)
============================================================
  POST /auth/register  [201] OK

============================================================
  Step 2: Register client 1 (TechStartup Inc.)
============================================================
  POST /auth/register  [201] OK

============================================================
  Step 3: Register client 2 (DataCorp Solutions)
============================================================
  POST /auth/register  [201] OK

============================================================
  Step 4: Log in all three users and grab tokens
============================================================
  POST /auth/login  [201] OK
  POST /auth/login  [201] OK
  POST /auth/login  [201] OK
  All tokens obtained.

============================================================
  Step 5: Fetch freelancer and client profile IDs
============================================================
  GET  /freelancers  [200] OK
  GET  /clients  [200] OK
  GET  /clients  [200] OK
  freelancer_id : a89dbe78-f59b-461f-9639-9babdac1f539
  client1_id    : 4e2e0f92-3625-4d32-887a-18db6b98ef61
  client2_id    : eb7f6e03-df1a-4b7e-96ca-79ce810279f9

============================================================
  Step 6: Fill in freelancer profile — rate, bio
============================================================
  PUT  /freelancers/a89dbe78-f59b-461f-9639-9babdac1f539  [200] OK

============================================================
  Step 7: Create skills (shared across freelancer + jobs)
============================================================
  POST /skills  [201] OK
    Python → e4acac8e-07ea-4d9b-be51-ea10dd6ded07
  POST /skills  [201] OK
    PostgreSQL → 7a072f99-fbcc-4d0f-8eba-ab0462d7b284
  POST /skills  [201] OK
    REST API → 33e3b78f-6fdf-4bdc-a435-bd431ca51f06
  POST /skills  [201] OK
    Docker → b6e850b6-1078-4142-aa4c-7012dacbcf19
  POST /skills  [201] OK
    Redis → 43f38f79-8db4-4f57-bc8e-c047dd5420ee
  POST /skills  [201] OK
    React → dfd397fb-9a62-4a45-811d-d26d2de36997
  POST /skills  [201] OK
    Apache Spark → 5c6ece7e-b5e4-40ac-be8a-ff282d2adb74
  POST /skills  [201] OK
    Kubernetes → 0f80b866-bca6-4e50-a525-8110e5a707f0
  POST /skills  [201] OK
    AWS → 2fea3450-4bd7-4f88-8ce0-7005a8287974
  POST /skills  [201] OK
    FastAPI → 31e410bf-60e3-4275-aad2-8f2346975093
  POST /skills  [201] OK
    Data Modeling → a28065f7-03c5-41fc-b704-fa33f7897032
  POST /skills  [201] OK
    Git → f3ed1e96-deca-440d-b492-e32b8dfc3aaf

============================================================
  Step 8: Create specialities
============================================================
  POST /specialities  [201] OK
    Backend Development → a5fdbc86-348e-4255-ac15-2ad0b52ad387
  POST /specialities  [201] OK
    Data Engineering → fc23ee67-e53e-4400-bd86-797dca7433d8
  POST /specialities  [201] OK
    DevOps → 26e0a019-334a-48fa-9b44-57eae5e3eaf7

============================================================
  Step 9: Create languages
============================================================
  POST /languages  [201] OK
    English → fd970788-30c9-415e-89ff-0391ffb2dc52
  POST /languages  [201] OK
    Indonesian → 8ef5c1b0-ade8-4247-a41d-d3618019cba3

============================================================
  Step 10: Assign skills to freelancer
============================================================
  POST /freelancer-skills  [201] OK
    Python (advanced)
  POST /freelancer-skills  [201] OK
    PostgreSQL (advanced)
  POST /freelancer-skills  [201] OK
    REST API (advanced)
  POST /freelancer-skills  [201] OK
    FastAPI (intermediate)
  POST /freelancer-skills  [201] OK
    Docker (intermediate)
  POST /freelancer-skills  [201] OK
    Redis (beginner)
  POST /freelancer-skills  [201] OK
    Git (advanced)
  POST /freelancer-skills  [201] OK
    Data Modeling (intermediate)

============================================================
  Step 11: Assign speciality to freelancer
============================================================
  POST /freelancer-specialities  [201] OK

============================================================
  Step 12: Assign languages to freelancer
============================================================
  POST /freelancer-languages  [201] OK
  POST /freelancer-languages  [201] OK

============================================================
  Step 13: Add work experience (2 past roles — boosts embedding quality)
============================================================
  POST /work-experiences  [201] OK
  POST /work-experiences  [201] OK

============================================================
  Step 14: Add portfolio items
============================================================
  POST /portfolios  [201] OK
  POST /portfolios  [201] OK

============================================================
  Step 15: Client 1 — Create job post: Backend API Developer (strong match)
============================================================
  POST /job-posts  [201] OK
  job1_id (Backend API Developer): 75a0c1fe-2ae4-4b63-b463-c75ca7fb8064

============================================================
  Step 16: Client 1 — Create job post: Full Stack Engineer (partial match — needs React)
============================================================
  POST /job-posts  [201] OK
  job2_id (Full Stack Engineer): 8975e614-1e4a-4a89-8a0f-3f5f57257aba

============================================================
  Step 17: Client 2 — Create job post: Data Engineer (partial match — needs Spark)
============================================================
  POST /job-posts  [201] OK
  job3_id (Data Engineer): c4b32954-1f38-4042-bf2a-c75840518c1a

============================================================
  Step 18: Client 2 — Create job post: DevOps Engineer (poor match — mainly infra)
============================================================
  POST /job-posts  [201] OK
  job4_id (DevOps): e530ebd8-fb31-453a-a350-35eda8ffb055

============================================================
  Step 19: Add roles and skills to Job 1 — Backend API Developer
============================================================
  POST /job-roles  [201] OK
  POST /job-role-skills  [201] OK
    Python (required)
  POST /job-role-skills  [201] OK
    FastAPI (required)
  POST /job-role-skills  [201] OK
    PostgreSQL (required)
  POST /job-role-skills  [201] OK
    REST API (required)
  POST /job-role-skills  [201] OK
    Redis (preferred)
  POST /job-role-skills  [201] OK
    Docker (preferred)

============================================================
  Step 20: Add roles and skills to Job 2 — Full Stack Engineer
============================================================
  POST /job-roles  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK

============================================================
  Step 21: Add roles and skills to Job 3 — Data Engineer
============================================================
  POST /job-roles  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK

============================================================
  Step 22: Add roles and skills to Job 4 — DevOps Engineer
============================================================
  POST /job-roles  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK
  POST /job-role-skills  [201] OK

============================================================
  Step 23: Set all job posts to 'active' — Stage 1 only matches active jobs
============================================================
  PUT  /job-posts/75a0c1fe-2ae4-4b63-b463-c75ca7fb8064  [200] OK
    'Backend API Developer' → active
  PUT  /job-posts/8975e614-1e4a-4a89-8a0f-3f5f57257aba  [200] OK
    'Full Stack Engineer' → active
  PUT  /job-posts/c4b32954-1f38-4042-bf2a-c75840518c1a  [200] OK
    'Data Engineer' → active
  PUT  /job-posts/e530ebd8-fb31-453a-a350-35eda8ffb055  [200] OK
    'DevOps Engineer' → active

============================================================
  Step 24: Create 2 historical job posts (already-finished work, status=closed)
============================================================
  POST /job-posts  [201] OK
  POST /job-posts  [201] OK
  hist_job1_id: 4b132b74-7427-404d-a0f0-f4d71efdbe49
  hist_job2_id: 4da9234c-dae1-440c-91a4-369c6118d78d

============================================================
  Step 25: Create job roles for historical job posts
============================================================
  POST /job-roles  [201] OK
  POST /job-roles  [201] OK

============================================================
  Step 26: Budi submits proposals for historical jobs (pre-accepted)
============================================================
  POST /proposals  [201] OK
  POST /proposals  [201] OK

============================================================
  Step 27: Create historical contracts (already active, about to be completed)
============================================================
  POST /contracts  [400] FAIL
  ERROR: {
    "status": "error",
    "details": "Job post is no longer open for contract creation"
}
