"""Helpers for building standardized API responses."""

from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from typing import Any
from uuid import UUID
from datetime import date, datetime
from decimal import Decimal


class ResponseSchema:

    @staticmethod
    def success(details: Any = None, status_code: int = 200) -> JSONResponse:
        """Build a success response. details can be a string, dict, or Pydantic model."""

        if status_code == 204:
            return JSONResponse(status_code=204, content=None)

        body = {
            "status": "success",
            "details": details
        }

        # Convert everything into JSON-safe format
        encoded_body = jsonable_encoder(
            body,
            custom_encoder={
                date: lambda v: v.isoformat(),
                datetime: lambda v: v.isoformat(),
                UUID: str,
                Decimal: float,
            }
        )

        return JSONResponse(status_code=status_code, content=encoded_body)

    @staticmethod
    def error(details: str, status_code: int = 400) -> JSONResponse:
        """Build an error response."""
        body = {
            "status": "error",
            "details": details
        }

        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body)
        )

    @staticmethod
    def validation_error(details: Any, status_code: int = 422) -> JSONResponse:
        """Build a 422 validation error response."""

        if isinstance(details, list):
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

        return JSONResponse(
            status_code=status_code,
            content=jsonable_encoder(body)
        )