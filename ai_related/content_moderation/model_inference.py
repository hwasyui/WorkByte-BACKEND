"""Inference module for content moderation."""

import os
import torch
import numpy as np
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import joblib

from ai_related.content_moderation.preprocessing import TextPreprocessor, labels_to_indices
from ai_related.content_moderation.data_loader import REVERSE_LABEL_SCHEMA


_MODEL_DIR = os.path.join(os.path.dirname(__file__), "machine_learning", "models")
_model = None
_tokenizer = None
_device = None
_model_name = None


def _get_device():
    """Get torch device."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def load_model(model_type: str = "bert"):
    """
    Load a trained content moderation model.

    Args:
        model_type: Type of model ('bert', 'roberta', 'distilbert', or 'best')
                    'best' loads the best performing model

    Returns:
        Tuple of (model, tokenizer, device)

    Raises:
        FileNotFoundError: If model not found
    """
    global _model, _tokenizer, _device, _model_name

    if _model is not None and _model_name == model_type:
        return _model, _tokenizer, _device

    model_mapping = {
        "bert": "content_moderation_bert.pt",
        "roberta": "content_moderation_roberta.pt",
        "distilbert": "content_moderation_distilbert.pt",
        "best": "content_moderation_best.pt",
    }

    model_file = model_mapping.get(model_type.lower(), model_mapping["best"])
    model_path = os.path.join(_MODEL_DIR, model_file)

    if not os.path.exists(model_path):
        raise FileNotFoundError(
            f"Model not found at {model_path}. "
            f"Please train a model using TRAIN_MODEL.ipynb first."
        )

    tokenizer_path = os.path.join(_MODEL_DIR, f"tokenizer_{model_type}.pt")
    config_path = os.path.join(_MODEL_DIR, f"model_config_{model_type}.pkl")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Model config not found at {config_path}")

    _device = _get_device()
    _model = torch.load(model_path, map_location=_device)
    _model.eval()

    config = joblib.load(config_path)
    model_name_id = config.get("model_name", "bert-base-uncased")
    _tokenizer = AutoTokenizer.from_pretrained(model_name_id)
    _model_name = model_type

    print(f"Loaded {model_type} model from {model_path}")
    print(f"Device: {_device}")

    return _model, _tokenizer, _device


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
    model, tokenizer, device = load_model(model_type)

    preprocessor = TextPreprocessor()
    cleaned_text = preprocessor.clean_text(text)

    if not cleaned_text:
        return {
            "text": text,
            "labels": [],
            "scores": {REVERSE_LABEL_SCHEMA[i]: 0.0 for i in range(6)},
            "is_harmful": False,
        }

    encoding = tokenizer(
        cleaned_text,
        max_length=512,
        padding="max_length",
        truncation=True,
        return_tensors="pt",
    )

    input_ids = encoding["input_ids"].to(device)
    attention_mask = encoding["attention_mask"].to(device)

    with torch.no_grad():
        outputs = model(input_ids=input_ids, attention_mask=attention_mask)
        logits = outputs.logits
        probabilities = torch.sigmoid(logits).cpu().numpy()[0]

    label_scores = {}
    detected_labels = []

    for idx, prob in enumerate(probabilities):
        label_name = REVERSE_LABEL_SCHEMA[idx]
        score = float(prob)
        label_scores[label_name] = round(score, 4)

        if score >= threshold:
            detected_labels.append(label_name)

    is_harmful = len(detected_labels) > 0

    return {
        "text": text,
        "cleaned_text": cleaned_text,
        "labels": detected_labels,
        "scores": label_scores,
        "is_harmful": is_harmful,
        "model": model_type,
    }


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
    return [predict(text, model_type=model_type, threshold=threshold) for text in texts]
