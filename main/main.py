from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import sys, os, uvicorn, json, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.functions import get_table_testing
from functions.logger import logger
from functions.db_manager import init_db, close_db
from functions.response_utils import ResponseSchema
from routes.auth_router import auth_router
from routes.users.users_routes import users_router
from routes.freelancers.freelancer_routes import freelancer_router
from routes.clients.client_routes import client_router
from routes.skills.skill_routes import skill_router
from routes.languages.language_routes import language_router
from routes.specialities.speciality_routes import speciality_router
from routes.freelancer_skills.freelancer_skill_routes import freelancer_skill_router
from routes.freelancer_specialities.freelancer_speciality_routes import freelancer_speciality_router
from routes.freelancer_languages.freelancer_language_routes import freelancer_language_router
from routes.work_experience.work_experience_routes import work_experience_router
from routes.education.education_routes import education_router
from routes.job_posts.job_post_routes import job_post_router
from routes.job_roles.job_role_routes import job_role_router
from routes.job_role_skills.job_role_skill_routes import job_role_skill_router
from routes.job_files.job_file_routes import job_file_router
from routes.proposals.proposal_routes import proposal_router
from routes.proposal_files.proposal_file_routes import proposal_file_router
from routes.contracts.contract_routes import contract_router
from routes.contract_milestones.contract_milestone_routes import contract_milestone_router
from routes.portfolio.portfolio_routes import portfolio_router
from routes.saved_jobs.saved_job_routes import saved_job_router
from routes.ratings.rating_routes import rating_router
from routes.performance_ratings.performance_rating_routes import performance_rating_router
from routes.client_trust_scores.client_trust_score_routes import client_trust_score_router
from routes.freelancer_embeddings.freelancer_embedding_routes import freelancer_embedding_router
from routes.job_embeddings.job_embedding_routes import job_embedding_router
from routes.messages.message_routes import message_router
from routes.job_payments.job_payment_routes import job_payment_router, job_milestone_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan context manager for startup and shutdown events"""
    # Startup: Initialize database
    try:
        init_db()
        logger("LIFESPAN", "Application startup complete - database initialized", level="INFO")
    except Exception as e:
        logger("LIFESPAN", f"Failed to initialize database on startup: {str(e)}", level="ERROR")
        raise
    
    yield
    
    # Shutdown: Close database connections
    try:
        close_db()
        logger("LIFESPAN", "Application shutdown complete - database connections closed", level="INFO")
    except Exception as e:
        logger("LIFESPAN", f"Error during shutdown: {str(e)}", level="ERROR")


app = FastAPI(
    title="CAPSTONE - BACKEND API", 
    description="API for CAPSTONE project", 
    version="1.0",
    lifespan=lifespan
)

# Include all routers
app.include_router(auth_router)

# Users router are not included
# We are handling user authentication through the auth_router.
# app.include_router(users_router)

app.include_router(freelancer_router)
app.include_router(client_router)
app.include_router(skill_router)
app.include_router(language_router)
app.include_router(speciality_router)
app.include_router(freelancer_skill_router)
app.include_router(freelancer_speciality_router)
app.include_router(freelancer_language_router)
app.include_router(work_experience_router)
app.include_router(education_router)
app.include_router(job_post_router)
app.include_router(job_role_router)
app.include_router(job_role_skill_router)
app.include_router(job_file_router)
app.include_router(proposal_router)
app.include_router(proposal_file_router)
app.include_router(contract_router)
app.include_router(contract_milestone_router)
app.include_router(portfolio_router)
app.include_router(saved_job_router)
app.include_router(rating_router)
app.include_router(performance_rating_router)
app.include_router(client_trust_score_router)
app.include_router(freelancer_embedding_router)
app.include_router(job_embedding_router)
app.include_router(message_router)
app.include_router(job_payment_router)
app.include_router(job_milestone_router)


# Custom exception handler for validation errors
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for Pydantic validation errors
    Returns structured response with detailed error information
    """
    errors = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field_name = ".".join(str(x) for x in loc[1:]) if len(loc) > 1 else str(loc[-1]) if loc else "unknown"
        msg = error.get("msg", "Invalid value")
        error_type = error.get("type", "unknown")
        
        errors.append({
            "field": field_name,
            "message": msg,
            "type": error_type
        })
    
    error_details = {
        "validation_errors": errors,
        "error_count": len(errors)
    }
    
    logger("VALIDATION_ERROR", f"Validation error with {len(errors)} field(s)", level="WARNING")
    return ResponseSchema.validation_error(error_details, status_code=422)


@app.get("/testing_tables")
def get_testing_tables():
    try: 
        data = get_table_testing()
        return {"data": data}
    except Exception as e:
        logger("API", f"Error fetching testing tables: {str(e)}", level="ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error fetching testing tables")
        

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)

