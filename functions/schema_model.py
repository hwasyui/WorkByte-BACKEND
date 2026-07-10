from fastapi import File, Form, Request, UploadFile
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, Any, Dict, List, Literal
from datetime import date, datetime

# Authentication
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserInDB(BaseModel):
    user_id: str
    email: str
    password: str
    password_login_enabled: bool = True
    email_verified: bool = False
    is_admin: bool = False
    freelancer_id: Optional[str] = None
    client_id: Optional[str] = None
    is_report_banned: bool = False
    ban_message: Optional[str] = None
    report_banned_at: Optional[datetime] = None

class UserRegister(BaseModel):
    email: EmailStr
    password: str
    user_type: str = "freelancer"  # initial role: freelancer or client
    full_name: Optional[str] = None
    company_name: Optional[str] = None

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

class AddRoleRequest(BaseModel):
    role: str
    full_name: Optional[str] = None

    @field_validator('role')
    @classmethod
    def validate_role(cls, v):
        if v not in ["freelancer", "client"]:
            raise ValueError("role must be 'freelancer' or 'client'")
        return v

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class EmailVerificationRequest(BaseModel):
    email: EmailStr
    otp: str

class ResendVerificationRequest(BaseModel):
    email: EmailStr

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str

    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

class RefreshRequest(BaseModel):
    refresh_token: str

class GoogleMobileTokenRequest(BaseModel):
    id_token: str

class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

class SetPasswordRequest(BaseModel):
    new_password: str

    @field_validator('new_password')
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        return v

class UserResponse(BaseModel):
    user_id: str
    email: str
    password_login_enabled: bool = True
    email_verified: bool = False
    is_admin: bool = False
    freelancer_id: Optional[str] = None
    client_id: Optional[str] = None
    is_report_banned: bool = False
    ban_message: Optional[str] = None
    report_banned_at: Optional[datetime] = None
    
class FreelancerProfileCreate(BaseModel):
    full_name: str
    bio: Optional[str] = None
    
class ClientProfileCreate(BaseModel):
    company_name: str
    company_description: Optional[str] = None


class CVUploadRequest(BaseModel):
    file: UploadFile = File(...)

    model_config = {"extra": "forbid"}


# Users
class UserCreate(BaseModel):
    user_id: Optional[str] = None
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = None

class UserResponseDetail(BaseModel):
    user_id: str
    email: str
    is_admin: bool = False
    email_verified: bool = False
    email_verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_report_banned: bool = False   
    ban_message: Optional[str] = None
    report_banned_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Freelancers
class FreelancerCreate(BaseModel):
    freelancer_id: Optional[str] = Form(None)
    user_id: Optional[str] = Form(None)
    full_name: str = Form(...)
    title: Optional[str] = Form(None)
    bio: Optional[str] = Form(None)
    profile_picture: Optional[UploadFile] = File(None)
    estimated_rate: Optional[float] = Form(None)
    rate_time: Optional[str] = Form("hourly")
    rate_currency: Optional[str] = Form("USD")

    model_config = {"extra": "forbid"}

    @field_validator("bio")
    @classmethod
    def validate_bio(cls, v):
        if v is not None and len(v) > 500:
            raise ValueError("Bio must be 500 characters or fewer")
        return v

    @field_validator("rate_time")
    @classmethod
    def validate_rate_time(cls, v):
        if v is None:
            return v
        allowed = {"hourly", "weekly", "monthly", "annually"}
        if v not in allowed:
            raise ValueError("rate_time must be one of: hourly, weekly, monthly, annually")
        return v

class FreelancerUpdate(BaseModel):
    full_name: Optional[str] = Form(None)
    title: Optional[str] = Form(None)
    bio: Optional[str] = Form(None)
    profile_picture: Optional[UploadFile] = File(None)
    estimated_rate: Optional[float] = None
    rate_time: Optional[str] = Form(None)
    rate_currency: Optional[str] = Form(None)
    cv_file_url: Optional[str] = None

    model_config = {"extra": "forbid"}

    @field_validator("bio")
    @classmethod
    def validate_bio(cls, v):
        if v is not None and len(v) > 500:
            raise ValueError("Bio must be 500 characters or fewer")
        return v

    @field_validator("rate_time")
    @classmethod
    def validate_rate_time(cls, v):
        if v is None:
            return v
        allowed = {"hourly", "weekly", "monthly", "annually"}
        if v not in allowed:
            raise ValueError("rate_time must be one of: hourly, weekly, monthly, annually")
        return v

    @classmethod
    async def as_form(
        cls,
        full_name: Optional[str] = Form(None),
        title: Optional[str] = Form(None),
        bio: Optional[str] = Form(None),
        profile_picture: Optional[UploadFile] = File(None),
        estimated_rate: Optional[float] = Form(None),
        rate_time: Optional[str] = Form(None),
        rate_currency: Optional[str] = Form(None),
        cv_file_url: Optional[str] = Form(None),
        request: Request = None,
    ) -> "FreelancerUpdate":
        form_data = await request.form()
        form_fields = set(form_data.keys())

        data = {}
        if "full_name" in form_fields:
            data["full_name"] = full_name
        if "title" in form_fields:
            data["title"] = title
        if "bio" in form_fields:
            data["bio"] = bio
        if "profile_picture" in form_fields:
            data["profile_picture"] = profile_picture
        if "estimated_rate" in form_fields:
            data["estimated_rate"] = estimated_rate
        if "rate_time" in form_fields:
            data["rate_time"] = rate_time
        if "rate_currency" in form_fields:
            data["rate_currency"] = rate_currency
        if "cv_file_url" in form_fields:
            data["cv_file_url"] = None if not cv_file_url or not cv_file_url.strip() else cv_file_url

        return cls(**data)

class FreelancerResponse(BaseModel):
    freelancer_id: str
    user_id: str
    full_name: str
    title: Optional[str] = None
    bio: Optional[str] = None
    cv_file_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    estimated_rate: Optional[float] = None
    rate_time: Optional[str] = None
    rate_currency: Optional[str] = None
    total_jobs: Optional[int] = 0
    completed_contracts_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Clients
class ClientCreate(BaseModel):
    client_id: Optional[str] = Form(None)
    user_id: Optional[str] = Form(None)
    full_name: Optional[str] = Form(None)
    bio: Optional[str] = Form(None)
    website_url: Optional[str] = Form(None)
    profile_picture: Optional[UploadFile] = File(None)

    model_config = {"extra": "forbid"}

    @field_validator("bio")
    @classmethod
    def validate_bio(cls, v):
        if v is not None and len(v) > 500:
            raise ValueError("Bio must be 500 characters or fewer")
        return v

class ClientUpdate(BaseModel):
    full_name: Optional[str] = Form(None)
    bio: Optional[str] = Form(None)
    website_url: Optional[str] = Form(None)
    profile_picture: Optional[UploadFile] = File(None)
    contract_message_template: Optional[str] = Form(None)

    model_config = {"extra": "forbid"}

    @field_validator("bio")
    @classmethod
    def validate_bio(cls, v):
        if v is not None and len(v) > 500:
            raise ValueError("Bio must be 500 characters or fewer")
        return v

    @classmethod
    def as_form(
        cls,
        full_name: Optional[str] = Form(None),
        bio: Optional[str] = Form(None),
        website_url: Optional[str] = Form(None),
        profile_picture: Optional[UploadFile] = File(None),
        contract_message_template: Optional[str] = Form(None),
    ) -> "ClientUpdate":
        return cls(
            full_name=full_name,
            bio=bio,
            website_url=website_url,
            profile_picture=profile_picture,
            contract_message_template=contract_message_template,
        )

class ClientResponse(BaseModel):
    client_id: str
    user_id: str
    full_name: Optional[str] = None
    bio: Optional[str] = None
    website_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    total_jobs_posted: Optional[int] = 0
    total_jobs_completed: Optional[int] = 0
    average_rating_given: Optional[float] = None
    contract_message_template: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Skills
class SkillCreate(BaseModel):
    skill_id: Optional[str] = None  # Auto-generated if not provided
    skill_name: str
    skill_category: str  # hard_skill, soft_skill, tool
    description: Optional[str] = None

class SkillUpdate(BaseModel):
    skill_name: Optional[str] = None
    skill_category: Optional[str] = None

class SkillResponse(BaseModel):
    skill_id: str
    skill_name: str
    skill_category: Optional[str] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Freelancer skills
class FreelancerSkillCreate(BaseModel):
    freelancer_skill_id: Optional[str] = None
    freelancer_id: str
    skill_id: str
    proficiency_level: str  # beginner, intermediate, advanced, expert

class FreelancerSkillUpdate(BaseModel):
    proficiency_level: Optional[str] = None

class FreelancerSkillResponse(BaseModel):
    freelancer_skill_id: str
    freelancer_id: str
    skill_id: str
    proficiency_level: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Work experience
class WorkExperienceCreate(BaseModel):
    work_experience_id: Optional[str] = None
    freelancer_id: str
    job_title: str
    company_name: str
    location: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    is_current: Optional[bool] = False
    description: Optional[str] = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):
        if v is not None and len(v) > 1000:
            raise ValueError("Work experience description must be 1000 characters or fewer")
        return v

class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: Optional[bool] = None
    description: Optional[str] = None

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):
        if v is not None and len(v) > 1000:
            raise ValueError("Work experience description must be 1000 characters or fewer")
        return v

class WorkExperienceResponse(BaseModel):
    work_experience_id: str
    freelancer_id: str
    job_title: str
    company_name: str
    location: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    is_current: Optional[bool] = False
    description: Optional[str] = None
    moderation_status: Optional[str] = "scanning"
    scanned_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Education
class EducationCreate(BaseModel):
    education_id: Optional[str] = None
    freelancer_id: str
    institution_name: str
    degree: str
    field_of_study: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    is_current: Optional[bool] = False
    grade: Optional[str] = None
    description: Optional[str] = None

class EducationUpdate(BaseModel):
    institution_name: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: Optional[bool] = None
    grade: Optional[str] = None
    description: Optional[str] = None

class EducationResponse(BaseModel):
    education_id: str
    freelancer_id: str
    institution_name: str
    degree: str
    field_of_study: Optional[str] = None
    start_date: date
    end_date: Optional[date] = None
    is_current: Optional[bool] = False
    grade: Optional[str] = None
    description: Optional[str] = None
    moderation_status: Optional[str] = "scanning"
    scanned_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Job posts
class JobPostCreate(BaseModel):
    job_post_id: Optional[str] = None
    client_id: Optional[str] = None
    job_title: str
    job_description: str
    project_category: Optional[str] = None  # auto-inferred from title+description if omitted
    project_type: str  # individual, team
    project_scope: Optional[str] = None  # small, medium, large; auto-calculated if omitted
    estimated_duration: Optional[str] = None
    working_days: Optional[int] = None
    deadline: Optional[date] = None
    experience_level: Optional[str] = None  # entry, intermediate, expert
    status: Optional[str] = "draft"  # draft, active, closed, filled
    is_ai_generated: Optional[bool] = False

class JobPostUpdate(BaseModel):
    job_title: Optional[str] = None
    job_description: Optional[str] = None
    project_type: Optional[str] = None
    project_scope: Optional[str] = None
    estimated_duration: Optional[str] = None
    working_days: Optional[int] = None
    deadline: Optional[date] = None
    experience_level: Optional[str] = None
    status: Optional[str] = None
    is_ai_generated: Optional[bool] = None

class JobPostResponse(BaseModel):
    job_post_id: str
    client_id: str
    client_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    job_title: str
    job_description: str
    project_type: str
    project_scope: str
    estimated_duration: Optional[str] = None
    working_days: Optional[int] = None
    deadline: Optional[date] = None
    experience_level: Optional[str] = None
    status: str
    is_ai_generated: Optional[bool] = False
    view_count: Optional[int] = 0
    proposal_count: Optional[int] = 0
    role_count: int = 0
    available_positions: Optional[int] = 0               
    closure_reason: Optional[str] = None
    closure_note: Optional[str] = None
    moderation_status: Optional[str] = "scanning"
    scanned_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    posted_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None


class JobPostScopeCalculationRequest(BaseModel):
    job_title: str
    job_description: str
    project_type: str  # individual, team
    estimated_duration: Optional[str] = None
    working_days: Optional[int] = None
    experience_level: Optional[str] = None  # entry, intermediate, expert
    role_count: Optional[int] = 1
    roles: Optional[List["JobPostScopeRoleInput"]] = None


class JobPostScopeCalculationResponse(BaseModel):
    recommended_project_scope: str
    score: int
    confidence: str
    factors: Dict[str, Any]
    reasons: List[str]

    class Config:
        from_attributes = True


class JobPostScopeRoleInput(BaseModel):
    role_title: Optional[str] = None
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = "USD"
    budget_type: Optional[str] = None
    positions_available: Optional[int] = Field(1, ge=1)
    is_required: Optional[bool] = True

# Job roles
class JobRoleCreate(BaseModel):
    job_role_id: Optional[str] = None
    job_post_id: str
    role_title: str
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = "USD"
    budget_type: str  # fixed, negotiable
    role_description: Optional[str] = None
    positions_available: Optional[int] = Field(1, ge=1)
    is_required: Optional[bool] = True
    display_order: Optional[int] = 0

class JobRoleUpdate(BaseModel):
    role_title: Optional[str] = None
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = None
    budget_type: Optional[str] = None
    role_description: Optional[str] = None
    positions_available: Optional[int] = Field(None, ge=1)
    is_required: Optional[bool] = None
    display_order: Optional[int] = None

class JobRoleResponse(BaseModel):
    job_role_id: str
    job_post_id: str
    role_title: str
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = "USD"
    budget_type: str
    role_description: Optional[str] = None
    positions_available: Optional[int] = 1
    positions_filled: Optional[int] = 0
    is_required: Optional[bool] = True
    display_order: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Job role skills
class JobRoleSkillCreate(BaseModel):
    job_role_skill_id: Optional[str] = None
    job_role_id: str
    skill_id: str
    is_required: Optional[bool] = True
    importance_level: Optional[str] = None  # nice_to_have, preferred, required

class JobRoleSkillUpdate(BaseModel):
    is_required: Optional[bool] = None
    importance_level: Optional[str] = None

class JobRoleSkillResponse(BaseModel):
    job_role_skill_id: str
    job_role_id: str
    skill_id: str
    is_required: Optional[bool] = True
    importance_level: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Job files
class JobFileCreate(BaseModel):
    job_post_id: str = Form(...)
    files: List[UploadFile] = File(...)

    model_config = {"extra": "forbid"}

class JobFileUpdate(BaseModel):
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None

class JobFileResponse(BaseModel):
    job_file_id: str
    job_post_id: str
    file_url: str
    file_type: str
    file_name: str
    file_size: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Proposals
class ProposalCreate(BaseModel):
    proposal_id: Optional[str] = None
    job_post_id: str
    job_role_id: Optional[str] = None
    cover_letter: str
    proposed_budget: float
    proposed_duration: Optional[str] = None
    status: Optional[str] = "pending"  # pending, accepted, rejected, withdrawn
    is_ai_generated: Optional[bool] = False

class ProposalUpdate(BaseModel):
    cover_letter: Optional[str] = None
    proposed_budget: Optional[float] = None
    proposed_duration: Optional[str] = None
    status: Optional[str] = None
    is_ai_generated: Optional[bool] = None

class ProposalResponse(BaseModel):
    proposal_id: str
    job_post_id: str
    job_role_id: Optional[str] = None
    freelancer_id: str
    cover_letter: str
    proposed_budget: float
    proposed_duration: Optional[str] = None
    status: str
    is_ai_generated: Optional[bool] = False
    submitted_at: Optional[datetime] = None
    moderation_status: Optional[str] = "visible"
    scanned_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Proposal files
class ProposalFileCreate(BaseModel):
    proposal_id: str = Form(...)
    files: List[UploadFile] = File(...)

    model_config = {"extra": "forbid"}

class ProposalFileUpdate(BaseModel):
    file_url: Optional[str] = None
    file_type: Optional[str] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None

class ProposalFileResponse(BaseModel):
    proposal_file_id: str
    proposal_id: str
    file_url: str
    file_type: str
    file_name: str
    file_size: Optional[int] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Contracts
class ContractCreate(BaseModel):
    contract_id: Optional[str] = None
    job_post_id: str
    job_role_id: str
    proposal_id: str
    freelancer_id: str
    client_id: str
    contract_title: str
    role_title: Optional[str] = None
    agreed_budget: float
    budget_currency: Optional[str] = "USD"
    payment_structure: str  # full_payment, milestone_based
    agreed_duration: Optional[str] = None
    status: str  # active, completed, cancelled, disputed
    start_date: date
    end_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    total_hours_worked: Optional[float] = None
    total_paid: Optional[float] = 0

class ContractUpdate(BaseModel):
    contract_title: Optional[str] = None
    role_title: Optional[str] = None
    agreed_budget: Optional[float] = None
    budget_currency: Optional[str] = None
    payment_structure: Optional[str] = None
    agreed_duration: Optional[str] = None
    status: Optional[str] = None
    end_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    total_hours_worked: Optional[float] = None
    total_paid: Optional[float] = None

class ContractResponse(BaseModel):
    contract_id: str
    job_post_id: str
    job_role_id: str
    proposal_id: str
    freelancer_id: str
    client_id: str
    contract_title: str
    role_title: Optional[str] = None
    agreed_budget: float
    budget_currency: Optional[str] = "USD"
    payment_structure: str
    agreed_duration: Optional[str] = None
    status: str
    start_date: date
    end_date: Optional[date] = None
    actual_completion_date: Optional[date] = None
    total_hours_worked: Optional[float] = None
    total_paid: Optional[float] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    contract_pdf_url: Optional[str] = None
    contract_pdf_generated_at: Optional[datetime] = None
    cancelled_by: Optional[str] = None
    cancellation_reason: Optional[str] = None

    class Config:
        from_attributes = True


# Contract generation
class PaymentScheduleItem(BaseModel):
    phase: str
    description: Optional[str] = None
    amount: Optional[float] = None
    percentage: Optional[float] = None
    due_date: Optional[date] = None


class ContractGenerateRequest(BaseModel):
    end_date: Optional[date] = None
    agreed_duration: Optional[str] = None
    termination_notice: Optional[int] = 30
    governing_law: Optional[str] = None
    confidentiality: Optional[bool] = False
    confidentiality_text: Optional[str] = None
    late_payment_penalty: Optional[float] = None
    dispute_resolution: Optional[str] = "negotiation"
    revision_rounds: Optional[int] = 0
    additional_clauses: Optional[str] = None
    payment_schedule: Optional[str] = None
    # Notification fields
    send_notification: bool = True
    notification_message: Optional[str] = None
    save_message_as_template: bool = False

# Contract submissions
class ContractSubmissionFileResponse(BaseModel):
    file_id: str
    submission_id: str
    file_url: str
    file_name: str
    file_size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    uploaded_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ContractSubmissionResponse(BaseModel):
    submission_id: str
    contract_id: str
    submitted_by: str
    note: Optional[str] = None
    status: str
    submitted_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    revision_note: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    files: List[ContractSubmissionFileResponse] = []

    class Config:
        from_attributes = True

# Portfolio
class PortfolioCreate(BaseModel):
    portfolio_id: Optional[str] = None
    freelancer_id: str
    contract_id: Optional[str] = None
    project_title: str
    project_description: Optional[str] = None
    project_url: Optional[str] = None
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tags: Optional[List[str]] = None

class PortfolioUpdate(BaseModel):
    project_title: Optional[str] = None
    project_description: Optional[str] = None
    project_url: Optional[str] = None
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tags: Optional[List[str]] = None

class PortfolioResponse(BaseModel):
    portfolio_id: str
    freelancer_id: str
    contract_id: Optional[str] = None
    project_title: str
    project_description: Optional[str] = None
    project_url: Optional[str] = None
    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tags: Optional[List[str]] = None
    moderation_status: Optional[str] = "scanning"
    scanned_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Saved jobs
class SavedJobCreate(BaseModel):
    saved_job_id: Optional[str] = None
    job_post_id: str
    freelancer_id: str

class SavedJobResponse(BaseModel):
    saved_job_id: str
    job_post_id: str
    freelancer_id: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Ratings
class RatingCreate(BaseModel):
    rating_id: Optional[str] = None
    contract_id: str
    freelancer_id: str
    communication_score: int
    result_quality_score: int
    professionalism_score: int
    timeline_compliance_score: int
    overall_rating: float
    review_text: Optional[str] = None

class RatingUpdate(BaseModel):
    communication_score: Optional[int] = None
    result_quality_score: Optional[int] = None
    professionalism_score: Optional[int] = None
    timeline_compliance_score: Optional[int] = None
    overall_rating: Optional[float] = None
    review_text: Optional[str] = None

class RatingResponse(BaseModel):
    rating_id: str
    contract_id: str
    client_id: str
    freelancer_id: str
    communication_score: Optional[int] = None
    result_quality_score: Optional[int] = None
    professionalism_score: Optional[int] = None
    timeline_compliance_score: Optional[int] = None
    overall_rating: Optional[float] = None
    review_text: Optional[str] = None
    update_count: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Performance ratings
class PerformanceRatingCreate(BaseModel):
    performance_rating_id: Optional[str] = None
    freelancer_id: str
    overall_performance_score: Optional[float] = None
    confidence_score: Optional[float] = None
    total_ratings_received: Optional[int] = 0
    average_communication: Optional[float] = None
    average_result_quality: Optional[float] = None
    average_professionalism: Optional[float] = None
    average_scope_compliance: Optional[float] = None
    average_timeline_compliance: Optional[float] = None
    success_rate: Optional[float] = None

class PerformanceRatingUpdate(BaseModel):
    overall_performance_score: Optional[float] = None
    confidence_score: Optional[float] = None
    total_ratings_received: Optional[int] = None
    average_communication: Optional[float] = None
    average_result_quality: Optional[float] = None
    average_professionalism: Optional[float] = None
    average_scope_compliance: Optional[float] = None
    average_timeline_compliance: Optional[float] = None
    success_rate: Optional[float] = None

class PerformanceRatingResponse(BaseModel):
    performance_rating_id: str
    freelancer_id: str
    overall_performance_score: Optional[float] = None
    confidence_score: Optional[float] = None
    total_ratings_received: Optional[int] = 0
    average_communication: Optional[float] = None
    average_result_quality: Optional[float] = None
    average_professionalism: Optional[float] = None
    average_scope_compliance: Optional[float] = None
    average_timeline_compliance: Optional[float] = None
    success_rate: Optional[float] = None
    last_calculated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Client trust score
class ClientTrustScoreCreate(BaseModel):
    client_trust_score_id: Optional[str] = None
    client_id: str
    trust_score: Optional[float] = None
    rating_consistency_score: Optional[float] = None
    extreme_rating_ratio: Optional[float] = None
    project_completion_rate: Optional[float] = None
    average_budget_gap: Optional[float] = None
    total_ratings_given: Optional[int] = 0

class ClientTrustScoreUpdate(BaseModel):
    trust_score: Optional[float] = None
    rating_consistency_score: Optional[float] = None
    extreme_rating_ratio: Optional[float] = None
    project_completion_rate: Optional[float] = None
    average_budget_gap: Optional[float] = None
    total_ratings_given: Optional[int] = None

class ClientTrustScoreResponse(BaseModel):
    client_trust_score_id: str
    client_id: str
    trust_score: Optional[float] = None
    rating_consistency_score: Optional[float] = None
    extreme_rating_ratio: Optional[float] = None
    project_completion_rate: Optional[float] = None
    average_budget_gap: Optional[float] = None
    total_ratings_given: Optional[int] = 0
    last_calculated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Freelancer embeddings
class FreelancerEmbeddingCreate(BaseModel):
    embedding_id: Optional[str] = None
    freelancer_id: str
    embedding_vector: List[float]
    embedding_type: Optional[str] = None  # skill_based, profile_based, etc
    last_updated: Optional[datetime] = None

class FreelancerEmbeddingUpdate(BaseModel):
    embedding_vector: Optional[List[float]] = None
    embedding_type: Optional[str] = None

class FreelancerEmbeddingResponse(BaseModel):
    embedding_id: str
    freelancer_id: str
    embedding_vector: List[float]
    embedding_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Job embeddings
class JobEmbeddingCreate(BaseModel):
    embedding_id: Optional[str] = None
    job_role_id: str
    embedding_vector: List[float]
    source_text: Optional[str] = None
    embedding_metadata: Optional[dict] = None

class JobEmbeddingUpdate(BaseModel):
    embedding_vector: Optional[List[float]] = None
    source_text: Optional[str] = None
    embedding_metadata: Optional[dict] = None

class JobEmbeddingResponse(BaseModel):
    embedding_id: str
    job_role_id: str
    job_post_id: str
    embedding_vector: List[float]
    source_text: Optional[str] = None
    embedding_metadata: Optional[dict] = None
    embedding_dirty: Optional[bool] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

# Direct messages

class DMThreadCreate(BaseModel):
    participant_id: str
    job_post_id: Optional[str] = None
    message_text: Optional[str] = None  # if None + job attached → default template used

class DMMessageCreate(BaseModel):
    message_text: str

class DMAttachmentResponse(BaseModel):
    attachment_id: str
    dm_message_id: str
    file_name: str
    file_url: str
    file_type: str
    mime_type: str
    file_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DMMessageResponse(BaseModel):
    dm_message_id: str
    thread_id: str
    sender_id: str
    message_text: str
    metadata: Optional[Dict[str, Any]] = None
    is_read: bool = False
    read_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    status: str = "sent"
    attachments: Optional[List[DMAttachmentResponse]] = None

    class Config:
        from_attributes = True

class DMThreadResponse(BaseModel):
    thread_id: str
    status: str
    initiator_id: str
    other_user: Optional[Dict[str, Any]] = None
    job_post: Optional[Dict[str, Any]] = None
    contract_id: Optional[str] = None
    last_message: Optional[Dict[str, Any]] = None
    unread_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RevisionRequest(BaseModel):
    note: Optional[str] = None

class CancelContractRequest(BaseModel):
    reason: Optional[str] = None

class ReportPaymentRequest(BaseModel):
    amount: float
    note: Optional[str] = None

class RaiseDisputeRequest(BaseModel):
    reason: str

class ArbitrateDisputeRequest(BaseModel):
    outcome: Literal["approve", "cancel", "revise"]
    note: Optional[str] = None
    new_deadline: Optional[date] = None


# Comprehensive freelancer profile
class FreelancerSkillWithDetails(BaseModel):
    freelancer_skill_id: str
    skill: SkillResponse
    proficiency_level: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FreelancerProfileComplete(BaseModel):
    freelancer: FreelancerResponse
    skills: List[FreelancerSkillWithDetails] = []
    education: List[EducationResponse] = []
    work_experience: List[WorkExperienceResponse] = []
    portfolio: List[PortfolioResponse] = []
    ratings: List[RatingResponse] = []  # Ratings received by this freelancer
    total_ratings: Optional[int] = 0
    average_rating: Optional[float] = None

    class Config:
        from_attributes = True

# Reviews

class ReviewRatingInput(BaseModel):
    category: str  # communication | quality | professionalism | value_for_money
    score: float   # 1.0 – 5.0


class SubmitReviewRequest(BaseModel):
    ratings: List[ReviewRatingInput]        # must contain all 4 categories
    client_answer: str                      # answer to the AI-generated targeted question
    overall_comment: str                    # free-form written review
    extra_skill_tags: Optional[List[str]] = []  # client can add extra tags manually


class ReviewResponse(BaseModel):
    id: str
    contract_id: str
    reviewer_id: str
    freelancer_id: str
    inferred_category: str
    status: str
    is_anonymous: bool
    created_at: Optional[datetime] = None
    published_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ReviewDetailResponse(ReviewResponse):
    ratings: Optional[List[dict]] = []
    written_content: Optional[dict] = None
    skill_tags: Optional[List[dict]] = []
    ai_analysis: Optional[dict] = None
    suggested_skill_tags: Optional[List[str]] = []


class TrustScoreResponse(BaseModel):
    freelancer_id: str
    overall_score: float
    weighted_review_avg: Optional[float] = None
    work_quality_score: Optional[float] = None
    revision_rate_score: Optional[float] = None
    responsiveness_score: Optional[float] = None
    communication_sentiment: Optional[float] = None
    total_reviews: int
    category: Optional[str] = None
    category_rank_pct: Optional[float] = None
    last_updated: Optional[datetime] = None

    class Config:
        from_attributes = True


class RedFlagAlertResponse(BaseModel):
    id: str
    freelancer_id: str
    alert_type: str
    severity: str
    message: str
    is_resolved: bool
    triggered_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InitiateReviewPayload(BaseModel):
    contract_id: str


# Notifications

class FCMTokenUpdate(BaseModel):
    token: str


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    body: str
    data: Optional[Any] = None
    is_read: bool
    created_at: datetime


# CV apply profile

class CVApplyWorkExperienceRequest(BaseModel):
    company_name: str
    job_title: str
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current: bool = False
    description: Optional[str] = None


class CVApplyEducationRequest(BaseModel):
    institution_name: str
    degree: str
    field_of_study: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current: bool = False
    grade: Optional[str] = None


class CVApplyRequest(BaseModel):
    apply_bio: bool = True
    apply_skills: bool = True
    apply_work_experience: bool = True
    apply_education: bool = True

    suggested_bio: Optional[str] = None
    skills: List[str] = []
    work_experience: List[CVApplyWorkExperienceRequest] = []
    education: List[CVApplyEducationRequest] = []
