
============================================================
  Capstone API — Contract Walkthrough
============================================================
  Target : http://localhost:8000
  Output : /app/walkthrough/walkthrough_contract_results_20260415_162659.md

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
  job_fp_id (full_payment): ac59a5f9-87a4-44be-be54-1ebdcdaf4fd7

============================================================
  Step 4: Client 1 — Add a role + required skills to the full_payment job
============================================================
  POST /job-roles  [201] OK
  role_fp_id: c9ad58e9-f8c0-45fc-9a51-35b00e901b2f

============================================================
  Step 5: Client 2 — Create fresh job post for the milestone_based contract
============================================================
  POST /job-posts  [201] OK
  job_ms_id (milestone_based): e2de4e44-39ca-4766-8049-1e68d3ad5ada

============================================================
  Step 6: Client 2 — Add a role to the milestone_based job
============================================================
  POST /job-roles  [201] OK
  role_ms_id: 43624d95-6530-4828-b184-231246c6cac1

============================================================
  Step 7: Budi submits a proposal for the full_payment job
============================================================
  POST /proposals  [201] OK
  proposal_fp_id: 7a51022c-be8e-4df1-af24-781647e2147a

============================================================
  Step 8: Budi submits a proposal for the milestone_based job
============================================================
  POST /proposals  [201] OK
  proposal_ms_id: 430c3cac-233c-47f3-8ed5-6289fd987057

============================================================
  Step 9: Client 1 creates a FULL-PAYMENT contract
============================================================
  POST /contracts  [201] OK
  contract_fp_id: 8007c6b2-04d0-447a-8cb3-a78d11dd5aa5
  (proposal auto-accepted, job post auto-filled)

============================================================
  Step 10: Client 2 creates a MILESTONE-BASED contract
============================================================
  POST /contracts  [201] OK
  contract_ms_id: 5998fcc4-4d3e-4f51-b65c-13db627c0bb9

============================================================
  Step 11: Inspect auto-filled generation data for the full_payment contract
============================================================
  GET  /contracts/8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/generation-data  [200] OK
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
  GET  /contracts/5998fcc4-4d3e-4f51-b65c-13db627c0bb9/generation-data  [200] OK
  contract_title     : Data Pipeline Build — DataCorp Solutions
  payment_structure  : milestone_based
  agreed_budget      : $4000.0 USD
  existing milestones: 0

============================================================
  Step 13: Generate full_payment contract PDF → upload to Supabase
============================================================
  Calling POST /contracts/{id}/generate ...
  POST /contracts/8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/generate  [200] OK
  PDF storage path : 8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/contract.pdf
  PDF generated at : 2026-04-15T09:27:01.252137

============================================================
  Step 14: Generate milestone_based contract PDF (3 milestones) → upload to Supabase
============================================================
  Calling POST /contracts/{id}/generate with milestones ...
  POST /contracts/5998fcc4-4d3e-4f51-b65c-13db627c0bb9/generate  [200] OK
  PDF storage path : 5998fcc4-4d3e-4f51-b65c-13db627c0bb9/contract.pdf
  PDF generated at : 2026-04-15T09:27:02.481817

============================================================
  Step 15: Get signed URL for the full_payment contract PDF
============================================================
  GET  /contracts/8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/pdf-url  [200] OK
  Signed URL (1 hr expiry):
    https://rsiiaiehqfwpdinhtmkc.supabase.co/storage/v1/object/sign/contract-assets/8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/con...
  → Open in a browser to verify the PDF in Supabase.

============================================================
  Step 16: Get signed URL for the milestone_based contract PDF
============================================================
  GET  /contracts/5998fcc4-4d3e-4f51-b65c-13db627c0bb9/pdf-url  [200] OK
  Signed URL (1 hr expiry):
    https://rsiiaiehqfwpdinhtmkc.supabase.co/storage/v1/object/sign/contract-assets/5998fcc4-4d3e-4f51-b65c-13db627c0bb9/con...
  → Open in a browser to verify the PDF in Supabase.

============================================================
  Step 17: List milestones created by the generate endpoint
============================================================
  GET  /contract-milestones/contract/5998fcc4-4d3e-4f51-b65c-13db627c0bb9  [200] OK
  3 milestone(s):
    [PENDING   ] Phase 1 — Ingestion Layer                  $?  due 2026-05-31
    [PENDING   ] Phase 2 — Transformation & Load            $?  due 2026-07-15
    [PENDING   ] Phase 3 — Testing & Handoff                $?  due 2026-08-31

============================================================
  Step 18: Client 2 moves Milestone 1 → in_progress
============================================================
  PUT  /contract-milestones/f5313764-c9db-43dc-9d18-b2be607e759a  [200] OK
  Milestone 1 is now in_progress (client_approved = True).

============================================================
  Step 19: Client 2 marks Milestone 1 → completed (work accepted)
============================================================
  PUT  /contract-milestones/f5313764-c9db-43dc-9d18-b2be607e759a  [200] OK
  Milestone 1 marked completed by client.

============================================================
  Step 20: Client 2 marks Milestone 1 → paid (releases payment)
============================================================
  PUT  /contract-milestones/f5313764-c9db-43dc-9d18-b2be607e759a  [200] OK
  Payment request sent to freelancer.

============================================================
  Step 21: Freelancer confirms receipt of Milestone 1 payment
============================================================
  POST /contract-milestones/f5313764-c9db-43dc-9d18-b2be607e759a/confirm-payment  [200] OK
  Freelancer confirmed payment — milestone fully settled.

============================================================
  Step 22: Verify final state of Milestone 1
============================================================
  GET  /contract-milestones/f5313764-c9db-43dc-9d18-b2be607e759a  [200] OK
  status                   : paid
  client_approved          : True
  payment_requested        : True
  freelancer_confirmed_paid: True
  payment_released         : True

============================================================
  Step 23: List all contracts visible to Budi
============================================================
  GET  /contracts  [200] OK
  8 contract(s):
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions        $4000.0 USD  (milestone_based)
    [ACTIVE    ] Backend API Developer — TechStartup Inc.        $3000.0 USD  (full_payment)
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions        $4000.0 USD  (milestone_based)
    [ACTIVE    ] Backend API Developer — TechStartup Inc.        $3000.0 USD  (full_payment)
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions        $4000.0 USD  (milestone_based)
    [ACTIVE    ] Backend API Developer — TechStartup Inc.        $3000.0 USD  (full_payment)
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions        $4000.0 USD  (milestone_based)
    [ACTIVE    ] Backend API Developer — TechStartup Inc.        $3000.0 USD  (full_payment)

============================================================
  Step 24: List all contracts visible to Client 1 (TechStartup Inc.)
============================================================
  GET  /contracts  [200] OK
  4 contract(s):
    [ACTIVE    ] Backend API Developer — TechStartup Inc.
    [ACTIVE    ] Backend API Developer — TechStartup Inc.
    [ACTIVE    ] Backend API Developer — TechStartup Inc.
    [ACTIVE    ] Backend API Developer — TechStartup Inc.

============================================================
  Step 25: List all contracts visible to Client 2 (DataCorp Solutions)
============================================================
  GET  /contracts  [200] OK
  4 contract(s):
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions
    [ACTIVE    ] Data Pipeline Build — DataCorp Solutions

============================================================
  Contract walkthrough complete.
============================================================

  Contracts created this run:
    full_payment   : 8007c6b2-04d0-447a-8cb3-a78d11dd5aa5
    milestone_based: 5998fcc4-4d3e-4f51-b65c-13db627c0bb9

  PDF verification (Supabase 'contract-assets' bucket):
    full_payment   path : 8007c6b2-04d0-447a-8cb3-a78d11dd5aa5/contract.pdf
    milestone_based path: 5998fcc4-4d3e-4f51-b65c-13db627c0bb9/contract.pdf

  Use the signed URLs printed above to download and inspect the PDFs.

