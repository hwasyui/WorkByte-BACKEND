from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import asyncio
import sys, os, uvicorn, json, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger
from functions.db_manager import init_db, close_db
from functions.minio_client import ensure_buckets
from functions.response_utils import ResponseSchema
from ai_related.job_engine.embedding_manager import _should_embed_immediately
from routes.auth_router import auth_router
from routes.oauth.oauth_routes import oauth_router
from ai_related.job_engine.job_engine_routes import router as job_engine_router
from ai_related.job_engine.sweep_worker import embedding_sweep_loop
from routes.users.users_routes import users_router
from routes.freelancers.freelancer_routes import freelancer_router
from routes.clients.client_routes import client_router
from routes.skills.skill_routes import skill_router
from routes.freelancer_skills.freelancer_skill_routes import freelancer_skill_router
from routes.work_experience.work_experience_routes import work_experience_router
from routes.education.education_routes import education_router
from routes.job_posts.job_post_routes import job_post_router
from routes.job_roles.job_role_routes import job_role_router
from routes.job_role_skills.job_role_skill_routes import job_role_skill_router
from routes.job_files.job_file_routes import job_file_router
from routes.proposals.proposal_routes import proposal_router
from routes.proposal_files.proposal_file_routes import proposal_file_router
from routes.contracts.contract_routes import contract_router
from routes.portfolio.portfolio_routes import portfolio_router
from routes.saved_jobs.saved_job_routes import saved_job_router
from routes.ratings.rating_routes import rating_router
from routes.performance_ratings.performance_rating_routes import performance_rating_router
from routes.client_trust_scores.client_trust_score_routes import client_trust_score_router
from routes.dm.dm_routes import dm_router
from routes.upload.upload_route import upload_router, files_router
from routes.cv_upload.cv_upload_routes import cv_upload_router
from routes.contract_submissions.contract_submission_routes import contract_submission_router
from routes.reviews.review_routes import review_router
from routes.dashboard.dashboard_routes import dashboard_router
from ai_related.cv_analysis.cv_analysis_routes import cv_analysis_router
from ai_related.harmful_text_detection.harmful_text_routes import harmful_text_router
from routes.admin.admin_routes import admin_router, reports_router, appeals_router
from routes.notifications.notification_routes import notification_router
from routes.share.share_routes import share_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup (database init, model warmup, sweep worker) and shutdown."""
    # Startup: Initialize database
    try:
        init_db()
        logger("LIFESPAN", "Application startup complete - database initialized", level="INFO")
    except Exception as e:
        logger("LIFESPAN", f"Failed to initialize database on startup: {str(e)}", level="ERROR")
        raise

    try:
        ensure_buckets()
        logger("LIFESPAN", "MinIO buckets ensured", level="INFO")
    except Exception as e:
        logger("LIFESPAN", f"MinIO bucket setup failed (non-fatal): {e}", level="WARNING")

    immediate = _should_embed_immediately()
    logger(
        "LIFESPAN",
        f"Embedding mode: {'immediate: embeddings fire on each mutation (below threshold)' if immediate else 'sweep: dirty records processed by background worker (above threshold)'}",
        level="INFO",
    )

    sweep_task = asyncio.create_task(embedding_sweep_loop())
    logger("LIFESPAN", "Embedding sweep worker started (handles dirty records and manual /embed/ calls regardless of mode)", level="INFO")

    def _warmup_harmful_text():
        try:
            from ai_related.harmful_text_detection.model_inference import load_model
            load_model("best")
            logger("LIFESPAN", "Harmful text model warmed up (roberta)", level="INFO")
        except Exception as e:
            logger("LIFESPAN", f"Harmful text model warm-up failed (non-fatal): {e}", level="WARNING")

    def _warmup_embedding():
        try:
            from ai_related.job_engine.embedding_service import _get_model
            _get_model()
            logger("LIFESPAN", "Embedding model warmed up (nomic-ai/nomic-embed-text-v1.5)", level="INFO")
        except Exception as e:
            logger("LIFESPAN", f"Embedding model warm-up failed (non-fatal): {e}", level="WARNING")

    def _warmup_scam_detector():
        try:
            from ai_related.job_scam_detection.scam_detector import _load_models
            _load_models()
            logger("LIFESPAN", "Scam detector warmed up (SBERT + Random Forest)", level="INFO")
        except Exception as e:
            logger("LIFESPAN", f"Scam detector warm-up failed (non-fatal): {e}", level="WARNING")

    asyncio.create_task(asyncio.to_thread(_warmup_harmful_text))
    asyncio.create_task(asyncio.to_thread(_warmup_embedding))
    asyncio.create_task(asyncio.to_thread(_warmup_scam_detector))

    yield

    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        pass
    logger("LIFESPAN", "Embedding sweep worker stopped", level="INFO")
    
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")
if os.path.isdir(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# Include all routers
app.include_router(auth_router)
app.include_router(oauth_router)

app.include_router(users_router)
app.include_router(freelancer_router)
app.include_router(client_router)
app.include_router(skill_router)
app.include_router(freelancer_skill_router)
app.include_router(work_experience_router)
app.include_router(education_router)
app.include_router(job_post_router)
app.include_router(job_role_router)
app.include_router(job_role_skill_router)
app.include_router(job_file_router)
app.include_router(proposal_router)
app.include_router(proposal_file_router)
app.include_router(contract_router)
app.include_router(portfolio_router)
app.include_router(saved_job_router)
app.include_router(rating_router)
app.include_router(performance_rating_router)
app.include_router(client_trust_score_router)
app.include_router(dm_router)
app.include_router(upload_router)
app.include_router(files_router)
app.include_router(cv_upload_router)
app.include_router(cv_analysis_router)
app.include_router(contract_submission_router)
app.include_router(review_router)
app.include_router(dashboard_router)
app.include_router(job_engine_router)
app.include_router(harmful_text_router)
app.include_router(admin_router)
app.include_router(reports_router)
app.include_router(appeals_router)
app.include_router(notification_router)
app.include_router(share_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return a structured 422 response for Pydantic validation errors."""
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, workers=1)

