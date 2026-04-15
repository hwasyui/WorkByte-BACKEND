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
    full_name: Optional[str] = None  # Required for freelancer or client
    company_name: Optional[str] = None  # Deprecated: use full_name for client as well

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
    full_name: Optional[str] = None
    bio: Optional[str] = None
    website_url: Optional[str] = None
    profile_picture_url: Optional[str] = None

class ClientUpdate(BaseModel):
    full_name: Optional[str] = None
    bio: Optional[str] = None
    website_url: Optional[str] = None
    profile_picture_url: Optional[str] = None

class ClientResponse(BaseModel):
    client_id: str
    user_id: str
    full_name: Optional[str] = None
    bio: Optional[str] = None
    website_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
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


# ==================== FREELANCER SKILLS ====================
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


# ==================== FREELANCER SPECIALITIES ====================
class FreelancerSpecialityCreate(BaseModel):
    freelancer_speciality_id: Optional[str] = None
    freelancer_id: str
    speciality_id: str
    is_primary: Optional[bool] = False

class FreelancerSpecialityUpdate(BaseModel):
    is_primary: Optional[bool] = None

class FreelancerSpecialityResponse(BaseModel):
    freelancer_speciality_id: str
    freelancer_id: str
    speciality_id: str
    is_primary: Optional[bool] = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== FREELANCER LANGUAGES ====================
class FreelancerLanguageCreate(BaseModel):
    freelancer_language_id: Optional[str] = None
    freelancer_id: str
    language_id: str
    proficiency_level: str  # basic, conversational, fluent, native

class FreelancerLanguageUpdate(BaseModel):
    proficiency_level: Optional[str] = None

class FreelancerLanguageResponse(BaseModel):
    freelancer_language_id: str
    freelancer_id: str
    language_id: str
    proficiency_level: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== WORK EXPERIENCE ====================
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

class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_current: Optional[bool] = None
    description: Optional[str] = None

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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== EDUCATION ====================
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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== JOB POSTS ====================
class JobPostCreate(BaseModel):
    job_post_id: Optional[str] = None
    client_id: str
    job_title: str
    job_description: str
    project_type: str  # individual, team
    project_scope: str  # small, medium, large
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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    posted_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== JOB ROLES ====================
class JobRoleCreate(BaseModel):
    job_role_id: Optional[str] = None
    job_post_id: str
    role_title: str
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = "USD"
    budget_type: str  # fixed, negotiable
    role_description: Optional[str] = None
    positions_available: Optional[int] = 1
    is_required: Optional[bool] = True
    display_order: Optional[int] = 0

class JobRoleUpdate(BaseModel):
    role_title: Optional[str] = None
    role_budget: Optional[float] = None
    budget_currency: Optional[str] = None
    budget_type: Optional[str] = None
    role_description: Optional[str] = None
    positions_available: Optional[int] = None
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


# ==================== JOB ROLE SKILLS ====================
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


# ==================== JOB FILES ====================
class JobFileCreate(BaseModel):
    job_file_id: Optional[str] = None
    job_post_id: str
    file_url: str
    file_type: str
    file_name: str
    file_size: Optional[int] = None

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


# ==================== PROPOSALS ====================
class ProposalCreate(BaseModel):
    proposal_id: Optional[str] = None
    job_post_id: str
    job_role_id: Optional[str] = None
    freelancer_id: str
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

    class Config:
        from_attributes = True


# ==================== PROPOSAL FILES ====================
class ProposalFileCreate(BaseModel):
    proposal_file_id: Optional[str] = None
    proposal_id: str
    file_url: str
    file_type: str
    file_name: str
    file_size: Optional[int] = None

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


# ==================== CONTRACTS ====================
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

    class Config:
        from_attributes = True


# ==================== CONTRACT GENERATION ====================
class ContractMilestoneTermCreate(BaseModel):
    milestone_title: str
    milestone_description: Optional[str] = None
    milestone_amount: float
    milestone_percentage: float
    milestone_order: int
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
    milestones: Optional[List[ContractMilestoneTermCreate]] = None


# ==================== CONTRACT MILESTONES ====================
class ContractMilestoneCreate(BaseModel):
    milestone_id: Optional[str] = None
    contract_id: str
    milestone_title: str
    milestone_description: Optional[str] = None
    milestone_amount: Optional[float] = None
    milestone_percentage: Optional[float] = 0.0
    milestone_order: Optional[int] = 0
    due_date: Optional[date] = None
    status: Optional[str] = "pending"

class ContractMilestoneUpdate(BaseModel):
    milestone_title: Optional[str] = None
    milestone_description: Optional[str] = None
    milestone_amount: Optional[float] = None
    milestone_percentage: Optional[float] = None
    milestone_order: Optional[int] = None
    due_date: Optional[date] = None
    status: Optional[str] = None
    client_approved: Optional[bool] = None
    payment_requested: Optional[bool] = None
    payment_released: Optional[bool] = None
    freelancer_confirmed_paid: Optional[bool] = None

class ContractMilestoneResponse(BaseModel):
    milestone_id: str
    contract_id: str
    milestone_title: str
    milestone_description: Optional[str] = None
    milestone_amount: Optional[float] = None
    milestone_percentage: Optional[float] = None
    milestone_order: Optional[int] = None
    due_date: Optional[date] = None
    status: str
    completed_at: Optional[datetime] = None
    paid_at: Optional[datetime] = None
    client_approved: Optional[bool] = False
    payment_requested: Optional[bool] = False
    payment_released: Optional[bool] = False
    freelancer_confirmed_paid: Optional[bool] = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== PORTFOLIO ====================
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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== SAVED JOBS ====================
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


# ==================== RATINGS ====================
class RatingCreate(BaseModel):
    rating_id: Optional[str] = None
    contract_id: str
    rater_id: str
    ratee_id: str
    rating_score: float
    rating_category: Optional[str] = None  # communication, quality, timeliness, professionalism
    review_text: Optional[str] = None

class RatingUpdate(BaseModel):
    rating_score: Optional[float] = None
    rating_category: Optional[str] = None
    review_text: Optional[str] = None

class RatingResponse(BaseModel):
    rating_id: str
    contract_id: str
    rater_id: str
    ratee_id: str
    rating_score: float
    rating_category: Optional[str] = None
    review_text: Optional[str] = None
    update_count: Optional[int] = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== PERFORMANCE RATINGS ====================
class PerformanceRatingCreate(BaseModel):
    performance_rating_id: Optional[str] = None
    freelancer_id: str
    total_contracts: Optional[int] = 0
    completed_contracts: Optional[int] = 0
    average_rating: Optional[float] = None
    on_time_delivery_rate: Optional[float] = None
    total_earnings: Optional[float] = 0
    customer_satisfaction_score: Optional[float] = None

class PerformanceRatingUpdate(BaseModel):
    total_contracts: Optional[int] = None
    completed_contracts: Optional[int] = None
    average_rating: Optional[float] = None
    on_time_delivery_rate: Optional[float] = None
    total_earnings: Optional[float] = None
    customer_satisfaction_score: Optional[float] = None

class PerformanceRatingResponse(BaseModel):
    performance_rating_id: str
    freelancer_id: str
    total_contracts: Optional[int] = 0
    completed_contracts: Optional[int] = 0
    average_rating: Optional[float] = None
    on_time_delivery_rate: Optional[float] = None
    total_earnings: Optional[float] = 0
    customer_satisfaction_score: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== CLIENT TRUST SCORE ====================
class ClientTrustScoreCreate(BaseModel):
    client_trust_score_id: Optional[str] = None
    client_id: str
    total_jobs_posted: Optional[int] = 0
    total_jobs_completed: Optional[int] = 0
    average_rating_received: Optional[float] = None
    payment_reliability_score: Optional[float] = None
    dispute_count: Optional[int] = 0
    trust_score: Optional[float] = None

class ClientTrustScoreUpdate(BaseModel):
    total_jobs_posted: Optional[int] = None
    total_jobs_completed: Optional[int] = None
    average_rating_received: Optional[float] = None
    payment_reliability_score: Optional[float] = None
    dispute_count: Optional[int] = None
    trust_score: Optional[float] = None

class ClientTrustScoreResponse(BaseModel):
    client_trust_score_id: str
    client_id: str
    total_jobs_posted: Optional[int] = 0
    total_jobs_completed: Optional[int] = 0
    average_rating_received: Optional[float] = None
    payment_reliability_score: Optional[float] = None
    dispute_count: Optional[int] = 0
    trust_score: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== FREELANCER EMBEDDINGS ====================
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


# ==================== JOB EMBEDDINGS ====================
class JobEmbeddingCreate(BaseModel):
    embedding_id: Optional[str] = None
    job_post_id: str
    embedding_vector: List[float]
    embedding_type: Optional[str] = None  # skill_based, description_based, etc
    last_updated: Optional[datetime] = None

class JobEmbeddingUpdate(BaseModel):
    embedding_vector: Optional[List[float]] = None
    embedding_type: Optional[str] = None

class JobEmbeddingResponse(BaseModel):
    embedding_id: str
    job_post_id: str
    embedding_vector: List[float]
    embedding_type: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== MESSAGES ====================
class MessageCreate(BaseModel):
    message_id: Optional[str] = None
    contract_id: Optional[str] = None
    sender_id: str
    receiver_id: str
    message_text: str
    is_read: Optional[bool] = False

class MessageUpdate(BaseModel):
    message_text: Optional[str] = None
    is_read: Optional[bool] = None

class MessageResponse(BaseModel):
    message_id: str
    contract_id: Optional[str] = None
    sender_id: str
    receiver_id: str
    message_text: str
    is_read: Optional[bool] = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== COMPREHENSIVE FREELANCER PROFILE ====================
class FreelancerSkillWithDetails(BaseModel):
    freelancer_skill_id: str
    skill: SkillResponse
    proficiency_level: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FreelancerSpecialityWithDetails(BaseModel):
    freelancer_speciality_id: str
    speciality: SpecialityResponse
    is_primary: bool
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FreelancerLanguageWithDetails(BaseModel):
    freelancer_language_id: str
    language: LanguageResponse
    proficiency_level: str
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class FreelancerProfileComplete(BaseModel):
    freelancer: FreelancerResponse
    skills: List[FreelancerSkillWithDetails] = []
    specialities: List[FreelancerSpecialityWithDetails] = []
    languages: List[FreelancerLanguageWithDetails] = []
    education: List[EducationResponse] = []
    work_experience: List[WorkExperienceResponse] = []
    portfolio: List[PortfolioResponse] = []
    ratings: List[RatingResponse] = []  # Ratings received by this freelancer
    total_ratings: Optional[int] = 0
    average_rating: Optional[float] = None

    class Config:
        from_attributes = True
