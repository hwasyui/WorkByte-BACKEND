
============================================================
  Capstone API — Contract Walkthrough
============================================================
  Target : http://localhost:8000
  Output : /app/walkthrough/walkthrough_contract_results_20260415_162500.md

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
  job_fp_id (full_payment): 1a5bd03e-22b4-487d-ba26-e86702a57f9d

============================================================
  Step 4: Client 1 — Add a role + required skills to the full_payment job
============================================================
  POST /job-roles  [201] OK
  role_fp_id: 91629a75-37c7-4537-be47-fc4488c0d71b

============================================================
  Step 5: Client 2 — Create fresh job post for the milestone_based contract
============================================================
  POST /job-posts  [201] OK
  job_ms_id (milestone_based): 09d57afe-bb1e-48ce-be45-c49ab8595f69

============================================================
  Step 6: Client 2 — Add a role to the milestone_based job
============================================================
  POST /job-roles  [201] OK
  role_ms_id: 030d9255-1dbd-4c5a-964c-27864b641a22

============================================================
  Step 7: Budi submits a proposal for the full_payment job
============================================================
  POST /proposals  [201] OK
  proposal_fp_id: 202a6e00-2a2c-4fc9-af90-63562be5d622

============================================================
  Step 8: Budi submits a proposal for the milestone_based job
============================================================
  POST /proposals  [201] OK
  proposal_ms_id: 531eb577-1424-4605-9825-bd313a3177da

============================================================
  Step 9: Client 1 creates a FULL-PAYMENT contract
============================================================
  POST /contracts  [201] OK
  contract_fp_id: 7fa7f19e-f1f7-41d6-8391-f15595ee76e5
  (proposal auto-accepted, job post auto-filled)

============================================================
  Step 10: Client 2 creates a MILESTONE-BASED contract
============================================================
  POST /contracts  [201] OK
  contract_ms_id: d08fdaf6-4b53-4ea1-a558-e09761bc2e19

============================================================
  Step 11: Inspect auto-filled generation data for the full_payment contract
============================================================
  GET  /contracts/7fa7f19e-f1f7-41d6-8391-f15595ee76e5/generation-data  [200] OK
  contract_title     : Backend API Developer — TechStartup Inc.
  payment_structure  : full_payment
  agreed_budget      : $3000.0 USD
  freelancer name    : Budi Santoso
  client name        : TechStartup Inc.
  job post title     : Backend API Developer (Contract Run)
  existing terms     : False
  existing milestones: 0

============================================================
  Step 12: Inspect auto-filled generation data for the milestone_based contract
============================================================
  GET  /contracts/d08fdaf6-4b53-4ea1-a558-e09761bc2e19/generation-data  [200] OK
  contract_title     : Data Pipeline Build — DataCorp Solutions
  payment_structure  : milestone_based
  agreed_budget      : $4000.0 USD
  existing milestones: 0

============================================================
  Step 13: Generate full_payment contract PDF → upload to Supabase
============================================================
  Calling POST /contracts/{id}/generate ...
  POST /contracts/7fa7f19e-f1f7-41d6-8391-f15595ee76e5/generate  [500] FAIL
  ERROR: {
    "status": "error",
    "details": "Failed to generate contract PDF for 7fa7f19e-f1f7-41d6-8391-f15595ee76e5: SyncBucketActionsMixin.upload() got an unexpected keyword argument 'content_type'"
}
