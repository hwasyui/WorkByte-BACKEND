"""Global Database Manager for application-wide database engine initialization"""

import os
from dotenv import load_dotenv
from functions.database import Database
from functions.logger import logger

load_dotenv()

# Global database instance - initialized once at app startup
db: Database = None


def init_db() -> Database:
    """Initialize the global database connection pool"""
    global db
    
    try:
        if db is None:
            db = Database(
                db_user=os.getenv("DB_USER", "capstone"),
                db_password=os.getenv("DB_PASSWORD", "capstone"),
                db_host=os.getenv("DB_HOST", "capstone-postgresql"),
                db_port=int(os.getenv("DB_PORT", 5432)),
                db_name=os.getenv("DB_NAME", "capstone")
            )
            logger("DB_MANAGER", "Global database engine initialized", level="INFO")
        return db
    except Exception as e:
        logger("DB_MANAGER", f"Failed to initialize global database: {str(e)}", level="ERROR")
        raise


def get_db() -> Database:
    """Get the global database instance"""
    global db
    if db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return db


def close_db():
    """Close all database connections"""
    global db
    try:
        if db is not None:
            if hasattr(db.engine, 'dispose'):
                db.engine.dispose()
            logger("DB_MANAGER", "Database connections closed", level="INFO")
            db = None
    except Exception as e:
        logger("DB_MANAGER", f"Error closing database: {str(e)}", level="ERROR")
