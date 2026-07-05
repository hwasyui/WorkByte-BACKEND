import os
import pickle
from typing import Dict, List, Optional, Tuple

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
_REQUIRED_MODEL_FILES = ["config.json", "model.safetensors", "tokenizer.json"]

# training used 128-token windows, so long text is chunked the same way at inference
_CHUNK_MAX_LENGTH = 128
_CHUNK_STRIDE = 20

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


def _get_device():
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _model_path_for(model_type: str) -> str:
    return os.path.join(_MODEL_DIR, _MODEL_FOLDERS[model_type])


def _is_model_available(model_type: str) -> bool:
    model_path = _model_path_for(model_type)
    return os.path.isdir(model_path) and all(
        os.path.exists(os.path.join(model_path, filename))
        for filename in _REQUIRED_MODEL_FILES
    )


def _load_config(model_type: str) -> dict:
    """load a model's config.pkl (best_thresholds, test_metrics, label schema)."""
    config_path = os.path.join(_model_path_for(model_type), "config.pkl")
    if not os.path.exists(config_path):
        return {}
    with open(config_path, "rb") as config_file:
        return pickle.load(config_file)


def _pick_best_model_type() -> str:
    """pick whichever uploaded model has the highest test f1, so retraining a
    better model just means uploading its folder, no code change needed."""
    best_type = None
    best_f1 = -1.0
    for model_type in _MODEL_FOLDERS:
        if not _is_model_available(model_type):
            continue
        f1 = _load_config(model_type).get("test_metrics", {}).get("f1", -1.0)
        if f1 > best_f1:
            best_f1 = f1
            best_type = model_type

    if best_type is None:
        raise FileNotFoundError(
            f"no trained model folders found in {_MODEL_DIR}. "
            f"upload a model folder (e.g. 'bert/') with config.json, model.safetensors, "
            f"tokenizer.json and config.pkl."
        )
    return best_type


def _normalize_model_type(model_type: str = "best") -> str:
    """resolve public model aliases to uploaded model folders."""
    requested_model = (model_type or "best").lower()
    if requested_model == "best":
        return _pick_best_model_type()
    if requested_model not in _MODEL_FOLDERS:
        valid_models = ", ".join(["best", *_MODEL_FOLDERS.keys()])
        raise ValueError(f"unsupported model_type '{model_type}'. use one of: {valid_models}")
    return requested_model


def get_available_models() -> List[Dict[str, object]]:
    """get uploaded model folders that can be loaded for inference."""
    try:
        best_type = _pick_best_model_type()
    except FileNotFoundError:
        best_type = None

    available_models = []
    for model_type, folder in _MODEL_FOLDERS.items():
        is_available = _is_model_available(model_type)
        info: Dict[str, object] = {
            "type": model_type,
            "folder": folder,
            "available": is_available,
            "default": model_type == best_type,
        }
        if is_available:
            config = _load_config(model_type)
            info["model_name"] = config.get("model_name")
            info["test_metrics"] = config.get("test_metrics")

        available_models.append(info)

    return available_models


def load_model(model_type: str = "best") -> Tuple[torch.nn.Module, AutoTokenizer, torch.device, str]:
    """
    load a trained harmful text detection model.

    args:
        model_type: 'bert', 'roberta', 'distilbert', or 'best' (highest test f1
                    among the uploaded model folders)

    returns:
        tuple of (model, tokenizer, device, resolved_model_type)
    """
    global _model, _tokenizer, _device, _model_name, _thresholds

    resolved_model = _normalize_model_type(model_type)

    if _model is not None and _model_name == resolved_model:
        return _model, _tokenizer, _device, resolved_model

    model_path = _model_path_for(resolved_model)
    missing = [f for f in _REQUIRED_MODEL_FILES if not os.path.exists(os.path.join(model_path, f))]
    if not os.path.isdir(model_path) or missing:
        raise FileNotFoundError(
            f"model files missing at {model_path}. "
            f"missing: {missing if missing else 'directory does not exist'}. "
            f"download models_export.zip from your training run, extract it, "
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
    _thresholds = _load_config(resolved_model).get("best_thresholds", {})

    return _model, _tokenizer, _device, resolved_model


def predict(text: str, model_type: str = "best", threshold: Optional[float] = None) -> dict:
    """
    predict harmful content labels for text.

    args:
        text: input text to classify
        model_type: type of model to use
        threshold: optional flat threshold override for all labels; if omitted,
                   each label uses the model's own tuned threshold

    returns dict with text, labels, scores, is_harmful.
    """
    return batch_predict([text], model_type=model_type, threshold=threshold)[0]


def batch_predict(texts: list, model_type: str = "best", threshold: Optional[float] = None) -> list:
    """predict labels for multiple texts. long texts are split into overlapping
    128-token chunks and a label is flagged if any chunk crosses its threshold."""
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
    chunk_to_text = encoding.pop("overflow_to_sample_mapping").tolist()

    model_inputs = {
        key: value.to(device)
        for key, value in encoding.items()
    }

    with torch.no_grad():
        outputs = model(**model_inputs)
        probabilities = torch.sigmoid(outputs.logits).cpu().numpy()

    # max-pool every chunk's probabilities back onto its source text
    pooled: Dict[int, List[float]] = {}
    for chunk_probs, sample_idx in zip(probabilities, chunk_to_text):
        text_index = infer_indices[sample_idx]
        if text_index not in pooled:
            pooled[text_index] = list(chunk_probs)
        else:
            pooled[text_index] = [max(a, b) for a, b in zip(pooled[text_index], chunk_probs)]

    for text_index, probability_row in pooled.items():
        results[text_index] = _format_prediction(
            texts[text_index],
            cleaned_texts[text_index],
            probability_row,
            threshold,
            resolved_model,
            model_type,
        )

    return results


def _empty_prediction(text: str, cleaned_text: str, resolved_model: str, requested_model: str) -> dict:
    """build a clean response for blank/empty inputs after preprocessing."""
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
    """convert pooled probabilities into the api response format, applying the
    per-label tuned threshold unless a flat override was given."""
    label_scores = {}
    detected_labels = []

    for idx, probability in enumerate(probabilities):
        label_name = REVERSE_LABEL_SCHEMA[idx]
        score = float(probability)
        label_scores[label_name] = round(score, 4)

        label_threshold = threshold if threshold is not None else _thresholds.get(label_name, 0.5)
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
