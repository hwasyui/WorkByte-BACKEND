"""Text preprocessing and tokenization for content moderation."""

import re
from typing import List
import numpy as np


class TextPreprocessor:
    """Text cleaning and normalization for content moderation."""

    @staticmethod
    def clean_text(text: str) -> str:
        """
        Clean and normalize text.

        Args:
            text: Raw text input

        Returns:
            Cleaned text
        """
        if not isinstance(text, str):
            return ""

        text = text.strip()

        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'www\.\S+', '', text)

        text = re.sub(r'<[^>]+>', '', text)

        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'#\w+', '', text)

        text = re.sub(r'[^\w\s.,!?-]', '', text)

        text = re.sub(r'\s+', ' ', text).strip()

        return text

    @staticmethod
    def normalize_whitespace(text: str) -> str:
        """Normalize whitespace."""
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def remove_extra_punctuation(text: str) -> str:
        """Remove excessive punctuation repetition (e.g., '!!!!' → '!')."""
        return re.sub(r'([.!?])\1{2,}', r'\1', text)


def create_label_array(label_indices: List[int], num_labels: int = 6) -> np.ndarray:
    """
    Convert label indices to multi-hot encoded array.

    Args:
        label_indices: List of label indices (0-5)
        num_labels: Total number of labels

    Returns:
        Multi-hot encoded array of shape (num_labels,)
    """
    array = np.zeros(num_labels, dtype=np.float32)
    for idx in label_indices:
        if 0 <= idx < num_labels:
            array[idx] = 1.0
    return array


def labels_to_indices(multi_hot_array: np.ndarray) -> List[int]:
    """
    Convert multi-hot encoded array back to label indices.

    Args:
        multi_hot_array: Multi-hot encoded array

    Returns:
        List of label indices where value is 1.0
    """
    return [int(idx) for idx, val in enumerate(multi_hot_array) if val > 0.5]
