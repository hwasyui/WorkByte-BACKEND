
============================================================
  Capstone API — Contract Walkthrough
============================================================
  Target : http://localhost:8000
  Output : /app/walkthrough/walkthrough_contract_results_20260415_162352.md

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
  job_fp_id (full_payment): 5b5f1a21-cbab-4495-bd3b-73a78b7ef2d6

============================================================
  Step 4: Client 1 — Add a role + required skills to the full_payment job
============================================================
  POST /job-roles  [201] OK
  role_fp_id: 40938970-a567-4815-a9f2-0c9713df4a88

============================================================
  Step 5: Client 2 — Create fresh job post for the milestone_based contract
============================================================
  POST /job-posts  [201] OK
  job_ms_id (milestone_based): c35256cd-5b02-46f7-95bf-0199e9508639

============================================================
  Step 6: Client 2 — Add a role to the milestone_based job
============================================================
  POST /job-roles  [201] OK
  role_ms_id: 578dc99c-8780-49d7-9dd1-8cee45487e6d

============================================================
  Step 7: Budi submits a proposal for the full_payment job
============================================================
  POST /proposals  [201] OK
  proposal_fp_id: 71941115-0d33-42cb-99f3-a00c2accb696

============================================================
  Step 8: Budi submits a proposal for the milestone_based job
============================================================
  POST /proposals  [201] OK
  proposal_ms_id: 3939fc99-ea6c-42b0-9014-480148e5257e

============================================================
  Step 9: Client 1 creates a FULL-PAYMENT contract
============================================================
  POST /contracts  [500] FAIL
  ERROR: {
    "status": "error",
    "details": "Failed to create contract: ContractFunctions.create_contract() got an unexpected keyword argument 'contract_id'"
}
