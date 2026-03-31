from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import sys, os, uvicorn, json, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.functions import get_table_testing
from functions.logger import logger
from functions.db_manager import init_db, close_db
from routes.auth_router import auth_router
from routes.users.users_routes import users_router
from routes.freelancers.freelancer_routes import freelancer_router
from routes.clients.client_routes import client_router
from routes.skills.skill_routes import skill_router
from routes.languages.language_routes import language_router
from routes.specialities.speciality_routes import speciality_router


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
app.include_router(users_router)
app.include_router(freelancer_router)
app.include_router(client_router)
app.include_router(skill_router)
app.include_router(language_router)
app.include_router(speciality_router)

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

