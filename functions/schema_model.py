from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional, List
from datetime import date, datetime

# ==================== AUTHENTICATION ====================
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserInDB(BaseModel):
    user_id: str
    email: str
    password: str
    type: str

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    user_type: str = "freelancer"  # freelancer or client
    full_name: Optional[str] = None  # Required for freelancer
    company_name: Optional[str] = None  # Required for client

    @field_validator('password')
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v
    
    @field_validator('user_type')
    @classmethod
    def validate_user_type(cls, v):
        if v not in ["freelancer", "client"]:
            raise ValueError("user_type must be 'freelancer' or 'client'")
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    user_id: str
    email: str
    type: str
    
class FreelancerProfileCreate(BaseModel):
    full_name: str
    bio: Optional[str] = None
    
class ClientProfileCreate(BaseModel):
    company_name: str
    company_description: Optional[str] = None


# ==================== USERS ====================
class UserCreate(BaseModel):
    user_id: Optional[str] = None  # Auto-generated if not provided
    email: EmailStr
    password: str
    type: str = "freelancer"

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    type: Optional[str] = None

class UserResponseDetail(BaseModel):
    user_id: str
    email: str
    type: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== FREELANCERS ====================
class FreelancerCreate(BaseModel):
    freelancer_id: Optional[str] = None  # Auto-generated if not provided
    user_id: str
    full_name: str
    bio: Optional[str] = None
    cv_file_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    estimated_rate: Optional[float] = None
    rate_time: Optional[str] = "hourly"
    rate_currency: Optional[str] = "USD"

class FreelancerUpdate(BaseModel):
    full_name: Optional[str] = None
    bio: Optional[str] = None
    cv_file_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    estimated_rate: Optional[float] = None
    rate_time: Optional[str] = None
    rate_currency: Optional[str] = None

class FreelancerResponse(BaseModel):
    freelancer_id: str
    user_id: str
    full_name: str
    bio: Optional[str] = None
    cv_file_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    estimated_rate: Optional[float] = None
    rate_time: Optional[str] = None
    rate_currency: Optional[str] = None
    total_projects: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== CLIENTS ====================
class ClientCreate(BaseModel):
    client_id: Optional[str] = None  # Auto-generated if not provided
    user_id: str
    company_name: Optional[str] = None
    company_description: Optional[str] = None
    website_url: Optional[str] = None

class ClientUpdate(BaseModel):
    company_name: Optional[str] = None
    company_description: Optional[str] = None
    website_url: Optional[str] = None

class ClientResponse(BaseModel):
    client_id: str
    user_id: str
    company_name: Optional[str] = None
    company_description: Optional[str] = None
    website_url: Optional[str] = None
    total_jobs_posted: Optional[int] = 0
    total_projects_completed: Optional[int] = 0
    average_rating_given: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== SKILLS ====================
class SkillCreate(BaseModel):
    skill_id: Optional[str] = None  # Auto-generated if not provided
    skill_name: str
    skill_category: str  # hard_skill, soft_skill, tool
    description: Optional[str] = None

class SkillUpdate(BaseModel):
    skill_name: Optional[str] = None
    skill_category: Optional[str] = None
    description: Optional[str] = None

class SkillResponse(BaseModel):
    skill_id: str
    skill_name: str
    skill_category: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== LANGUAGES ====================
class LanguageCreate(BaseModel):
    language_id: Optional[str] = None  # Auto-generated if not provided
    language_name: str
    iso_code: Optional[str] = None

class LanguageUpdate(BaseModel):
    language_name: Optional[str] = None
    iso_code: Optional[str] = None

class LanguageResponse(BaseModel):
    language_id: str
    language_name: str
    iso_code: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== SPECIALITIES ====================
class SpecialityCreate(BaseModel):
    speciality_id: Optional[str] = None  # Auto-generated if not provided
    speciality_name: str
    description: Optional[str] = None

class SpecialityUpdate(BaseModel):
    speciality_name: Optional[str] = None
    description: Optional[str] = None

class SpecialityResponse(BaseModel):
    speciality_id: str
    speciality_name: str
    description: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
