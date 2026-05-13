"""API routes for content moderation."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from pydantic import BaseModel

from functions.logger import logger
from functions.response_utils import ResponseSchema
from ai_related.content_moderation.model_inference import (
    batch_predict,
    get_available_models as get_model_registry,
    predict,
)

content_moderation_router = APIRouter(prefix="/content_moderation", tags=["Content Moderation"])


class TextInput(BaseModel):
    """Input for text moderation."""
    text: str


class BatchTextInput(BaseModel):
    """Input for batch text moderation."""
    texts: List[str]


class ModerationParams(BaseModel):
    """Parameters for moderation."""
    text: str = None
    texts: List[str] = None
    model_type: str = "best"
    threshold: float = 0.5


@content_moderation_router.post("/moderate", response_model=None)
async def moderate_text(input_data: TextInput, model_type: str = "best", threshold: float = 0.5) -> Dict[str, Any]:
    """
    Moderate a single text input.

    Args:
        input_data: TextInput containing the text to moderate
        model_type: Type of model to use ('best', 'bert', 'roberta', 'distilbert')
        threshold: Confidence threshold for label prediction (0.0-1.0)

    Returns:
        Moderation result with detected labels and scores
    """
    logger("CONTENT_MODERATION", f"Text moderation request | text_length={len(input_data.text)}", level="DEBUG")

    try:
        if not input_data.text or not input_data.text.strip():
            raise HTTPException(status_code=400, detail="Text input cannot be empty")

        if not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")

        result = predict(
            input_data.text,
            model_type=model_type,
            threshold=threshold,
        )

        logger(
            "CONTENT_MODERATION",
            f"Moderation completed | is_harmful={result['is_harmful']} | labels={result['labels']}",
            level="INFO",
        )

        return ResponseSchema.success(result)

    except HTTPException:
        raise
    except Exception as e:
        logger("CONTENT_MODERATION", f"Moderation failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Moderation failed: {str(e)}")


@content_moderation_router.post("/moderate_batch", response_model=None)
async def moderate_batch(input_data: BatchTextInput, model_type: str = "best", threshold: float = 0.5) -> Dict[str, Any]:
    """
    Moderate multiple texts in batch.

    Args:
        input_data: BatchTextInput containing list of texts
        model_type: Type of model to use
        threshold: Confidence threshold for label prediction

    Returns:
        List of moderation results
    """
    logger(
        "CONTENT_MODERATION",
        f"Batch moderation request | batch_size={len(input_data.texts)}",
        level="DEBUG",
    )

    try:
        if not input_data.texts or len(input_data.texts) == 0:
            raise HTTPException(status_code=400, detail="Texts list cannot be empty")

        if len(input_data.texts) > 100:
            raise HTTPException(
                status_code=400,
                detail="Batch size too large (max 100 texts)",
            )

        if not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")

        if any(not text or not text.strip() for text in input_data.texts):
            raise HTTPException(status_code=400, detail="Texts list cannot contain empty values")

        results = batch_predict(
            input_data.texts,
            model_type=model_type,
            threshold=threshold,
        )

        harmful_count = sum(1 for r in results if r["is_harmful"])
        logger(
            "CONTENT_MODERATION",
            f"Batch moderation completed | batch_size={len(results)} | harmful={harmful_count}",
            level="INFO",
        )

        return ResponseSchema.success({
            "results": results,
            "summary": {
                "total": len(results),
                "harmful": harmful_count,
                "clean": len(results) - harmful_count,
            },
        })

    except HTTPException:
        raise
    except Exception as e:
        logger("CONTENT_MODERATION", f"Batch moderation failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Batch moderation failed: {str(e)}")


@content_moderation_router.get("/labels", response_model=None)
async def get_labels() -> Dict[str, Any]:
    """
    Get list of harmful content labels that the model detects.

    Returns:
        Label information with descriptions
    """
    labels_info = {
        "0": {
            "name": "toxicity",
            "description": "General toxic/rude language",
        },
        "1": {
            "name": "severe_toxicity",
            "description": "Severe toxic content",
        },
        "2": {
            "name": "obscene",
            "description": "Obscene/profane language",
        },
        "3": {
            "name": "threat",
            "description": "Threats of violence or harm",
        },
        "4": {
            "name": "insult",
            "description": "Insulting/demeaning language",
        },
        "5": {
            "name": "identity_hate",
            "description": "Hate speech targeting identity (race, ethnicity, gender, religion, sexual orientation, national origin, disability)",
        },
    }

    return ResponseSchema.success(labels_info)


@content_moderation_router.get("/models", response_model=None)
async def get_available_models() -> Dict[str, Any]:
    """
    Get information about available models.

    Returns:
        Information about available trained models
    """
    available_models = [
        model_info
        for model_info in get_model_registry()
        if model_info["available"]
    ]

    if not available_models:
        return ResponseSchema.success({
            "available_models": [],
            "message": "No trained Hugging Face model folders found. Please upload the output folders from TRAIN_MODEL.ipynb.",
        })

    return ResponseSchema.success({
        "available_models": available_models,
        "default": "roberta",
        "aliases": {
            "best": "roberta",
        },
    })
