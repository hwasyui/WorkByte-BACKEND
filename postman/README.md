# Postman Collection Documentation

## Overview
This folder contains a complete Postman collection for the Capstone API with all endpoints, request/response examples, and proper authentication setup.

## Files Included

### 1. `Capstone_API.postman_collection.json`
The main API collection with all endpoints organized by resource:
- **Authentication** - Register, Login, Get Current User
- **Users** - CRUD operations and search
- **Clients** - CRUD operations and search
- **Freelancers** - CRUD operations, search, and embedding management
- **Skills** - CRUD operations, category filtering, and search
- **Specialities** - CRUD operations and search
- **Languages** - CRUD operations and search

### 2. `Capstone_API.postman_environment.json`
Environment file with pre-configured variables for easy API testing

## How to Import

### Step 1: Import the Collection
1. Open Postman
2. Click **"Import"** button (top-left)
3. Go to **"File"** tab
4. Upload `Capstone_API.postman_collection.json`
5. Click **"Import"**

### Step 2: Import the Environment
1. Click the **gear icon** (Settings) in the top-right
2. Go to **"Environments"**
3. Click **"Import"**
4. Upload `Capstone_API.postman_environment.json`
5. Click **"Import"**

### Step 3: Select the Environment
1. Look for the dropdown in the top-right corner (currently says "No Environment")
2. Select **"Capstone API Environment"**

## Quick Start Guide

### 1. Register a New User
```
POST /auth/register
JSON Body:
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "user_type": "freelancer",
  "full_name": "John Doe",
  "company_name": null
}
```
✅ Copy the `user_id` from the response for later use

### 2. Login to Get Token
```
POST /auth/login
JSON Body:
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```
✅ Copy the `access_token` value  
✅ Go to **Environment** → Select your environment → Set `access_token` variable with the token

### 3. Test Protected Routes
All protected routes (marked with 🔒) require the Bearer token:
- Authorization header is automatically added: `Bearer {{access_token}}`
- Make sure you've set the `access_token` variable after login!

## Authentication Flow

### JSON Bearer Token Authentication
**All protected endpoints use JSON Bearer Token authentication:**

```
Headers:
{
  "Authorization": "Bearer {{access_token}}",
  "Content-Type": "application/json"
}
```

**Why JSON Bearer over OAuth Form?**
- ✅ Better for API testing
- ✅ Supports JSON request/response body
- ✅ More flexible for frontend integration
- ✅ Can be easily managed with Postman environment variables

### Logging
Every endpoint includes automatic logging with the following format:
```
{TIMESTAMP} | {LEVEL} | {SERVICE} | {ROUTE} | {MESSAGE}
```

Examples:
- `2026-03-31 10:00:00,123 | INFO | AUTH | POST /auth/login | Login successful for user@example.com`
- `2026-03-31 10:00:01,456 | ERROR | SKILL | POST /skills | Failed to create skill: Duplicate name`
- `2026-03-31 10:00:02,789 | WARNING | FREELANCER | GET /freelancers/123 | Freelancer 123 not found`

## Response Format

### Success Response
```json
{
  "status": "success",
  "reason": "Descriptive message about what was accomplished",
  "data": {}  // Additional data if applicable
}
```

### Error Response
```json
{
  "status": "error",
  "reason": "Descriptive error message"
}
```

## Endpoint Categories

### 🔓 Public Endpoints (No Authentication Required)
- `POST /auth/register` - Register new user
- `POST /auth/login` - Login and get token

### 🔒 Protected Endpoints (Bearer Token Required)
All other endpoints require authentication with Bearer token:

**AUTH Routes:**
- `GET /auth/me` - Get current user info

**USER Routes:**
- `GET /users` - Get all users (with pagination)
- `GET /users/{user_id}` - Get single user
- `POST /users` - Create new user
- `PUT /users/{user_id}` - Update user
- `DELETE /users/{user_id}` - Delete user
- `GET /users/search/{search_term}` - Search users

**CLIENT Routes:**
- `GET /clients` - Get all clients
- `GET /clients/{identifier}` - Get single client (supports client_id or user_id)
- `POST /clients` - Create new client
- `PUT /clients/{identifier}` - Update client
- `DELETE /clients/{identifier}` - Delete client
- `GET /clients/search/{search_term}` - Search clients

**FREELANCER Routes:**
- `GET /freelancers` - Get all freelancers
- `GET /freelancers/{identifier}` - Get single freelancer
- `POST /freelancers` - Create new freelancer (auto-creates embedding)
- `PUT /freelancers/{identifier}` - Update freelancer (auto-updates embedding)
- `DELETE /freelancers/{identifier}` - Delete freelancer (auto-deletes embedding)
- `GET /freelancers/search/{search_term}` - Search freelancers
- `GET /freelancers/{freelancer_id}/embedding` - Get embedding metadata

**SKILL Routes:**
- `GET /skills` - Get all skills
- `GET /skills/{skill_id}` - Get single skill
- `GET /skills/category/{category}` - Get skills by category
- `POST /skills` - Create new skill
- `PUT /skills/{skill_id}` - Update skill
- `DELETE /skills/{skill_id}` - Delete skill
- `GET /skills/search/{search_term}` - Search skills

**SPECIALITY Routes:**
- `GET /specialities` - Get all specialities
- `GET /specialities/{speciality_id}` - Get single speciality
- `POST /specialities` - Create new speciality
- `PUT /specialities/{speciality_id}` - Update speciality
- `DELETE /specialities/{speciality_id}` - Delete speciality
- `GET /specialities/search/{search_term}` - Search specialities

**LANGUAGE Routes:**
- `GET /languages` - Get all languages
- `GET /languages/{language_id}` - Get single language
- `POST /languages` - Create new language
- `PUT /languages/{language_id}` - Update language
- `DELETE /languages/{language_id}` - Delete language
- `GET /languages/search/{search_term}` - Search languages

## Common Variables

In the environment file, you can use these variables anywhere:

| Variable | Default Value | Usage |
|----------|---------------|-------|
| `{{base_url}}` | http://localhost:8000 | API base URL |
| `{{access_token}}` | (empty) | JWT token after login |
| `{{email}}` | user@example.com | Test email |
| `{{password}}` | SecurePassword123! | Test password |
| `{{user_id}}` | uuid | User ID variable |
| `{{client_id}}` | uuid-client-1 | Client ID variable |
| `{{freelancer_id}}` | uuid-freelancer-1 | Freelancer ID variable |
| `{{skill_id}}` | uuid-skill-1 | Skill ID variable |
| `{{speciality_id}}` | uuid-speciality-1 | Speciality ID variable |
| `{{language_id}}` | uuid-lang-1 | Language ID variable |

## Example Flow: Complete Testing Workflow

### 1. Register a User
```
POST {{base_url}}/auth/register
Body: {
  "email": "testuser@example.com",
  "password": "TestPass123!",
  "user_type": "freelancer",
  "full_name": "Test User",
  "company_name": null
}
```
Response:
```json
{
  "status": "success",
  "reason": "User testuser@example.com registered successfully as freelancer",
  "data": {
    "user_id": "uuid-123",
    "email": "testuser@example.com",
    "type": "freelancer"
  }
}
```

### 2. Login
```
POST {{base_url}}/auth/login
Body: {
  "email": "testuser@example.com",
  "password": "TestPass123!"
}
```
Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```
✅ **Update Environment:** Set `access_token` to the value from response

### 3. Get Current User
```
GET {{base_url}}/auth/me
Headers:
  Authorization: Bearer {{access_token}}
```
Response:
```json
{
  "user_id": "uuid-123",
  "email": "testuser@example.com",
  "type": "freelancer"
}
```

### 4. Create Freelancer Profile
```
POST {{base_url}}/freelancers
Headers:
  Authorization: Bearer {{access_token}}
Body: {
  "user_id": "uuid-123",
  "full_name": "Test User",
  "bio": "A test freelancer",
  "estimated_rate": 50,
  "rate_time": "hour",
  "rate_currency": "USD"
}
```
Response:
```json
{
  "status": "success",
  "reason": "Created freelancer uuid-456 for user uuid-123 - Test User"
}
```

## Tips & Tricks

### 1. Using Variables in Request URLs
```
{{base_url}}/freelancers/{{freelancer_id}}
```

### 2. Using Variables in Request Body
```json
{
  "email": "{{email}}",
  "password": "{{password}}"
}
```

### 3. Quickly Update Token After Login
1. Login endpoint returns `access_token`
2. Right-click on the token value in response
3. Select "Set: Capstone API Environment" → "access_token"
4. Or manually copy-paste in Environment settings

### 4. Test Collections
Use Postman's **Collection Runner** to run all tests in sequence:
1. Click **"Runner"** button
2. Select **"Capstone API Collection"**
3. Select **"Capstone API Environment"**
4. Click **"Run Collection"**

### 5. Check Console Logs
Your backend logs will appear in the console output:
```
2026-03-31 10:00:00,000 | INFO | FREELANCER | POST /freelancers | Created freelancer uuid-456 for user uuid-123 - Test User
```

## Troubleshooting

### "Authorization header invalid" Error
- ❌ Make sure you're logged in and have copied the token
- ❌ Check that the environment variable `{{access_token}}` is set
- ✅ Verify the Bearer token is properly formatted: `Bearer {token}`

### "User not found" Error
- ❌ Make sure you created the user/resource first
- ❌ Check that you're using correct IDs from previous responses
- ✅ Copy resource IDs from response bodies

### 404 Error on Search Endpoint
- ❌ Make sure the search_term is in the correct path parameter
- ❌ Some resources might not exist yet
- ✅ Create test data first before searching

### Base URL Connection Error
- ❌ Verify your backend is running on `http://localhost:8000`
- ✅ If on different port/host, update `{{base_url}}` in environment
- ✅ Check Docker containers are running: `docker compose ps`

## Additional Resources

For more information:
- See [AUTHENTICATION_IMPLEMENTATION.md](../AUTHENTICATION_IMPLEMENTATION.md) for auth details
- See [ARCHITECTURE_DIAGRAM.md](../ARCHITECTURE_DIAGRAM.md) for system architecture
- See [logger.py](../functions/logger.py) for logging configuration

## Support

For issues or questions about the API:
1. Check the logs in `/logs/app.log`
2. Review the response `reason` field for descriptive error messages
3. Ensure all required fields are provided in request body
4. Verify authentication token is valid and not expired
