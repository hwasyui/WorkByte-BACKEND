"""
Response utility module for standardized API responses
"""
from fastapi.responses import JSONResponse
from typing import Any, Optional, Dict
import json

class ResponseSchema:
    """Standardized response schemas for API"""
    
    @staticmethod
    def success(details: Any = None, status_code: int = 200) -> JSONResponse:
        """
        Success response with optional data
        
        Args:
            details: Can be a string message or dict/object with data
            status_code: HTTP status code (200, 201, 204, etc.)
        """
        if status_code == 204:
            # No content response
            return JSONResponse(status_code=204, content=None)
        
        # If details is a dict but empty, return as message string
        if isinstance(details, dict):
            body = {
                "status": "success",
                "details": details
            }
        elif isinstance(details, str):
            body = {
                "status": "success",
                "details": details
            }
        else:
            # Try to convert to dict if it's a Pydantic model or similar
            if hasattr(details, 'model_dump'):
                body = {
                    "status": "success",
                    "details": details.model_dump()
                }
            elif hasattr(details, 'dict'):
                body = {
                    "status": "success",
                    "details": details.dict()
                }
            else:
                # Fallback: convert to dict
                try:
                    body = {
                        "status": "success",
                        "details": json.loads(json.dumps(details, default=str))
                    }
                except:
                    body = {
                        "status": "success",
                        "details": str(details)
                    }
        
        return JSONResponse(status_code=status_code, content=body)
    
    @staticmethod
    def error(details: str, status_code: int = 400) -> JSONResponse:
        """
        Error response
        
        Args:
            details: Error message describing what went wrong
            status_code: HTTP status code (400, 404, 500, etc.)
        """
        body = {
            "status": "error",
            "details": details
        }
        return JSONResponse(status_code=status_code, content=body)
    
    @staticmethod
    def validation_error(details: Any, status_code: int = 422) -> JSONResponse:
        """
        Validation error response with detailed information
        
        Args:
            details: Can be a string or dict with error information
            status_code: HTTP status code (typically 422)
        """
        if isinstance(details, list):
            # Convert pydantic errors to readable format
            error_details = {}
            for error in details:
                field = ".".join(str(x) for x in error.get("loc", [])[1:])  # Skip "body"
                msg = error.get("msg", "Invalid value")
                error_type = error.get("type", "unknown")
                error_details[field] = f"{msg} (type: {error_type})"
            details = error_details
        elif isinstance(details, str):
            details = {"error": details}
        
        body = {
            "status": "error",
            "details": details
        }
        return JSONResponse(status_code=status_code, content=body)
