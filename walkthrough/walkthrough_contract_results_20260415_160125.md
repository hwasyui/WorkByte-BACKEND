
============================================================
  Capstone API — Contract Walkthrough
============================================================
  Target : http://localhost:8000
  Output : /app/walkthrough/walkthrough_contract_results_20260415_160125.md

  Prerequisite: walkthrough.py must have been run at least once
  so that Budi Santoso, TechStartup Inc., and DataCorp Solutions exist.

============================================================
  Step 1: Log in as existing walkthrough users
============================================================
  POST /auth/login  [201] OK
  POST /auth/login  [201] OK
  POST /auth/login  [201] OK
  All tokens obtained.

============================================================
  Step 2: Resolve freelancer and client profile IDs
============================================================
  GET  /freelancers  [200] OK
  GET  /clients  [200] OK
  GET  /clients  [200] OK
  freelancer_id : a89dbe78-f59b-461f-9639-9babdac1f539
  client1_id    : 4e2e0f92-3625-4d32-887a-18db6b98ef61
  client2_id    : eb7f6e03-df1a-4b7e-96ca-79ce810279f9

============================================================
  Step 3: Client 1 — Create fresh job post for the full_payment contract
============================================================
  POST /job-posts  [201] OK
  job_fp_id (full_payment): e3fdaae7-3a38-4a01-a71d-025e3272011c

============================================================
  Step 4: Client 1 — Add a role + required skills to the full_payment job
============================================================
  POST /job-roles  [201] OK
  role_fp_id: 8a24623f-ec3b-4146-81ec-7c95e2b7d3e9

============================================================
  Step 5: Client 2 — Create fresh job post for the milestone_based contract
============================================================
  POST /job-posts  [201] OK
  job_ms_id (milestone_based): 89fcd464-918d-46fa-9236-842dc2b0bcca

============================================================
  Step 6: Client 2 — Add a role to the milestone_based job
============================================================
  POST /job-roles  [201] OK
  role_ms_id: 6d2ab4f5-c02e-474f-8aba-ef4d5f05c9a5

============================================================
  Step 7: Budi submits a proposal for the full_payment job
============================================================
  POST /proposals  [201] OK
  proposal_fp_id: 3d4f8d1c-f7f3-401e-a7ba-6f473b8bc4fb

============================================================
  Step 8: Budi submits a proposal for the milestone_based job
============================================================
  POST /proposals  [201] OK
  proposal_ms_id: 9e501dd6-ee43-49be-9e82-b8bc3303f072

============================================================
  Step 9: Client 1 creates a FULL-PAYMENT contract
============================================================
  POST /contracts  [201] OK
  contract_fp_id: 441c608f-2ddf-4d90-a3c8-cc583a29ef84
  (proposal auto-accepted, job post auto-filled)

============================================================
  Step 10: Client 2 creates a MILESTONE-BASED contract
============================================================
  POST /contracts  [201] OK
  contract_ms_id: 3d4ec33c-adfd-4e79-ba7c-1bf54b03c925

============================================================
  Step 11: Inspect auto-filled generation data for the full_payment contract
============================================================
  GET  /contracts/441c608f-2ddf-4d90-a3c8-cc583a29ef84/generation-data  [404] FAIL
  ERROR: {
    "detail": "Not Found"
}
