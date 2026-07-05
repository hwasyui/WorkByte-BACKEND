import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from functions.logger import logger
from functions.response_utils import ResponseSchema
from ai_related.harmful_text_detection.model_inference import (
    batch_predict,
    get_available_models as get_model_registry,
    predict,
)

harmful_text_router = APIRouter(prefix="/harmful-text", tags=["Harmful Text Detection"])

class TextInput(BaseModel):
    text: str

class BatchTextInput(BaseModel):
    texts: List[str]

@harmful_text_router.post("/detect", response_model=None)
async def detect_harmful_text(input_data: TextInput, model_type: str = "best", threshold: Optional[float] = None) -> Dict[str, Any]:
    """detect harmful content in a single text. returns scores for 5 harm labels.
    threshold is an optional override; by default each label uses its own tuned threshold."""
    logger("HARMFUL_TEXT", f"Detect request | text_length={len(input_data.text)}", level="DEBUG")
    try:
        if not input_data.text or not input_data.text.strip():
            raise HTTPException(status_code=400, detail="Text input cannot be empty")
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")

        result = predict(input_data.text, model_type=model_type, threshold=threshold)

        logger("HARMFUL_TEXT", f"Detection complete | is_harmful={result['is_harmful']} | labels={result['labels']}", level="INFO")
        return ResponseSchema.success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger("HARMFUL_TEXT", f"Detection failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Harmful text detection failed: {str(e)}")

@harmful_text_router.post("/detect-batch", response_model=None)
async def detect_harmful_text_batch(input_data: BatchTextInput, model_type: str = "best", threshold: Optional[float] = None) -> Dict[str, Any]:
    """detect harmful content in multiple texts. max 100 per batch."""
    logger("HARMFUL_TEXT", f"Batch detect request | batch_size={len(input_data.texts)}", level="DEBUG")
    try:
        if not input_data.texts:
            raise HTTPException(status_code=400, detail="Texts list cannot be empty")
        if len(input_data.texts) > 100:
            raise HTTPException(status_code=400, detail="Batch size too large (max 100 texts)")
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise HTTPException(status_code=400, detail="Threshold must be between 0.0 and 1.0")
        if any(not text or not text.strip() for text in input_data.texts):
            raise HTTPException(status_code=400, detail="Texts list cannot contain empty values")

        results = batch_predict(input_data.texts, model_type=model_type, threshold=threshold)

        harmful_count = sum(1 for r in results if r["is_harmful"])
        logger("HARMFUL_TEXT", f"Batch complete | batch_size={len(results)} | harmful={harmful_count}", level="INFO")
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
        logger("HARMFUL_TEXT", f"Batch detection failed: {str(e)}", level="ERROR")
        raise HTTPException(status_code=500, detail=f"Harmful text batch detection failed: {str(e)}")

@harmful_text_router.get("/labels", response_model=None)
async def get_labels() -> Dict[str, Any]:
    """return the 5 harm labels the model detects."""
    labels_info = {
        "0": {"name": "toxicity",      "description": "General toxic/rude language (includes profanity)"},
        "1": {"name": "obscene",       "description": "Obscene/profane language"},
        "2": {"name": "threat",        "description": "Threats of violence or harm"},
        "3": {"name": "insult",        "description": "Insulting/demeaning language"},
        "4": {"name": "identity_hate", "description": "Hate speech targeting identity (race, ethnicity, gender, religion, sexual orientation, national origin, disability)"},
    }
    return ResponseSchema.success(labels_info)

@harmful_text_router.get("/models", response_model=None)
async def get_available_models() -> Dict[str, Any]:
    """return info about available trained models and which one 'best' resolves to."""
    available_models = [m for m in get_model_registry() if m["available"]]
    if not available_models:
        return ResponseSchema.success({
            "available_models": [],
            "message": "No trained model folders found. Upload the output folders from TRAIN_MODEL.ipynb.",
        })

    default_model = next((m["type"] for m in available_models if m.get("default")), available_models[0]["type"])
    return ResponseSchema.success({
        "available_models": available_models,
        "default": default_model,
        "aliases": {"best": default_model},
    })
