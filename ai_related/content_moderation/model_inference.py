"""Inference module for content moderation."""

import json
import os
from typing import Dict, List, Tuple

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from ai_related.content_moderation.preprocessing import TextPreprocessor


LABEL_SCHEMA = {
    "toxicity": 0,
    "severe_toxicity": 1,
    "obscene": 2,
    "threat": 3,
    "insult": 4,
    "identity_hate": 5,
}
REVERSE_LABEL_SCHEMA = {v: k for k, v in LABEL_SCHEMA.items()}

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_model = None
_tokenizer = None
_device = None
_model_name = None
_preprocessor = TextPreprocessor()

_MODEL_FOLDERS = {
    "bert": "bert",
    "roberta": "roberta",
    "distilbert": "distilbert",
}
_DEFAULT_MODEL_TYPE = "roberta"


def _get_device():
    """Get torch device."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _normalize_model_type(model_type: str = "best") -> str:
    """Resolve public model aliases to uploaded model folders."""
    requested_model = (model_type or "best").lower()
    if requested_model == "best":
        return _DEFAULT_MODEL_TYPE
    if requested_model not in _MODEL_FOLDERS:
        valid_models = ", ".join(["best", *_MODEL_FOLDERS.keys()])
        raise ValueError(f"Unsupported model_type '{model_type}'. Use one of: {valid_models}")
    return requested_model


def _model_path_for(model_type: str) -> str:
    """Return the local Hugging Face model directory for a model type."""
    resolved_model = _normalize_model_type(model_type)
    return os.path.join(_MODEL_DIR, _MODEL_FOLDERS[resolved_model])


def get_available_models() -> List[Dict[str, object]]:
    """Get uploaded model folders that can be loaded for inference."""
    available_models = []
    for model_type, folder in _MODEL_FOLDERS.items():
        model_path = os.path.join(_MODEL_DIR, folder)
        required_files = ["config.json", "model.safetensors", "tokenizer.json"]
        is_available = os.path.isdir(model_path) and all(
            os.path.exists(os.path.join(model_path, filename))
            for filename in required_files
        )

        info: Dict[str, object] = {
            "type": model_type,
            "folder": folder,
            "available": is_available,
            "default": model_type == _DEFAULT_MODEL_TYPE,
        }

        metrics_path = os.path.join(model_path, "metrics.json")
        if os.path.exists(metrics_path):
            with open(metrics_path, "r", encoding="utf-8") as metrics_file:
                metrics = json.load(metrics_file)
            info["model_name"] = metrics.get("model_name")
            info["test_metrics"] = metrics.get("test_metrics")

        available_models.append(info)

    return available_models


def load_model(model_type: str = "best") -> Tuple[torch.nn.Module, AutoTokenizer, torch.device, str]:
    """
    Load a trained content moderation model.

    Args:
        model_type: Type of model ('bert', 'roberta', 'distilbert', or 'best')
                    'best' loads RoBERTa

    Returns:
        Tuple of (model, tokenizer, device, resolved_model_type)

    Raises:
        FileNotFoundError: If model not found
    """
    global _model, _tokenizer, _device, _model_name

    resolved_model = _normalize_model_type(model_type)

    if _model is not None and _model_name == resolved_model:
        return _model, _tokenizer, _device, resolved_model

    model_path = _model_path_for(resolved_model)

    if not os.path.isdir(model_path):
        raise FileNotFoundError(
            f"Model not found at {model_path}. "
            f"Please upload the trained Hugging Face model folder from TRAIN_MODEL.ipynb."
        )

    _device = _get_device()
    _tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    _model = AutoModelForSequenceClassification.from_pretrained(
        model_path,
        local_files_only=True,
    ).to(_device)
    _model.eval()
    _model_name = resolved_model

    print(f"Loaded {resolved_model} model from {model_path}")
    print(f"Device: {_device}")

    return _model, _tokenizer, _device, resolved_model


def predict(text: str, model_type: str = "best", threshold: float = 0.5) -> dict:
    """
    Predict harmful content labels for text.

    Args:
        text: Input text to classify
        model_type: Type of model to use
        threshold: Confidence threshold for label prediction

    Returns:
        Dictionary with:
        - text: Input text
        - labels: List of detected harmful labels
        - scores: Dictionary of label -> confidence score
        - is_harmful: Boolean indicating if any harmful content detected
    """
    return batch_predict([text], model_type=model_type, threshold=threshold)[0]


def batch_predict(texts: list, model_type: str = "best", threshold: float = 0.5) -> list:
    """
    Predict labels for multiple texts.

    Args:
        texts: List of input texts
        model_type: Type of model to use
        threshold: Confidence threshold for label prediction

    Returns:
        List of prediction dictionaries
    """
    model, tokenizer, device, resolved_model = load_model(model_type)

    cleaned_texts = [_preprocessor.clean_text(text) for text in texts]
    results = [
        _empty_prediction(text, cleaned_text, resolved_model, model_type)
        for text, cleaned_text in zip(texts, cleaned_texts)
    ]

    infer_indices = [
        index for index, cleaned_text in enumerate(cleaned_texts)
        if cleaned_text
    ]
    if not infer_indices:
        return results

    infer_texts = [cleaned_texts[index] for index in infer_indices]
    encoding = tokenizer(
        infer_texts,
        max_length=512,
        padding=True,
        truncation=True,
        return_tensors="pt",
    )

    model_inputs = {
        key: value.to(device)
        for key, value in encoding.items()
    }

    with torch.no_grad():
        outputs = model(**model_inputs)
        probabilities = torch.sigmoid(outputs.logits).cpu().numpy()

    for result_index, probability_row in zip(infer_indices, probabilities):
        results[result_index] = _format_prediction(
            texts[result_index],
            cleaned_texts[result_index],
            probability_row,
            threshold,
            resolved_model,
            model_type,
        )

    return results


def _empty_prediction(text: str, cleaned_text: str, resolved_model: str, requested_model: str) -> dict:
    """Build a clean response for blank/empty inputs after preprocessing."""
    return {
        "text": text,
        "cleaned_text": cleaned_text,
        "labels": [],
        "scores": {REVERSE_LABEL_SCHEMA[i]: 0.0 for i in range(len(LABEL_SCHEMA))},
        "is_harmful": False,
        "model": resolved_model,
        "requested_model": requested_model,
    }


def _format_prediction(
    text: str,
    cleaned_text: str,
    probabilities,
    threshold: float,
    resolved_model: str,
    requested_model: str,
) -> dict:
    """Convert model probabilities into the API response format."""
    label_scores = {}
    detected_labels = []

    for idx, probability in enumerate(probabilities):
        label_name = REVERSE_LABEL_SCHEMA[idx]
        score = float(probability)
        label_scores[label_name] = round(score, 4)

        if score >= threshold:
            detected_labels.append(label_name)

    return {
        "text": text,
        "cleaned_text": cleaned_text,
        "labels": detected_labels,
        "scores": label_scores,
        "is_harmful": bool(detected_labels),
        "model": resolved_model,
        "requested_model": requested_model,
    }
