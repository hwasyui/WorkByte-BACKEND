"""Training utilities for multi-label text classification models."""

import torch
import numpy as np
from sklearn.metrics import (
    hamming_loss,
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
    jaccard_score,
)


def compute_multilabel_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    """
    Compute multi-label classification metrics.

    Args:
        y_true: True labels (multi-hot encoded, shape: (n_samples, n_labels))
        y_pred: Predicted labels (multi-hot encoded, same shape)

    Returns:
        Dictionary with various metrics
    """
    binarized_pred = (y_pred > 0.5).astype(int)

    metrics = {
        "hamming_loss": hamming_loss(y_true, binarized_pred),
        "exact_match_accuracy": accuracy_score(y_true, binarized_pred),
        "jaccard": jaccard_score(y_true, binarized_pred, average='micro', zero_division=0),
    }

    metrics["precision_micro"] = precision_score(
        y_true, binarized_pred, average='micro', zero_division=0
    )
    metrics["recall_micro"] = recall_score(
        y_true, binarized_pred, average='micro', zero_division=0
    )
    metrics["f1_micro"] = f1_score(
        y_true, binarized_pred, average='micro', zero_division=0
    )

    metrics["precision_macro"] = precision_score(
        y_true, binarized_pred, average='macro', zero_division=0
    )
    metrics["recall_macro"] = recall_score(
        y_true, binarized_pred, average='macro', zero_division=0
    )
    metrics["f1_macro"] = f1_score(
        y_true, binarized_pred, average='macro', zero_division=0
    )

    return metrics


def get_device():
    """Get torch device (GPU if available, else CPU)."""
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


class FocalLoss(torch.nn.Module):
    """Focal loss for handling class imbalance in multi-label setting."""

    def __init__(self, alpha: float = 1.0, gamma: float = 2.0):
        super().__init__()
        self.alpha = alpha
        self.gamma = gamma

    def forward(self, inputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            inputs: Logits, shape (batch_size, num_labels)
            targets: Target labels, shape (batch_size, num_labels)

        Returns:
            Focal loss
        """
        bce_loss = torch.nn.functional.binary_cross_entropy_with_logits(
            inputs, targets, reduction='none'
        )

        pt = torch.exp(-bce_loss)
        focal_loss = self.alpha * (1 - pt) ** self.gamma * bce_loss

        return focal_loss.mean()


class WeightedBCELoss(torch.nn.Module):
    """Weighted BCE loss for multi-label classification."""

    def __init__(self, pos_weights: torch.Tensor = None):
        super().__init__()
        self.pos_weights = pos_weights

    def forward(self, inputs: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            inputs: Logits, shape (batch_size, num_labels)
            targets: Target labels, shape (batch_size, num_labels)

        Returns:
            Weighted BCE loss
        """
        if self.pos_weights is not None:
            return torch.nn.functional.binary_cross_entropy_with_logits(
                inputs, targets, pos_weight=self.pos_weights, reduction='mean'
            )
        else:
            return torch.nn.functional.binary_cross_entropy_with_logits(
                inputs, targets, reduction='mean'
            )
