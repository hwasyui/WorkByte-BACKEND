# Authentication & Authorization Setup Guide

## Overview
The API now has **comprehensive authentication and authorization** with role-based access control (RBAC).

---

## Authentication Architecture

### 1. OAuth2 with JWT Bearer Tokens
- **Scheme**: OAuth2PasswordBearer
- **Token URL**: `/auth/login`
- **Algorithm**: HS256
- **Expiration**: 30 minutes
- **Note**: Client credentials (client_id/secret) are NOT needed for this simple OAuth2 setup. Users authenticate with email/password.

### 2. Authentication Flow

```
User Registration:
POST /auth/register
├─ email (required)
├─ password (required, min 8 chars)
├─ user_type (freelancer or client)
├─ full_name (if freelancer)
└─ company_name (if client)

↓ Creates:
- User account
- Auto-creates Freelancer or Client profile
- Hashed password (Argon2)

User Login:
POST /auth/login
├─ email
└─ password

↓ Returns:
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}

Authenticated Request:
GET /freelancers
Header: Authorization: Bearer <access_token>

↓ Response (if valid):
[{freelancer data}]

↓ Response (if invalid/expired):
401 Unauthorized - "Could not validate credentials"
```

---

## Authorization & Role-Based Access Control

### Public Endpoints (No Auth Required)
```
POST /auth/register    - Register new user
POST /auth/login       - Login user
```

### Authenticated Endpoints (All Authenticated Users Can Access)
```
GET    /auth/me                          - Get current user info
GET    /users                            - List all users
GET    /users/{user_id}                  - Get user details
POST   /users                            - Create new user
PUT    /users/{user_id}                  - Update user
DELETE /users/{user_id}                  - Delete user
GET    /skills                           - List all skills
GET    /skills/{skill_id}                - Get skill details
POST   /skills                           - Create skill
PUT    /skills/{skill_id}                - Update skill
DELETE /skills/{skill_id}                - Delete skill
GET    /languages                        - List all languages
GET    /languages/{language_id}          - Get language details
POST   /languages                        - Create language
PUT    /languages/{language_id}          - Update language
DELETE /languages/{language_id}          - Delete language
GET    /specialities                     - List all specialities
GET    /specialities/{speciality_id}     - Get speciality details
POST   /specialities                     - Create speciality
PUT    /specialities/{speciality_id}     - Update speciality
DELETE /specialities/{speciality_id}     - Delete speciality
```

### Freelancer-Only Endpoints
```
GET    /freelancers                           - List freelancers [REQUIRES: user.type == "freelancer"]
GET    /freelancers/{identifier}              - Get freelancer details
POST   /freelancers                           - Create freelancer profile
PUT    /freelancers/{identifier}              - Update freelancer profile
DELETE /freelancers/{identifier}              - Delete freelancer profile
```

### Client-Only Endpoints
```
GET    /clients                               - List clients [REQUIRES: user.type == "client"]
GET    /clients/{identifier}                  - Get client details
POST   /clients                               - Create client profile
PUT    /clients/{identifier}                  - Update client profile
DELETE /clients/{identifier}                  - Delete client profile
```

---

## How to Test

### 1. Register a Freelancer
```bash
curl -X POST "http://localhost:8000/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "freelancer@example.com",
    "password": "SecurePassword123",
    "user_type": "freelancer",
    "full_name": "John Developer"
  }'
```

### 2. Register a Client
```bash
curl -X POST "http://localhost:8000/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "client@example.com",
    "password": "SecurePassword123",
    "user_type": "client",
    "company_name": "Tech Company Inc"
  }'
```

### 3. Login (Get Token)
```bash
curl -X POST "http://localhost:8000/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "freelancer@example.com",
    "password": "SecurePassword123"
  }'

# Response:
# {
#   "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
#   "token_type": "bearer"
# }
```

### 4. Access Freelancer Route (with token)
```bash
curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..." \
  http://localhost:8000/freelancers
```

### 5. Try Accessing Client Route as Freelancer
```bash
curl -H "Authorization: Bearer <freelancer_token>" \
  http://localhost:8000/clients

# Returns 403 Forbidden:
# {"detail": "Only clients can access this resource"}
```

### 6. Try Accessing Without Token
```bash
curl http://localhost:8000/freelancers

# Returns 401 Unauthorized:
# {"detail": "Not authenticated"}
```

---

## Authentication Functions

### In `functions/authentication.py`

```python
# Token generation & verification
create_access_token()      - Creates JWT token with expiration
verify_token()            - Validates JWT token
get_password_hash()       - Hashes password with Argon2
verify_password()         - Verifies password against hash

# User lookups
authenticate_user()       - Validates email/password combo
get_user()               - Retrieves user by email
register_user()          - Creates user + auto-creates profile

# Dependency functions (for route protection)
get_current_user()       - Returns authenticated user (any type)
get_freelancer_user()    - Returns user if type=="freelancer"
get_client_user()        - Returns user if type=="client"
```

### Dependency Injection Pattern
Routes use FastAPI's `Depends()` to inject authentication:

```python
@freelancer_router.get("")
async def get_freelancers(current_user: UserInDB = Depends(get_freelancer_user)):
    # Only freelancers reach here
    # current_user contains: user_id, email, password, type
    return freelancers
```

---

## Security Features

### 1. Password Hashing (Argon2)
- Industry-standard, memory-hard hash
- Resistant to GPU attacks
- Salt automatically included

### 2. JWT Token Security
- Secret key from environment variable
- HS256 algorithm
- Expiration time: 30 minutes
- Payload includes email as subject (`sub`)

### 3. Role-Based Access Control
- Users marked as "freelancer" or "client"
- Routes check user.type before processing
- Returns 403 Forbidden if role doesn't match

### 4. Exception Handling
- 401 Unauthorized: Missing/invalid token
- 403 Forbidden: Wrong role/permission
- 400 Bad Request: Invalid input
- 500 Internal Server Error: Server error

---

## Environment Variables Required

Add to `.env`:
```
SECRET_KEY=your-super-secret-key-here-at-least-32-characters
```

If not set, the app will raise an error on startup.

---

## No Client Credentials Needed
This setup uses **Resource Owner Password Credentials Flow** (OAuth2 password grant), which is simpler than full OAuth2 client credentials flow. It's appropriate for:
- First-party applications
- Desktop/mobile apps
- Internal services

If you need client credentials (for third-party apps), that would require:
- Additional client registration
- Client_id and client_secret storage
- More complex token exchange logic

For now, email/password is sufficient and secure.

---

## Summary of Changes

### Files Modified:
1. **functions/authentication.py**
   - Added `get_freelancer_user()` dependency
   - Added `get_client_user()` dependency

2. **routes/auth_router.py**
   - Removed duplicate `/me` endpoint

3. **routes/freelancers/freelancer_routes.py**
   - All endpoints require `get_freelancer_user` dependency
   - Added auth check to all methods

4. **routes/clients/client_routes.py**
   - All endpoints require `get_client_user` dependency
   - Added auth check to all methods

5. **routes/users/users_routes.py**
   - All endpoints require `get_current_user` dependency
   - Generic auth for any authenticated user

6. **routes/skills/skill_routes.py**
   - All endpoints require `get_current_user` dependency

7. **routes/languages/language_routes.py**
   - All endpoints require `get_current_user` dependency

8. **routes/specialities/speciality_routes.py**
   - All endpoints require `get_current_user` dependency

### Authentication Flow:
✅ No client credentials needed
✅ Bearer token authentication
✅ Role-based access control (Freelancer vs Client)
✅ All routes locked except /auth/register and /auth/login
✅ Proper error responses (401, 403)
✅ Argon2 password hashing
✅ JWT token with expiration
