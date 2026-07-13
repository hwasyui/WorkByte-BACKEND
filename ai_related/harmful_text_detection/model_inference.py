import json
import os
import pickle
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from ai_related.harmful_text_detection.preprocessing import TextPreprocessor


LABEL_SCHEMA = {
    "toxicity": 0,
    "obscene": 1,
    "threat": 2,
    "insult": 3,
    "identity_hate": 4,
}
REVERSE_LABEL_SCHEMA = {v: k for k, v in LABEL_SCHEMA.items()}

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_model = None
_tokenizer = None
_device = None
_model_name = None
_thresholds: Dict[str, float] = {}
_preprocessor = TextPreprocessor()

_MODEL_FOLDERS = {
    "bert": "bert",
    "roberta": "roberta",
    "distilbert": "distilbert",
}
_DEFAULT_MODEL_TYPE = "bert"
_DEFAULT_FLAT_THRESHOLD = 0.5  # fallback when config.pkl's tuned thresholds aren't available

# Chunking: matches the training-time window (see harmful_text.md Section 3/9). A single
# truncation pass silently drops anything past this many tokens; chunking instead scores every
# overlapping window and max-pools each label's probability, so a harmful phrase deep in a long
# text is still caught rather than truncated away.
_CHUNK_MAX_LENGTH = 128
_CHUNK_STRIDE = 20


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


def _load_thresholds(model_path: str) -> Dict[str, float]:
    """
    Load this model's per-label tuned thresholds from config.pkl's best_thresholds.
    Falls back to a flat _DEFAULT_FLAT_THRESHOLD for every label if config.pkl is
    missing or malformed, so a model folder without it still serves rather than failing.
    """
    config_path = os.path.join(model_path, "config.pkl")
    if not os.path.exists(config_path):
        return {label: _DEFAULT_FLAT_THRESHOLD for label in LABEL_SCHEMA}
    try:
        with open(config_path, "rb") as config_file:
            config = pickle.load(config_file)
        best_thresholds = config.get("best_thresholds", {})
        return {
            label: float(best_thresholds.get(label, _DEFAULT_FLAT_THRESHOLD))
            for label in LABEL_SCHEMA
        }
    except Exception:
        return {label: _DEFAULT_FLAT_THRESHOLD for label in LABEL_SCHEMA}


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
    Load a trained harmful text detection model.

    Args:
        model_type: Type of model ('bert', 'roberta', 'distilbert', or 'best')
                    'best' loads BERT

    Returns:
        Tuple of (model, tokenizer, device, resolved_model_type)

    Raises:
        FileNotFoundError: If model not found.
    """
    global _model, _tokenizer, _device, _model_name, _thresholds

    resolved_model = _normalize_model_type(model_type)

    if _model is not None and _model_name == resolved_model:
        return _model, _tokenizer, _device, resolved_model

    model_path = _model_path_for(resolved_model)

    required_files = ["config.json", "model.safetensors", "tokenizer.json"]
    missing = [f for f in required_files if not os.path.exists(os.path.join(model_path, f))]
    if not os.path.isdir(model_path) or missing:
        raise FileNotFoundError(
            f"Model files missing at {model_path}. "
            f"Missing: {missing if missing else 'directory does not exist'}. "
            f"Download models_export.zip from your Colab training run, extract it, "
            f"and copy the '{resolved_model}/' folder into machine_learning/models/."
        )

    _device = _get_device()
    _tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    _model = AutoModelForSequenceClassification.from_pretrained(
        model_path,
        local_files_only=True,
    ).to(_device)
    _model.eval()
    _model_name = resolved_model
    _thresholds = _load_thresholds(model_path)

    return _model, _tokenizer, _device, resolved_model


def predict(text: str, model_type: str = "best", threshold: Optional[float] = None) -> dict:
    """
    Predict harmful content labels for text.

    Args:
        text: Input text to classify
        model_type: Type of model to use
        threshold: Flat override applied to all 5 labels. Leave as None (the default)
                    to use this model's own tuned per-label thresholds instead.

    Returns:
        Dictionary with:
        - text: Input text
        - labels: List of detected harmful labels
        - scores: Dictionary of label -> confidence score
        - is_harmful: Boolean indicating if any harmful content detected.
    """
    return batch_predict([text], model_type=model_type, threshold=threshold)[0]


def batch_predict(texts: list, model_type: str = "best", threshold: Optional[float] = None) -> list:
    """
    Predict labels for multiple texts.

    Long texts are split into overlapping _CHUNK_MAX_LENGTH-token windows (stride
    _CHUNK_STRIDE) rather than truncated, and each label's probability is max-pooled
    across every chunk of a text, so a harmful phrase anywhere in a long passage is
    still caught.

    Args:
        texts: List of input texts
        model_type: Type of model to use
        threshold: Flat override applied to all 5 labels. Leave as None (the default)
                    to use this model's own tuned per-label thresholds instead.

    Returns:
        List of prediction dictionaries.
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
        max_length=_CHUNK_MAX_LENGTH,
        stride=_CHUNK_STRIDE,
        truncation=True,
        padding=True,
        return_overflowing_tokens=True,
        return_tensors="pt",
    )

    sample_mapping = encoding.pop("overflow_to_sample_mapping")
    sample_mapping = sample_mapping.tolist() if hasattr(sample_mapping, "tolist") else list(sample_mapping)

    model_inputs = {
        key: value.to(device)
        for key, value in encoding.items()
    }

    with torch.no_grad():
        outputs = model(**model_inputs)
        chunk_probabilities = torch.sigmoid(outputs.logits).cpu().numpy()

    # Max-pool each label's probability across every chunk belonging to the same text --
    # if any window looks harmful, the whole text is harmful.
    pooled: Dict[int, np.ndarray] = {}
    for chunk_idx, sample_idx in enumerate(sample_mapping):
        sample_idx = int(sample_idx)
        if sample_idx not in pooled:
            pooled[sample_idx] = chunk_probabilities[chunk_idx].copy()
        else:
            pooled[sample_idx] = np.maximum(pooled[sample_idx], chunk_probabilities[chunk_idx])

    for local_index, result_index in enumerate(infer_indices):
        results[result_index] = _format_prediction(
            texts[result_index],
            cleaned_texts[result_index],
            pooled[local_index],
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
    threshold: Optional[float],
    resolved_model: str,
    requested_model: str,
) -> dict:
    """
    Convert model probabilities into the API response format.
    Uses this model's tuned per-label thresholds (loaded from config.pkl) by default;
    an explicit `threshold` overrides all 5 labels with one flat number instead.
    """
    label_scores = {}
    detected_labels = []

    for idx, probability in enumerate(probabilities):
        label_name = REVERSE_LABEL_SCHEMA[idx]
        score = float(probability)
        label_scores[label_name] = round(score, 4)

        label_threshold = threshold if threshold is not None else _thresholds.get(label_name, _DEFAULT_FLAT_THRESHOLD)
        if score >= label_threshold:
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
