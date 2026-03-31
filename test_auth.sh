#!/bin/bash

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

BASE_URL="http://localhost:8000"
FREELANCER_EMAIL="test_freelancer@example.com"
FREELANCER_PASSWORD="TestPassword123"
CLIENT_EMAIL="test_client@example.com"
CLIENT_PASSWORD="TestPassword123"

echo -e "${YELLOW}=== CAPSTONE API Authentication Testing ===${NC}\n"

# Test 1: Register Freelancer
echo -e "${YELLOW}1. Registering Freelancer...${NC}"
FREELANCER_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$FREELANCER_EMAIL\",
    \"password\": \"$FREELANCER_PASSWORD\",
    \"user_type\": \"freelancer\",
    \"full_name\": \"John Developer\"
  }")

echo "$FREELANCER_RESPONSE" | grep -q "user_id" && \
  echo -e "${GREEN}✓ Freelancer registered${NC}" || \
  echo -e "${RED}✗ Failed to register freelancer${NC}"

# Test 2: Register Client
echo -e "${YELLOW}2. Registering Client...${NC}"
CLIENT_RESPONSE=$(curl -s -X POST "$BASE_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$CLIENT_EMAIL\",
    \"password\": \"$CLIENT_PASSWORD\",
    \"user_type\": \"client\",
    \"company_name\": \"Tech Company Inc\"
  }")

echo "$CLIENT_RESPONSE" | grep -q "user_id" && \
  echo -e "${GREEN}✓ Client registered${NC}" || \
  echo -e "${RED}✗ Failed to register client${NC}"

# Test 3: Login as Freelancer
echo -e "${YELLOW}3. Logging in as Freelancer...${NC}"
FREELANCER_LOGIN=$(curl -s -X POST "$BASE_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$FREELANCER_EMAIL\",
    \"password\": \"$FREELANCER_PASSWORD\"
  }")

FREELANCER_TOKEN=$(echo "$FREELANCER_LOGIN" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -n "$FREELANCER_TOKEN" ]; then
  echo -e "${GREEN}✓ Freelancer login successful${NC}"
  echo "  Token: ${FREELANCER_TOKEN:0:30}..."
else
  echo -e "${RED}✗ Freelancer login failed${NC}"
fi

# Test 4: Login as Client
echo -e "${YELLOW}4. Logging in as Client...${NC}"
CLIENT_LOGIN=$(curl -s -X POST "$BASE_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{
    \"email\": \"$CLIENT_EMAIL\",
    \"password\": \"$CLIENT_PASSWORD\"
  }")

CLIENT_TOKEN=$(echo "$CLIENT_LOGIN" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4)

if [ -n "$CLIENT_TOKEN" ]; then
  echo -e "${GREEN}✓ Client login successful${NC}"
  echo "  Token: ${CLIENT_TOKEN:0:30}..."
else
  echo -e "${RED}✗ Client login failed${NC}"
fi

# Test 5: Freelancer accessing /auth/me
echo -e "${YELLOW}5. Freelancer accessing /auth/me...${NC}"
if [ -n "$FREELANCER_TOKEN" ]; then
  ME_RESPONSE=$(curl -s -H "Authorization: Bearer $FREELANCER_TOKEN" "$BASE_URL/auth/me")
  echo "$ME_RESPONSE" | grep -q "freelancer" && \
    echo -e "${GREEN}✓ Freelancer can access /auth/me${NC}" || \
    echo -e "${RED}✗ Failed to access /auth/me${NC}"
fi

# Test 6: Freelancer trying to access /clients (should fail)
echo -e "${YELLOW}6. Freelancer trying to access /clients (should be denied)...${NC}"
if [ -n "$FREELANCER_TOKEN" ]; then
  CLIENTS_RESPONSE=$(curl -s -H "Authorization: Bearer $FREELANCER_TOKEN" "$BASE_URL/clients")
  echo "$CLIENTS_RESPONSE" | grep -q "Only clients can access" && \
    echo -e "${GREEN}✓ Freelancer correctly denied access to /clients${NC}" || \
    echo -e "${RED}✗ Unexpected response from /clients${NC}"
fi

# Test 7: Client trying to access /freelancers (should fail)
echo -e "${YELLOW}7. Client trying to access /freelancers (should be denied)...${NC}"
if [ -n "$CLIENT_TOKEN" ]; then
  FREELANCERS_RESPONSE=$(curl -s -H "Authorization: Bearer $CLIENT_TOKEN" "$BASE_URL/freelancers")
  echo "$FREELANCERS_RESPONSE" | grep -q "Only freelancers can access" && \
    echo -e "${GREEN}✓ Client correctly denied access to /freelancers${NC}" || \
    echo -e "${RED}✗ Unexpected response from /freelancers${NC}"
fi

# Test 8: Accessing route without token (should fail)
echo -e "${YELLOW}8. Accessing /freelancers without token (should be denied)...${NC}"
NO_AUTH_RESPONSE=$(curl -s "$BASE_URL/freelancers")
echo "$NO_AUTH_RESPONSE" | grep -q "Not authenticated" && \
  echo -e "${GREEN}✓ Correctly denied access without token${NC}" || \
  echo -e "${RED}✗ Unexpected response${NC}"

# Test 9: Accessing /skills with freelancer token (should succeed)
echo -e "${YELLOW}9. Freelancer accessing /skills (should succeed)...${NC}"
if [ -n "$FREELANCER_TOKEN" ]; then
  SKILLS_RESPONSE=$(curl -s -H "Authorization: Bearer $FREELANCER_TOKEN" "$BASE_URL/skills")
  echo "$SKILLS_RESPONSE" | grep -q "\[\]" || echo "$SKILLS_RESPONSE" | grep -q "skill" && \
    echo -e "${GREEN}✓ Freelancer can access /skills${NC}" || \
    echo -e "${RED}✗ Failed to access /skills${NC}"
fi

# Test 10: Get current user info
echo -e "${YELLOW}10. Retrieving current user info...${NC}"
if [ -n "$FREELANCER_TOKEN" ]; then
  CURRENT_USER=$(curl -s -H "Authorization: Bearer $FREELANCER_TOKEN" "$BASE_URL/auth/me")
  echo "$CURRENT_USER" | grep -q "$FREELANCER_EMAIL" && \
    echo -e "${GREEN}✓ Current user info retrieved${NC}" || \
    echo -e "${RED}✗ Failed to get user info${NC}"
  echo "  User: $CURRENT_USER"
fi

echo -e "\n${YELLOW}=== Testing Complete ===${NC}"
