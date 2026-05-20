"""API routes for toxicity detection."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
from pydantic import BaseModel

from functions.logger import logger
from functions.response_utils import ResponseSchema
from ai_related.toxicity_detection.model_inference import (
    batch_predict,
    get_available_models as get_model_registry,
    predict,
)

toxicity_router = APIRouter(prefix="/toxicity", tags=["Toxicity Detection"])


class TextInput(BaseModel):
    text: str


class BatchTextInput(BaseModel):
    texts: List[str]


@toxicity_router.post("/detect", response_model=None)
async def detect_toxicity(input_data: TextInput, model_type: str = "best", threshold: float = 0.5) -> Dict[str, Any]:
    """Detect toxicity in a single text. Returns scores for 6 harm labels."""
    logger("TOXICITY", f"Detect request | text_length={len(input_data.text)}", level="DEBUG")
    try:
        if not input_data.text or not input_data.text.strip():
            raise HTTPException(status_code=400, detail="Text input cannot be empty")
        if not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")

        result = predict(input_data.text, model_type=model_type, threshold=threshold)

        logger("TOXICITY", f"Detection complete | is_harmful={result['is_harmful']} | labels={result['labels']}", level="INFO")
        return ResponseSchema.success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger("TOXICITY", f"Detection failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Toxicity detection failed: {str(e)}")


@toxicity_router.post("/detect-batch", response_model=None)
async def detect_toxicity_batch(input_data: BatchTextInput, model_type: str = "best", threshold: float = 0.5) -> Dict[str, Any]:
    """Detect toxicity in multiple texts. Max 100 per batch."""
    logger("TOXICITY", f"Batch detect request | batch_size={len(input_data.texts)}", level="DEBUG")
    try:
        if not input_data.texts:
            raise HTTPException(status_code=400, detail="Texts list cannot be empty")
        if len(input_data.texts) > 100:
            raise HTTPException(status_code=400, detail="Batch size too large (max 100 texts)")
        if not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")
        if any(not text or not text.strip() for text in input_data.texts):
            raise HTTPException(status_code=400, detail="Texts list cannot contain empty values")

        results = batch_predict(input_data.texts, model_type=model_type, threshold=threshold)

        harmful_count = sum(1 for r in results if r["is_harmful"])
        logger("TOXICITY", f"Batch complete | batch_size={len(results)} | harmful={harmful_count}", level="INFO")
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
        logger("TOXICITY", f"Batch detection failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Batch toxicity detection failed: {str(e)}")


@toxicity_router.get("/labels", response_model=None)
async def get_labels() -> Dict[str, Any]:
    """Return the 6 toxicity labels the model detects."""
    labels_info = {
        "0": {"name": "toxicity",        "description": "General toxic/rude language"},
        "1": {"name": "severe_toxicity", "description": "Severe toxic content"},
        "2": {"name": "obscene",         "description": "Obscene/profane language"},
        "3": {"name": "threat",          "description": "Threats of violence or harm"},
        "4": {"name": "insult",          "description": "Insulting/demeaning language"},
        "5": {"name": "identity_hate",   "description": "Hate speech targeting identity (race, ethnicity, gender, religion, sexual orientation, national origin, disability)"},
    }
    return ResponseSchema.success(labels_info)


@toxicity_router.get("/models", response_model=None)
async def get_available_models() -> Dict[str, Any]:
    """Return information about available trained toxicity detection models."""
    available_models = [m for m in get_model_registry() if m["available"]]
    if not available_models:
        return ResponseSchema.success({
            "available_models": [],
            "message": "No trained model folders found. Upload the output folders from TRAIN_MODEL.ipynb.",
        })
    return ResponseSchema.success({
        "available_models": available_models,
        "default": "roberta",
        "aliases": {"best": "roberta"},
    })
