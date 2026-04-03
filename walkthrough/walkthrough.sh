#!/usr/bin/env bash

# Full API walkthrough script for WorkByte backend
# Generates a JSON report with request, response and scenario state.
# Usage:
#   chmod +x walkthrough/walkthrough.sh
#   ./walkthrough/walkthrough.sh

set -o pipefail

BASE_URL="http://localhost:8000"
OUTPUT_DIR="$(dirname "$0")"
OUTPUT_FILE="$OUTPUT_DIR/output.json"

# Count route modules to validate coverage
ROUTE_FILES_COUNT=$(find "$OUTPUT_DIR/../routes" -maxdepth 2 -name '*_routes.py' | wc -l | tr -d '[:space:]')
TOTAL_ROUTE_FILES=$((ROUTE_FILES_COUNT + 1)) # plus auth_router.py

echo "Detected $ROUTE_FILES_COUNT route modules under routes/ plus auth_router = $TOTAL_ROUTE_FILES total routes."

report_steps=()

timestamp() {
  date --utc +"%Y-%m-%dT%H:%M:%SZ"
}

run_step() {
  local name="$1"
  local method="$2"
  local endpoint="$3"
  local data="$4"
  local token="$5"

  local url="$BASE_URL$endpoint"
  local headers=("-H" "Content-Type: application/json")
  if [ -n "$token" ]; then
    headers+=("-H" "Authorization: Bearer $token")
  fi

  echo "=== STEP: $name ==="
  echo "URL: $url"
  echo "METHOD: $method"
  if [ -n "$data" ]; then
    echo "DATA: $data"
  fi

  local response
  if [ "$method" = "GET" ]; then
    response=$(curl -sS -w "%{http_code}" "${headers[@]}" "$url")
  else
    response=$(curl -sS -w "%{http_code}" "${headers[@]}" -X "$method" -d "$data" "$url")
  fi

  local status="${response: -3}"
  local body="${response:: -3}"

  echo "STATUS: $status"
  echo "BODY: $body"

  local normalized
  normalized=$(printf '%s' "$body" | jq -R -s -c 'fromjson? // .')
  report_steps+=("{\"name\": \"$name\", \"method\": \"$method\", \"endpoint\": \"$endpoint\", \"status\": $status, \"response\": $normalized, \"timestamp\": \"$(timestamp)\"}")

  echo
  echo "$body"
}

extract_id() {
  local payload="$1"
  local keys=("client_id" "freelancer_id" "user_id" "skill_id" "language_id" "speciality_id" "job_post_id" "job_role_id" "job_role_skill_id" "job_file_id" "proposal_id" "proposal_file_id" "contract_id" "milestone_id" "portfolio_id" "saved_job_id" "rating_id" "performance_rating_id" "client_trust_score_id" "embedding_id" "message_id")
  for key in "${keys[@]}"; do
    local value
    value=$(printf '%s' "$payload" | jq -r ".data.$key // .data.${key}_id // empty" 2>/dev/null)
    if [ -n "$value" ]; then
      echo "$value"
      return
    fi
  done
}

# 0) Global checks

echo "Route module count: $ROUTE_FILES_COUNT; including auth_router: $TOTAL_ROUTE_FILES"

# 1) Auth flows
invalid_login_resp=$(run_step "Invalid login attempt" "POST" "/auth/login" '{"email":"notfound@example.com","password":"badpass"}' "")

RANDOM_ID=$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')
TEST_CLIENT_EMAIL="test.client.${RANDOM_ID}@example.com"
TEST_FREELANCER_EMAIL="test.freelancer.${RANDOM_ID}@example.com"
TEST_PASSWORD="SecurePwd1!"

# client user registration and login
register_client_payload=$(cat <<EOF
{"email":"$TEST_CLIENT_EMAIL","password":"$TEST_PASSWORD","user_type":"client","full_name":"Test Client","company_name":"Test Client Co"}
EOF
)
register_client_resp=$(run_step "Register client user" "POST" "/auth/register" "$register_client_payload" "")

login_client_payload=$(cat <<EOF
{"email":"$TEST_CLIENT_EMAIL","password":"$TEST_PASSWORD"}
EOF
)
login_client_resp=$(run_step "Login client user" "POST" "/auth/login" "$login_client_payload" "")
CLIENT_TOKEN=$(printf '%s' "$login_client_resp" | jq -r '.data.access_token // empty')

# freelancer user registration and login
register_freelancer_payload=$(cat <<EOF
{"email":"$TEST_FREELANCER_EMAIL","password":"$TEST_PASSWORD","user_type":"freelancer","full_name":"Test Freelancer"}
EOF
)
register_freelancer_resp=$(run_step "Register freelancer user" "POST" "/auth/register" "$register_freelancer_payload" "")

login_freelancer_payload=$(cat <<EOF
{"email":"$TEST_FREELANCER_EMAIL","password":"$TEST_PASSWORD"}
EOF
)
login_freelancer_resp=$(run_step "Login freelancer user" "POST" "/auth/login" "$login_freelancer_payload" "")
FREELANCER_TOKEN=$(printf '%s' "$login_freelancer_resp" | jq -r '.data.access_token // empty')

me_client_resp=$(run_step "Client /auth/me" "GET" "/auth/me" "" "$CLIENT_TOKEN")
me_freelancer_resp=$(run_step "Freelancer /auth/me" "GET" "/auth/me" "" "$FREELANCER_TOKEN")

CLIENT_USER_ID=$(printf '%s' "$me_client_resp" | jq -r '.data.user_id // empty')
FREELANCER_USER_ID=$(printf '%s' "$me_freelancer_resp" | jq -r '.data.user_id // empty')

# 2) Clients flows
client_payload=$(cat <<EOF
{"user_id":"$CLIENT_USER_ID","full_name":"Test Client Full","bio":"Business bio","website_url":"https://example.com","profile_picture_url":"https://example.com/avatar.jpg"}
EOF
)
client_create_resp=$(run_step "Create client profile" "POST" "/clients" "$client_payload" "$CLIENT_TOKEN")
client_id=$(extract_id "$client_create_resp")
run_step "Get all clients" "GET" "/clients" "" "$CLIENT_TOKEN"
run_step "Search clients" "GET" "/clients/search/Test" "" "$CLIENT_TOKEN"
run_step "Get client not found" "GET" "/clients/not-found" "" "$CLIENT_TOKEN"
if [ -n "$client_id" ]; then
  run_step "Get client by id" "GET" "/clients/$client_id" "" "$CLIENT_TOKEN"
  run_step "Update client" "PUT" "/clients/$client_id" '{"bio":"Updated bio"}' "$CLIENT_TOKEN"
  run_step "Delete client" "DELETE" "/clients/$client_id" "" "$CLIENT_TOKEN"
fi

# 3) Freelancers flows
freelancer_payload=$(cat <<EOF
{"user_id":"$FREELANCER_USER_ID","full_name":"Test Freelancer Full","bio":"Developer bio"}
EOF
)
freelancer_create_resp=$(run_step "Create freelancer profile" "POST" "/freelancers" "$freelancer_payload" "$FREELANCER_TOKEN")
freelancer_id=$(extract_id "$freelancer_create_resp")
run_step "Get all freelancers" "GET" "/freelancers" "" "$FREELANCER_TOKEN"
run_step "Search freelancers" "GET" "/freelancers/search/Test" "" "$FREELANCER_TOKEN"
if [ -n "$freelancer_id" ]; then
  run_step "Get freelancer by id" "GET" "/freelancers/$freelancer_id" "" "$FREELANCER_TOKEN"
  run_step "Get freelancer embedding" "GET" "/freelancers/$freelancer_id/embedding" "" "$FREELANCER_TOKEN"
  run_step "Update freelancer" "PUT" "/freelancers/$freelancer_id" '{"bio":"Updated developer bio"}' "$FREELANCER_TOKEN"
  run_step "Delete freelancer" "DELETE" "/freelancers/$freelancer_id" "" "$FREELANCER_TOKEN"
fi

# 4) Users flows
user_payload=$(cat <<EOF
{"email":"test.user.${RANDOM_ID}@example.com","password":"$TEST_PASSWORD","type":"freelancer"}
EOF
)
user_create_resp=$(run_step "Create user record" "POST" "/users" "$user_payload" "")
user_id=$(extract_id "$user_create_resp")
run_step "Get all users" "GET" "/users" "" ""
run_step "Search users" "GET" "/users/search/test" "" ""
if [ -n "$user_id" ]; then
  run_step "Get user by id" "GET" "/users/$user_id" "" ""
  run_step "Update user" "PUT" "/users/$user_id" '{"type":"client"}' ""
  run_step "Delete user" "DELETE" "/users/$user_id" "" ""
fi

# 5) Skills flows
skill_payload=$(cat <<EOF
{"skill_name":"Bash scripting","skill_category":"tool","description":"Shell automation"}
EOF
)
skill_create_resp=$(run_step "Create skill" "POST" "/skills" "$skill_payload" "")
skill_id=$(extract_id "$skill_create_resp")
run_step "Get all skills" "GET" "/skills" "" ""
run_step "Search skills" "GET" "/skills/search/bash" "" ""
run_step "Get skills category" "GET" "/skills/category/tool" "" ""
if [ -n "$skill_id" ]; then
  run_step "Get skill by id" "GET" "/skills/$skill_id" "" ""
  run_step "Update skill" "PUT" "/skills/$skill_id" '{"description":"Updated description"}' ""
  run_step "Delete skill" "DELETE" "/skills/$skill_id" "" ""
fi

# 6) Specialities flows
speciality_payload=$(cat <<EOF
{"speciality_name":"Data Engineering","description":"ETL and pipelines"}
EOF
)
speciality_create_resp=$(run_step "Create speciality" "POST" "/specialities" "$speciality_payload" "")
speciality_id=$(extract_id "$speciality_create_resp")
run_step "Get all specialities" "GET" "/specialities" "" ""
run_step "Search specialities" "GET" "/specialities/search/data" "" ""
if [ -n "$speciality_id" ]; then
  run_step "Get speciality by id" "GET" "/specialities/$speciality_id" "" ""
  run_step "Update speciality" "PUT" "/specialities/$speciality_id" '{"description":"Updated data speciality"}' ""
  run_step "Delete speciality" "DELETE" "/specialities/$speciality_id" "" ""
fi

# 7) Languages flows
language_payload=$(cat <<EOF
{"language_name":"Python","iso_code":"py"}
EOF
)
language_create_resp=$(run_step "Create language" "POST" "/languages" "$language_payload" "")
language_id=$(extract_id "$language_create_resp")
run_step "Get all languages" "GET" "/languages" "" ""
run_step "Search languages" "GET" "/languages/search/python" "" ""
if [ -n "$language_id" ]; then
  run_step "Get language by id" "GET" "/languages/$language_id" "" ""
  run_step "Update language" "PUT" "/languages/$language_id" '{"iso_code":"pyt"}' ""
  run_step "Delete language" "DELETE" "/languages/$language_id" "" ""
fi

# 8) Freelancer skills flows
if [ -n "$freelancer_id" ] && [ -n "$skill_id" ]; then
  fs_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","skill_id":"$skill_id","proficiency_level":"expert"}
EOF
)
  fs_create_resp=$(run_step "Create freelancer skill" "POST" "/freelancer_skills" "$fs_payload" "")
  fs_id=$(extract_id "$fs_create_resp")

  run_step "Get all freelancer skills" "GET" "/freelancer_skills" "" ""
  run_step "Get freelancer skill by id" "GET" "/freelancer_skills/$fs_id" "" ""
  run_step "Get freelancer skills by freelancer" "GET" "/freelancer_skills/freelancer/$freelancer_id" "" ""
  if [ -n "$fs_id" ]; then
    run_step "Update freelancer skill" "PUT" "/freelancer_skills/$fs_id" '{"proficiency_level":"advanced"}' ""
    run_step "Delete freelancer skill" "DELETE" "/freelancer_skills/$fs_id" "" ""
  fi
fi

# 9) Freelancer specialities flows
if [ -n "$freelancer_id" ] && [ -n "$speciality_id" ]; then
  fsp_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","speciality_id":"$speciality_id","is_primary":true}
EOF
)
  fsp_create_resp=$(run_step "Create freelancer speciality" "POST" "/freelancer_specialities" "$fsp_payload" "")
  fsp_id=$(extract_id "$fsp_create_resp")

  run_step "Get all freelancer specialities" "GET" "/freelancer_specialities" "" ""
  run_step "Get freelancer speciality by id" "GET" "/freelancer_specialities/$fsp_id" "" ""
  run_step "Get freelancer specialities by freelancer" "GET" "/freelancer_specialities/freelancer/$freelancer_id" "" ""
  if [ -n "$fsp_id" ]; then
    run_step "Update freelancer speciality" "PUT" "/freelancer_specialities/$fsp_id" '{"is_primary":false}' ""
    run_step "Delete freelancer speciality" "DELETE" "/freelancer_specialities/$fsp_id" "" ""
    run_step "Delete freelancer speciality mapping" "DELETE" "/freelancer_specialities/freelancer/$freelancer_id/speciality/$speciality_id" "" ""
  fi
fi

# 10) Freelancer languages flows
if [ -n "$freelancer_id" ] && [ -n "$language_id" ]; then
  fl_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","language_id":"$language_id","proficiency_level":"native"}
EOF
)
  fl_create_resp=$(run_step "Create freelancer language" "POST" "/freelancer_languages" "$fl_payload" "")
  fl_id=$(extract_id "$fl_create_resp")

  run_step "Get all freelancer languages" "GET" "/freelancer_languages" "" ""
  run_step "Get freelancer language by id" "GET" "/freelancer_languages/$fl_id" "" ""
  run_step "Get freelancer languages by freelancer" "GET" "/freelancer_languages/freelancer/$freelancer_id" "" ""
  if [ -n "$fl_id" ]; then
    run_step "Update freelancer language" "PUT" "/freelancer_languages/$fl_id" '{"proficiency_level":"fluent"}' ""
    run_step "Delete freelancer language" "DELETE" "/freelancer_languages/$fl_id" "" ""
  fi
fi

# 11) Work experience flows
if [ -n "$freelancer_id" ]; then
  work_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","job_title":"Software Engineer","company_name":"ACME","location":"Remote","start_date":"2023-01-01","end_date":"2023-12-01","is_current":false,"description":"Developed features"}
EOF
)
  work_create_resp=$(run_step "Create work experience" "POST" "/work_experience" "$work_payload" "")
  work_id=$(extract_id "$work_create_resp")

  run_step "Get all work experiences" "GET" "/work_experience" "" ""
  run_step "Get work experience by id" "GET" "/work_experience/$work_id" "" ""
  run_step "Get work by freelancer" "GET" "/work_experience/freelancer/$freelancer_id" "" ""
  if [ -n "$work_id" ]; then
    run_step "Update work experience" "PUT" "/work_experience/$work_id" '{"job_title":"Sr Software Engineer"}' ""
    run_step "Delete work experience" "DELETE" "/work_experience/$work_id" "" ""
  fi
fi

# 12) Education flows
if [ -n "$freelancer_id" ]; then
  education_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","institution_name":"State University","degree":"BS Computer Science","start_date":"2018-08-01","end_date":"2022-06-01","is_current":false}
EOF
)
  educ_create_resp=$(run_step "Create education" "POST" "/education" "$education_payload" "")
  education_id=$(extract_id "$educ_create_resp")

  run_step "Get all education" "GET" "/education" "" ""
  run_step "Get education by id" "GET" "/education/$education_id" "" ""
  run_step "Get education by freelancer" "GET" "/education/freelancer/$freelancer_id" "" ""
  if [ -n "$education_id" ]; then
    run_step "Update education" "PUT" "/education/$education_id" '{"degree":"MS Computer Science"}' ""
    run_step "Delete education" "DELETE" "/education/$education_id" "" ""
  fi
fi

# 13) Job posts flows
if [ -n "$client_id" ]; then
  job_payload=$(cat <<EOF
{"client_id":"$client_id","job_title":"API Developer","job_description":"Build REST endpoints","project_type":"individual","project_scope":"small","estimated_duration":"30 days","experience_level":"intermediate","status":"active"}
EOF
)
  job_create_resp=$(run_step "Create job post" "POST" "/job_posts" "$job_payload" "")
  job_post_id=$(extract_id "$job_create_resp")

  run_step "Get all job posts" "GET" "/job_posts" "" ""
  run_step "Get job post by id" "GET" "/job_posts/$job_post_id" "" ""
  run_step "Get job posts by client" "GET" "/job_posts/client/$client_id" "" ""
  if [ -n "$job_post_id" ]; then
    run_step "Update job post" "PUT" "/job_posts/$job_post_id" '{"status":"closed"}' ""
    run_step "Delete job post" "DELETE" "/job_posts/$job_post_id" "" ""
  fi
fi

# 14) Job roles flows
if [ -n "$job_post_id" ]; then
  role_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","role_title":"Backend Engineer","budget_type":"fixed","role_description":"REST APIs","positions_available":1}
EOF
)
  role_create_resp=$(run_step "Create job role" "POST" "/job_roles" "$role_payload" "")
  job_role_id=$(extract_id "$role_create_resp")

  run_step "Get all job roles" "GET" "/job_roles" "" ""
  run_step "Get job role by id" "GET" "/job_roles/$job_role_id" "" ""
  run_step "Get job roles by job post" "GET" "/job_roles/job-post/$job_post_id" "" ""
  if [ -n "$job_role_id" ]; then
    run_step "Update job role" "PUT" "/job_roles/$job_role_id" '{"role_title":"Senior Backend Engineer"}' ""
    run_step "Delete job role" "DELETE" "/job_roles/$job_role_id" "" ""
  fi
fi

# 15) Job role skills flows
if [ -n "$job_role_id" ] && [ -n "$skill_id" ]; then
  jrs_payload=$(cat <<EOF
{"job_role_id":"$job_role_id","skill_id":"$skill_id","importance_level":"required"}
EOF
)
  jrs_create_resp=$(run_step "Create job role skill" "POST" "/job_role_skills" "$jrs_payload" "")
  jrs_id=$(extract_id "$jrs_create_resp")

  run_step "Get all job role skills" "GET" "/job_role_skills" "" ""
  run_step "Get job role skill by id" "GET" "/job_role_skills/$jrs_id" "" ""
  run_step "Get job role skills by role" "GET" "/job_role_skills/job-role/$job_role_id" "" ""
  if [ -n "$jrs_id" ]; then
    run_step "Update job role skill" "PUT" "/job_role_skills/$jrs_id" '{"importance_level":"preferred"}' ""
    run_step "Delete job role skill" "DELETE" "/job_role_skills/$jrs_id" "" ""
  fi
fi

# 16) Job files flows
if [ -n "$job_post_id" ]; then
  jf_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","file_url":"https://example.com/design.pdf","file_type":"pdf","file_name":"design.pdf"}
EOF
)
  jf_create_resp=$(run_step "Create job file" "POST" "/job_files" "$jf_payload" "")
  jf_id=$(extract_id "$jf_create_resp")

  run_step "Get all job files" "GET" "/job_files" "" ""
  run_step "Get job file by id" "GET" "/job_files/$jf_id" "" ""
  run_step "Get job files by job post" "GET" "/job_files/job-post/$job_post_id" "" ""
  if [ -n "$jf_id" ]; then
    run_step "Update job file" "PUT" "/job_files/$jf_id" '{"file_name":"design-updated.pdf"}' ""
    run_step "Delete job file" "DELETE" "/job_files/$jf_id" "" ""
  fi
fi

# 17) Proposals flows
if [ -n "$job_post_id" ] && [ -n "$freelancer_id" ]; then
  prop_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","freelancer_id":"$freelancer_id","cover_letter":"I can do this","proposed_budget":1500}
EOF
)
  prop_create_resp=$(run_step "Create proposal" "POST" "/proposals" "$prop_payload" "")
  proposal_id=$(extract_id "$prop_create_resp")

  run_step "Get all proposals" "GET" "/proposals" "" ""
  run_step "Get proposal by id" "GET" "/proposals/$proposal_id" "" ""
  run_step "Get proposals by job post" "GET" "/proposals/job-post/$job_post_id" "" ""
  run_step "Get proposals by freelancer" "GET" "/proposals/freelancer/$freelancer_id" "" ""
  if [ -n "$proposal_id" ]; then
    run_step "Update proposal" "PUT" "/proposals/$proposal_id" '{"status":"accepted"}' ""
    run_step "Delete proposal" "DELETE" "/proposals/$proposal_id" "" ""
  fi
fi

# 18) Proposal files flows
if [ -n "$proposal_id" ]; then
  pf_payload=$(cat <<EOF
{"proposal_id":"$proposal_id","file_url":"https://example.com/proposal.pdf","file_type":"pdf","file_name":"proposal.pdf"}
EOF
)
  pf_create_resp=$(run_step "Create proposal file" "POST" "/proposal_files" "$pf_payload" "")
  proposal_file_id=$(extract_id "$pf_create_resp")

  run_step "Get all proposal files" "GET" "/proposal_files" "" ""
  run_step "Get proposal file by id" "GET" "/proposal_files/$proposal_file_id" "" ""
  run_step "Get proposal files by proposal" "GET" "/proposal_files/proposal/$proposal_id" "" ""
  if [ -n "$proposal_file_id" ]; then
    run_step "Update proposal file" "PUT" "/proposal_files/$proposal_file_id" '{"file_name":"proposal-updated.pdf"}' ""
    run_step "Delete proposal file" "DELETE" "/proposal_files/$proposal_file_id" "" ""
  fi
fi

# 19) Contracts flows
if [ -n "$job_post_id" ] && [ -n "$job_role_id" ] && [ -n "$proposal_id" ] && [ -n "$freelancer_id" ] && [ -n "$client_id" ]; then
  contract_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","job_role_id":"$job_role_id","proposal_id":"$proposal_id","freelancer_id":"$freelancer_id","client_id":"$client_id","contract_title":"Fixed Rate Contract","agreed_budget":1500,"payment_structure":"full_payment","status":"active","start_date":"2024-01-01"}
EOF
)
  contract_create_resp=$(run_step "Create contract" "POST" "/contracts" "$contract_payload" "")
  contract_id=$(extract_id "$contract_create_resp")

  run_step "Get all contracts" "GET" "/contracts" "" ""
  run_step "Get contract by id" "GET" "/contracts/$contract_id" "" ""
  run_step "Get contracts by freelancer" "GET" "/contracts/freelancer/$freelancer_id" "" ""
  run_step "Get contracts by client" "GET" "/contracts/client/$client_id" "" ""
  if [ -n "$contract_id" ]; then
    run_step "Update contract" "PUT" "/contracts/$contract_id" '{"status":"completed"}' ""
    run_step "Delete contract" "DELETE" "/contracts/$contract_id" "" ""
  fi
fi

# 20) Contract milestones flows
if [ -n "$contract_id" ]; then
  milestone_payload=$(cat <<EOF
{"contract_id":"$contract_id","milestone_title":"Phase 1","milestone_budget":500}
EOF
)
  milestone_create_resp=$(run_step "Create contract milestone" "POST" "/contract_milestones" "$milestone_payload" "")
  milestone_id=$(extract_id "$milestone_create_resp")

  run_step "Get all contract milestones" "GET" "/contract_milestones" "" ""
  run_step "Get milestone by id" "GET" "/contract_milestones/$milestone_id" "" ""
  run_step "Get milestones by contract" "GET" "/contract_milestones/contract/$contract_id" "" ""
  if [ -n "$milestone_id" ]; then
    run_step "Update milestone" "PUT" "/contract_milestones/$milestone_id" '{"status":"completed"}' ""
    run_step "Delete milestone" "DELETE" "/contract_milestones/$milestone_id" "" ""
  fi
fi

# 21) Portfolio flows
if [ -n "$freelancer_id" ] && [ -n "$contract_id" ]; then
  portfolio_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","contract_id":"$contract_id","project_title":"API Project","project_description":"Demo project"}
EOF
)
  portfolio_create_resp=$(run_step "Create portfolio item" "POST" "/portfolio" "$portfolio_payload" "")
  portfolio_id=$(extract_id "$portfolio_create_resp")

  run_step "Get all portfolio entries" "GET" "/portfolio" "" ""
  run_step "Get portfolio by id" "GET" "/portfolio/$portfolio_id" "" ""
  run_step "Get portfolio by freelancer" "GET" "/portfolio/freelancer/$freelancer_id" "" ""
  if [ -n "$portfolio_id" ]; then
    run_step "Update portfolio" "PUT" "/portfolio/$portfolio_id" '{"project_title":"Updated project"}' ""
    run_step "Delete portfolio" "DELETE" "/portfolio/$portfolio_id" "" ""
  fi
fi

# 22) Saved jobs flows
if [ -n "$job_post_id" ] && [ -n "$freelancer_id" ]; then
  saved_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","freelancer_id":"$freelancer_id"}
EOF
)
  saved_create_resp=$(run_step "Create saved job" "POST" "/saved_jobs" "$saved_payload" "")
  saved_id=$(extract_id "$saved_create_resp")

  run_step "Get all saved jobs" "GET" "/saved_jobs" "" ""
  run_step "Get saved job by id" "GET" "/saved_jobs/$saved_id" "" ""
  run_step "Get saved jobs by freelancer" "GET" "/saved_jobs/freelancer/$freelancer_id" "" ""
  if [ -n "$saved_id" ]; then
    run_step "Delete saved job" "DELETE" "/saved_jobs/$saved_id" "" ""
  fi
fi

# 23) Ratings flows
if [ -n "$contract_id" ] && [ -n "$freelancer_id" ] && [ -n "$client_id" ]; then
  rating_payload=$(cat <<EOF
{"contract_id":"$contract_id","rater_id":"$freelancer_id","ratee_id":"$client_id","rating_score":4.8}
EOF
)
  rating_create_resp=$(run_step "Create rating" "POST" "/ratings" "$rating_payload" "")
  rating_id=$(extract_id "$rating_create_resp")

  run_step "Get all ratings" "GET" "/ratings" "" ""
  run_step "Get rating by id" "GET" "/ratings/$rating_id" "" ""
  run_step "Get ratings by freelancer" "GET" "/ratings/freelancer/$freelancer_id" "" ""
  run_step "Get ratings by client" "GET" "/ratings/client/$client_id" "" ""
  if [ -n "$rating_id" ]; then
    run_step "Update rating" "PUT" "/ratings/$rating_id" '{"rating_score":5.0}' ""
    run_step "Delete rating" "DELETE" "/ratings/$rating_id" "" ""
  fi
fi

# 24) Performance ratings flows
if [ -n "$freelancer_id" ]; then
  perf_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","total_contracts":3,"completed_contracts":2,"average_rating":4.7}
EOF
)
  perf_create_resp=$(run_step "Create performance rating" "POST" "/performance_ratings" "$perf_payload" "")
  perf_id=$(extract_id "$perf_create_resp")

  run_step "Get all performance ratings" "GET" "/performance_ratings" "" ""
  run_step "Get performance rating by freelancer" "GET" "/performance_ratings/freelancer/$freelancer_id" "" ""
  if [ -n "$per_five_id" ]; then
    run_step "Update performance rating" "PUT" "/performance_ratings/freelancer/$freelancer_id" '{"total_contracts":4}' ""
    run_step "Delete performance rating" "DELETE" "/performance_ratings/freelancer/$freelancer_id" "" ""
  fi
fi

# 25) Client trust scores flows
if [ -n "$client_id" ]; then
  trust_payload=$(cat <<EOF
{"client_id":"$client_id","total_jobs_posted":5,"total_jobs_completed":4,"trust_score":92.5}
EOF
)
  trust_create_resp=$(run_step "Create client trust score" "POST" "/client_trust_scores" "$trust_payload" "")
  trust_id=$(extract_id "$trust_create_resp")

  run_step "Get all client trust scores" "GET" "/client_trust_scores" "" ""
  run_step "Get client trust score by client" "GET" "/client_trust_scores/$client_id" "" ""
  if [ -n "$trust_id" ]; then
    run_step "Update client trust score" "PUT" "/client_trust_scores/$client_id" '{"trust_score":95.2}' ""
    run_step "Delete client trust score" "DELETE" "/client_trust_scores/$client_id" "" ""
  fi
fi

# 26) Freelancer embeddings flows
if [ -n "$freelancer_id" ]; then
  fem_payload=$(cat <<EOF
{"freelancer_id":"$freelancer_id","embedding_vector":[0.1,0.2,0.3],"embedding_type":"profile_based"}
EOF
)
  fem_create_resp=$(run_step "Create freelancer embedding" "POST" "/freelancer_embeddings" "$fem_payload" "")
  fem_id=$(extract_id "$fem_create_resp")

  run_step "Get all freelancer embeddings" "GET" "/freelancer_embeddings" "" ""
  run_step "Get freelancer embedding by id" "GET" "/freelancer_embeddings/$fem_id" "" ""
  run_step "Get freelancer embedding by freelancer" "GET" "/freelancer_embeddings/freelancer/$freelancer_id" "" ""
  if [ -n "$fem_id" ]; then
    run_step "Update freelancer embedding" "PUT" "/freelancer_embeddings/$fem_id" '{"embedding_vector":[0.9,0.8,0.7]}' ""
    run_step "Delete freelancer embedding" "DELETE" "/freelancer_embeddings/$fem_id" "" ""
  fi
fi

# 27) Job embeddings flows
if [ -n "$job_post_id" ]; then
  jemb_payload=$(cat <<EOF
{"job_post_id":"$job_post_id","embedding_vector":[0.2,0.4,0.6],"embedding_type":"description_based"}
EOF
)
  jemb_create_resp=$(run_step "Create job embedding" "POST" "/job_embeddings" "$jemb_payload" "")
  jemb_id=$(extract_id "$jemb_create_resp")

  run_step "Get all job embeddings" "GET" "/job_embeddings" "" ""
  run_step "Get job embedding by id" "GET" "/job_embeddings/$jemb_id" "" ""
  run_step "Get job embedding by job post" "GET" "/job_embeddings/job-post/$job_post_id" "" ""
  if [ -n "$jemb_id" ]; then
    run_step "Update job embedding" "PUT" "/job_embeddings/$jemb_id" '{"embedding_vector":[0.3,0.6,0.9]}' ""
    run_step "Delete job embedding" "DELETE" "/job_embeddings/$jemb_id" "" ""
  fi
fi

# 28) Messages flows
if [ -n "$contract_id" ] && [ -n "$client_id" ] && [ -n "$freelancer_id" ]; then
  msg_payload=$(cat <<EOF
{"contract_id":"$contract_id","sender_id":"$client_id","receiver_id":"$freelancer_id","message_text":"Hello, starting work"}
EOF
)
  msg_create_resp=$(run_step "Create message" "POST" "/messages" "$msg_payload" "")
  msg_id=$(extract_id "$msg_create_resp")

  run_step "Get all messages" "GET" "/messages" "" ""
  run_step "Get message by id" "GET" "/messages/$msg_id" "" ""
  run_step "Get messages by sender" "GET" "/messages/sender/$client_id" "" ""
  run_step "Get messages by receiver" "GET" "/messages/receiver/$freelancer_id" "" ""
  run_step "Get messages by contract" "GET" "/messages/contract/$contract_id" "" ""
  if [ -n "$msg_id" ]; then
    run_step "Update message" "PUT" "/messages/$msg_id" '{"message_text":"Updated message","is_read":true}' ""
    run_step "Delete message" "DELETE" "/messages/$msg_id" "" ""
  fi
fi

# 29) unauthorized checks
run_step "Unauthorized clients fetch" "GET" "/clients" "" ""
run_step "Unauthorized job posts fetch" "GET" "/job_posts" "" ""

# Save JSON results
printf '{"route_module_count": %s, "total_routes": %s, "steps": [%s]}' "$ROUTE_FILES_COUNT" "$TOTAL_ROUTE_FILES" "$(printf '%s\n' "${report_steps[@]}" | paste -sd, -)" > "$OUTPUT_FILE"

echo "Walkthrough finished. Output is in $OUTPUT_FILE"
