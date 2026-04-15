
============================================================
  Capstone API — Contract Walkthrough
============================================================
  Target : http://localhost:8000
  Output : /app/walkthrough/walkthrough_contract_results_20260415_162551.md

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
  job_fp_id (full_payment): d6dd3e6b-6758-4092-8f38-dfc7eba6b5af

============================================================
  Step 4: Client 1 — Add a role + required skills to the full_payment job
============================================================
  POST /job-roles  [201] OK
  role_fp_id: ba615ea5-58a5-425b-a1a0-b371db2b5147

============================================================
  Step 5: Client 2 — Create fresh job post for the milestone_based contract
============================================================
  POST /job-posts  [201] OK
  job_ms_id (milestone_based): 74d82130-bbd1-4833-a83a-b7e3a1941bd7

============================================================
  Step 6: Client 2 — Add a role to the milestone_based job
============================================================
  POST /job-roles  [201] OK
  role_ms_id: 9af6d64e-b8ac-4c2d-9566-a18753c1f7b1

============================================================
  Step 7: Budi submits a proposal for the full_payment job
============================================================
  POST /proposals  [201] OK
  proposal_fp_id: c8884120-0ec4-43f7-a112-6e59623245c2

============================================================
  Step 8: Budi submits a proposal for the milestone_based job
============================================================
  POST /proposals  [201] OK
  proposal_ms_id: e68f8d3b-f50d-4b82-bde9-f2fc1e6f5055

============================================================
  Step 9: Client 1 creates a FULL-PAYMENT contract
============================================================
  POST /contracts  [201] OK
  contract_fp_id: 01afe88a-e55f-4728-a3c3-462080201e76
  (proposal auto-accepted, job post auto-filled)

============================================================
  Step 10: Client 2 creates a MILESTONE-BASED contract
============================================================
  POST /contracts  [201] OK
  contract_ms_id: 5eb7f529-8ab0-46d8-8315-e35de5f64957

============================================================
  Step 11: Inspect auto-filled generation data for the full_payment contract
============================================================
  GET  /contracts/01afe88a-e55f-4728-a3c3-462080201e76/generation-data  [200] OK
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
  GET  /contracts/5eb7f529-8ab0-46d8-8315-e35de5f64957/generation-data  [200] OK
  contract_title     : Data Pipeline Build — DataCorp Solutions
  payment_structure  : milestone_based
  agreed_budget      : $4000.0 USD
  existing milestones: 0

============================================================
  Step 13: Generate full_payment contract PDF → upload to Supabase
============================================================
  Calling POST /contracts/{id}/generate ...
  POST /contracts/01afe88a-e55f-4728-a3c3-462080201e76/generate  [200] OK
  PDF storage path : 01afe88a-e55f-4728-a3c3-462080201e76/contract.pdf
  PDF generated at : 2026-04-15T09:25:53.064972

============================================================
  Step 14: Generate milestone_based contract PDF (3 milestones) → upload to Supabase
============================================================
  Calling POST /contracts/{id}/generate with milestones ...
  POST /contracts/5eb7f529-8ab0-46d8-8315-e35de5f64957/generate  [500] FAIL
  ERROR: {
    "status": "error",
    "details": "Failed to generate contract PDF for 5eb7f529-8ab0-46d8-8315-e35de5f64957: ContractMilestoneFunctions.create_contract_milestone() got an unexpected keyword argument 'description'"
}
