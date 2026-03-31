# API Authentication & Authorization Architecture

## Request Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLIENT REQUEST                              │
│  GET /freelancers                                               │
│  Header: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ↓
        ┌────────────────────────────────────────┐
        │ FastAPI OAuth2PasswordBearer Middleware │
        │  - Extract token from Authorization    │
        │  - Validate format (Bearer <token>)    │
        └────────────────────┬───────────────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
              ↓ Token present?              ↓ No token
        ┌──────────────┐                401 Unauthorized
        │ Verify Token │────→ Invalid token? ──→ 401
        │ (JWT/HS256)  │                │
        └──────┬───────┘                │
               │                        │ Expired?  ──→ 401
               │ ✓ Valid
               ↓
        ┌──────────────────┐
        │ Extract user     │
        │ email from token │
        └────────┬─────────┘
                 │
                 ↓
        ┌──────────────────────┐
        │ Query database for   │
        │ user by email        │
        └────────┬─────────────┘
                 │
        ┌────────┴────────┐
        │                 │
        ↓ User found      ↓ Not found
   ┌─────────────┐    401 Error
   │ Execute     │
   │ get_current │
   │ _user()     │
   │ dependency  │
   └────────┬────┘
            │
            ↓
   ┌──────────────────────────┐
   │ get_current_user()       │ (returns UserInDB)
   │ (Generic Auth)           │
   │ ✓ Any authenticated user │
   └────────┬─────────────────┘
            │
            ↓ For /freelancers route:
   ┌──────────────────────────────────┐
   │ get_freelancer_user()            │
   │ (Role-Based Auth)                │
   └────────┬─────────────────────────┘
            │
      ┌─────┴─────┐
      │            │
      ↓ Type OK    ↓ Wrong type
   ✓/✓ Continue   403 Forbidden
      │            "Only freelancers..."
      ↓
   ┌─────────────────────────────────────┐
   │ Route Handler Executes Safely       │
   │ (current_user is guaranteed valid)  │
   └────────────┬────────────────────────┘
                │
                ↓
        ┌──────────────────┐
        │ Return response  │
        │ (200/201/etc)    │
        └──────────────────┘
```

---

## Authentication Dependency Injection

### Pattern 1: Generic Authentication
```python
from fastapi import Depends
from functions.authentication import get_current_user

@router.get("/users")
async def get_users(current_user: UserInDB = Depends(get_current_user)):
    # ✓ Reaches here if: user authenticated AND token valid
    # ✗ Returns 401 if: no token OR token invalid
    return users

# Usage:
# ✓ curl -H "Authorization: Bearer <token>" /users  → 200 OK
# ✗ curl /users  → 401 Not authenticated
# ✗ curl -H "Authorization: Bearer invalid" /users  → 401
```

### Pattern 2: Freelancer-Only Access
```python
from fastapi import Depends
from functions.authentication import get_freelancer_user

@router.get("/freelancers")
async def get_freelancers(current_user: UserInDB = Depends(get_freelancer_user)):
    # ✓ Reaches here if: user authenticated AND type == "freelancer"
    # ✗ Returns 401 if: no token
    # ✗ Returns 403 if: type != "freelancer"
    return freelancers

# Usage:
# ✓ Freelancer with token → 200 OK
# ✗ Client with token → 403 Forbidden
# ✗ No token → 401 Unauthorized
```

### Pattern 3: Client-Only Access
```python
from fastapi import Depends
from functions.authentication import get_client_user

@router.get("/clients")
async def get_clients(current_user: UserInDB = Depends(get_client_user)):
    # ✓ Reaches here if: user authenticated AND type == "client"
    # ✗ Returns 401 if: no token
    # ✗ Returns 403 if: type != "client"
    return clients
```

---

## Route Protection Summary

### Public Routes (No Auth)
```
POST /auth/register          → 🟢 Open
POST /auth/login             → 🟢 Open
```

### User Routes (Any Auth)
```
GET /auth/me                → 🔵 Requires: Login
GET /users                  → 🔵 Requires: Login
POST /users                 → 🔵 Requires: Login
PUT /users/{id}             → 🔵 Requires: Login
DELETE /users/{id}          → 🔵 Requires: Login
GET /skills                 → 🔵 Requires: Login
POST /skills                → 🔵 Requires: Login
PUT /skills/{id}            → 🔵 Requires: Login
DELETE /skills/{id}         → 🔵 Requires: Login
GET /languages              → 🔵 Requires: Login
POST /languages             → 🔵 Requires: Login
PUT /languages/{id}         → 🔵 Requires: Login
DELETE /languages/{id}      → 🔵 Requires: Login
GET /specialities           → 🔵 Requires: Login
POST /specialities          → 🔵 Requires: Login
PUT /specialities/{id}      → 🔵 Requires: Login
DELETE /specialities/{id}   → 🔵 Requires: Login
```

### Freelancer Routes (Freelancer Only)
```
GET /freelancers            → 🔴 Requires: Freelancer login
GET /freelancers/{id}       → 🔴 Requires: Freelancer login
POST /freelancers           → 🔴 Requires: Freelancer login
PUT /freelancers/{id}       → 🔴 Requires: Freelancer login
DELETE /freelancers/{id}    → 🔴 Requires: Freelancer login
```

### Client Routes (Client Only)
```
GET /clients                → 🟣 Requires: Client login
GET /clients/{id}           → 🟣 Requires: Client login
POST /clients               → 🟣 Requires: Client login
PUT /clients/{id}           → 🟣 Requires: Client login
DELETE /clients/{id}        → 🟣 Requires: Client login
```

---

## Login Flow

```
┌─────────────────────────────────────────┐
│ 1. User submits credentials             │
│    POST /auth/login                     │
│    {                                    │
│      "email": "user@example.com",       │
│      "password": "SecurePass123"        │
│    }                                    │
└────────────────┬────────────────────────┘
                 │
                 ↓
        ┌────────────────────┐
        │ Authenticate User  │
        │ - Lookup by email  │
        │ - Verify password  │
        │   with Argon2      │
        └────────┬───────────┘
                 │
        ┌────────┴────────┐
        │                 │
        ↓ Success         ↓ Failure
   ┌─────────────┐     ┌──────────────┐
   │ Create JWT  │     │ 401 Error    │
   │ Include:    │     │ Invalid creds│
   │ - email     │     └──────────────┘
   │ - exp time  │
   │ - algorithm │
   │ Sign with   │
   │ SECRET_KEY  │
   └────────┬────┘
            │
            ↓
   ┌──────────────────────────────┐
   │ Return JWT Token             │
   │ {                            │
   │   "access_token": "eyJ...",  │
   │   "token_type": "bearer"     │
   │ }                            │
   └──────────────────────────────┘
            │
            ↓
   ┌──────────────────────────────┐
   │ Client receives token        │
   │ Stores in memory/localStorage│
   │ Adds to all future requests: │
   │ Authorization: Bearer eyJ... │
   └──────────────────────────────┘
```

---

## Registration Flow

```
┌──────────────────────────────────────────┐
│ 1. New user registers                    │
│    POST /auth/register                   │
│    {                                     │
│      "email": "freelancer@example.com",  │
│      "password": "SecurePass123",        │
│      "user_type": "freelancer",          │
│      "full_name": "John Developer"       │
│    }                                     │
└────────────────┬─────────────────────────┘
                 │
                 ↓
        ┌──────────────────────┐
        │ Validate input       │
        │ - Email format ✓     │
        │ - Password length ✓  │
        │ - User type valid ✓  │
        │ - Email not exists ✓ │
        └────────┬─────────────┘
                 │
                 ↓
        ┌──────────────────────┐
        │ Create User          │
        │ - Hash password      │
        │   (Argon2)           │
        │ - Generate UUID      │
        │ - Save to DB         │
        └────────┬─────────────┘
                 │
                 ↓
        ┌──────────────────────┐
        │ Auto-create Profile  │
        │ If freelancer:       │
        │   → Create Freelancer│
        │      Profile         │
        │ If client:           │
        │   → Create Client    │
        │      Profile         │
        └────────┬─────────────┘
                 │
                 ↓
        ┌──────────────────────┐
        │ 200 OK Response      │
        │ User created + Login │
        │ user_id returned     │
        └──────────────────────┘
```

---

## Token Verification Process

```
Token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlbWFpbEBlbWFpbC5jb20iLCJleHAiOjE2NTQzMjE2MDB9.abc123...

                            ↓

        ┌───────────────────────────────────┐
        │ 1. Extract Header                 │
        │    {                              │
        │      "alg": "HS256",               │
        │      "typ": "JWT"                 │
        │    }                              │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 2. Extract Payload                │
        │    {                              │
        │      "sub": "email@email.com",    │
        │      "exp": 1654321600           │
        │    }                              │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 3. Check Expiration               │
        │    current_time < exp_time? ✓     │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 4. Verify Signature               │
        │    Decode(signature, SECRET_KEY)  │
        │    Should match computed digest ✓ │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 5. Extract User Email             │
        │    email = payload["sub"]         │
        │    = "email@email.com"            │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 6. Retrieve User from DB          │
        │    Find user where email = "x"    │
        │    user_id, type, password, etc   │
        └───────────────────────────────────┘

        ┌───────────────────────────────────┐
        │ 7. Return UserInDB Object         │
        │    {                              │
        │      user_id: "uuid",             │
        │      email: "email@email.com",    │
        │      type: "freelancer",          │
        │      password: "hashed..."        │
        │    }                              │
        └───────────────────────────────────┘

        ↓ ✓ Successfully authenticated!
```

---

## Error Responses

```
Scenario 1: No Authorization Header
Request: GET /freelancers
Response:
  Status: 401 Unauthorized
  {
    "detail": "Not authenticated"
  }

Scenario 2: Invalid Token Format
Request: GET /freelancers
         Authorization: Bearer invalid
Response:
  Status: 401 Unauthorized
  {
    "detail": "Could not validate credentials"
  }

Scenario 3: Expired Token
Request: GET /freelancers
         Authorization: Bearer eyJ... (expired)
Response:
  Status: 401 Unauthorized
  {
    "detail": "Could not validate credentials"
  }

Scenario 4: Valid Token but Wrong Role (Freelancer accessing /clients)
Request: GET /clients
         Authorization: Bearer eyJ... (client-only role)
Response:
  Status: 403 Forbidden
  {
    "detail": "Only clients can access this resource"
  }

Scenario 5: Valid Token but Another Role tries
Request: GET /freelancers
         Authorization: Bearer eyJ... (client token)
Response:
  Status: 403 Forbidden
  {
    "detail": "Only freelancers can access this resource"
  }
```

---

## Security Implementation Details

### Password Hashing (Argon2)
```
User Password: "MySecurePassword123"
                        ↓
            ┌───────────────────────┐
            │ Hash with Argon2      │
            │ - K=64MB Memory       │
            │ - t=3 Iterations      │
            │ - Parallelism=4       │
            │ - Salt: random 16B    │
            └───────────────────────┘
                        ↓
Stored Hash: "$argon2id$v=19$m=65536,t=3,p=4$abcdef...==$xyz123..."
(Never stored plaintext)
```

### JWT Token Structure
```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJlbWFpbCIsImV4cCI6MTY1NDMyMTYwMH0.sig...
├─ Header (base64url)
│  {
│    "alg": "HS256",
│    "typ": "JWT"
│  }
├─ Payload (base64url)
│  {
│    "sub": "user@email.com",
│    "exp": 1654321600
│  }
└─ Signature (HMAC-SHA256)
   HMAC-SHA256(
     <header>.<payload>,
     SECRET_KEY
   )
```

---

## File Structure

```
backend/
├── functions/
│   ├── authentication.py
│   │   ├── verify_password()           # Argon2 verification
│   │   ├── get_password_hash()         # Argon2 hashing
│   │   ├── create_access_token()       # JWT generation
│   │   ├── verify_token()              # JWT validation
│   │   ├── get_current_user()          # Generic auth
│   │   ├── get_freelancer_user()       # Freelancer-only
│   │   └── get_client_user()           # Client-only
│   └── schema_model.py
│       ├── UserInDB                    # User data model
│       ├── UserRegister                # Registration input
│       ├── UserLogin                   # Login input
│       └── Token                       # Token response
├── routes/
│   ├── auth_router.py                  # /auth endpoints
│   ├── freelancers/freelancer_routes.py
│   │   ├── get_all_freelancers()       # @Depends(get_freelancer_user)
│   │   ├── get_freelancer()            # @Depends(get_freelancer_user)
│   │   ├── create_freelancer()         # @Depends(get_freelancer_user)
│   │   ├── update_freelancer()         # @Depends(get_freelancer_user)
│   │   └── delete_freelancer()         # @Depends(get_freelancer_user)
│   ├── clients/client_routes.py
│   │   ├── get_all_clients()           # @Depends(get_client_user)
│   │   ├── get_client()                # @Depends(get_client_user)
│   │   ├── create_client()             # @Depends(get_client_user)
│   │   ├── update_client()             # @Depends(get_client_user)
│   │   └── delete_client()             # @Depends(get_client_user)
│   ├── users/users_routes.py           # All @Depends(get_current_user)
│   ├── skills/skill_routes.py          # All @Depends(get_current_user)
│   ├── languages/language_routes.py    # All @Depends(get_current_user)
│   └── specialities/speciality_routes.py # All @Depends(get_current_user)
└── main/
    └── main.py                         # FastAPI app with routers
```

---

## Summary

✅ **Authentication**: OAuth2 with JWT tokens (email/password)
✅ **Authorization**: Role-based access control (Freelancer/Client/Any)
✅ **Security**: Argon2 password hashing, token expiration
✅ **API Design**: Clean dependency injection with `Depends()`
✅ **Error Handling**: Proper 401/403 HTTP responses
✅ **Implementation**: 7 route files modified, 2 new dependencies
✅ **Testing**: Automated test script included
✅ **Documentation**: Comprehensive guides provided
