"""Data loading and merging for content moderation datasets."""

import pandas as pd
import numpy as np
from typing import Tuple
from datasets import load_dataset


LABEL_SCHEMA = {
    "toxicity": 0,
    "severe_toxicity": 1,
    "obscene": 2,
    "threat": 3,
    "insult": 4,
    "identity_hate": 5,
}

REVERSE_LABEL_SCHEMA = {v: k for k, v in LABEL_SCHEMA.items()}


def load_jigsaw_dataset() -> pd.DataFrame:
    """
    Load Jigsaw Toxic Comment Classification dataset from Hugging Face.

    The dataset contains toxic comment classification with severity scores.
    We convert it to multi-label format using thresholds.

    Returns:
        DataFrame with columns: text, labels (list of label indices)
    """
    print("Loading Jigsaw Toxic Comment Classification dataset...")
    dataset = load_dataset("google/jigsaw_toxicity_pred")

    df_list = []
    for split in dataset.keys():
        split_df = dataset[split].to_pandas()
        df_list.append(split_df)

    df = pd.concat(df_list, ignore_index=True)

    if 'comment_text' in df.columns:
        df = df.rename(columns={'comment_text': 'text'})

    if 'text' not in df.columns:
        raise ValueError("Jigsaw dataset must have 'text' or 'comment_text' column")

    label_columns = ['toxicity', 'severe_toxicity', 'obscene', 'threat', 'insult', 'identity_hate']
    available_cols = [col for col in label_columns if col in df.columns]

    if not available_cols:
        raise ValueError(f"Expected label columns not found. Found: {df.columns.tolist()}")

    threshold = 0.5
    df['labels'] = df[available_cols].apply(
        lambda row: [LABEL_SCHEMA[col] for col in available_cols if row[col] >= threshold],
        axis=1
    )

    df['source'] = 'jigsaw'

    return df[['text', 'labels', 'source']].copy()


def load_ethos_dataset() -> pd.DataFrame:
    """
    Load ETHOS hate speech dataset from Hugging Face.

    ETHOS multi-label structure:
    - violence (0/1): incites violence
    - directed_vs_generalized (0/1): directed at person (1) vs group (0)
    - gender, race, national_origin, disability, religion, sexual_orientation (0/1)

    Maps to unified schema:
    - gender=1 → identity_hate
    - race=1 → identity_hate
    - religion=1 → identity_hate
    - sexual_orientation=1 → identity_hate
    - national_origin=1 → identity_hate
    - disability=1 → identity_hate
    - violence=1 → threat
    - Any hate speech detected → toxicity

    Returns:
        DataFrame with columns: text, labels (list of label indices)
    """
    print("Loading ETHOS hate speech dataset...")
    dataset = load_dataset("iamollas/ethos")

    df_list = []
    for split in dataset.keys():
        split_df = dataset[split].to_pandas()
        df_list.append(split_df)

    df = pd.concat(df_list, ignore_index=True)

    if 'comment' in df.columns:
        df = df.rename(columns={'comment': 'text'})

    if 'text' not in df.columns:
        raise ValueError("ETHOS dataset must have 'text' or 'comment' column")

    def map_ethos_labels(row):
        labels = set()

        # Check for identity-hate related labels
        identity_hate_columns = ['gender', 'race', 'national_origin', 'disability', 'religion', 'sexual_orientation']
        has_identity_hate = any(col in row and row[col] == 1 for col in identity_hate_columns)

        if has_identity_hate:
            labels.add(LABEL_SCHEMA['identity_hate'])
            labels.add(LABEL_SCHEMA['toxicity'])  # Any hate speech is toxic

        # Violence maps to threat
        if 'violence' in row and row['violence'] == 1:
            labels.add(LABEL_SCHEMA['threat'])
            labels.add(LABEL_SCHEMA['toxicity'])

        return sorted(list(labels))

    df['labels'] = df.apply(map_ethos_labels, axis=1)
    df['source'] = 'ethos'

    return df[['text', 'labels', 'source']].copy()


def merge_datasets(jigsaw_df: pd.DataFrame, ethos_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Jigsaw and ETHOS datasets into a unified multi-label format.

    Both datasets are mapped to the unified label schema (0-5).

    Args:
        jigsaw_df: DataFrame from load_jigsaw_dataset
        ethos_df: DataFrame from load_ethos_dataset

    Returns:
        Merged DataFrame with standardized columns
    """
    print(f"Merging datasets: Jigsaw ({len(jigsaw_df)} samples) + ETHOS ({len(ethos_df)} samples)")

    merged = pd.concat([jigsaw_df, ethos_df], ignore_index=True)

    merged = merged[merged['text'].notna() & (merged['text'].str.len() > 0)]

    merged['label_count'] = merged['labels'].apply(len)
    merged['has_labels'] = merged['label_count'] > 0

    print(f"Merged dataset: {len(merged)} samples")
    print(f"  Samples with at least one label: {merged['has_labels'].sum()}")
    print(f"  Samples without labels: {(~merged['has_labels']).sum()}")
    print(f"\nLabel distribution:")

    label_counts = {REVERSE_LABEL_SCHEMA[i]: 0 for i in range(len(LABEL_SCHEMA))}
    for labels_list in merged['labels']:
        for label_idx in labels_list:
            label_counts[REVERSE_LABEL_SCHEMA[label_idx]] += 1

    for label_name, count in sorted(label_counts.items()):
        percentage = (count / len(merged)) * 100 if len(merged) > 0 else 0
        print(f"  {label_name}: {count} ({percentage:.2f}%)")

    return merged


def prepare_datasets_for_training(
    df: pd.DataFrame,
    test_size: float = 0.15,
    val_size: float = 0.15,
    random_state: int = 42,
    remove_unlabeled: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Split dataset into train/val/test sets.

    Args:
        df: Merged dataset
        test_size: Fraction for test set
        val_size: Fraction for validation set (of remaining after test split)
        random_state: Random seed
        remove_unlabeled: Whether to filter out samples with no labels

    Returns:
        Tuple of (train_df, val_df, test_df)
    """
    from sklearn.model_selection import train_test_split

    if remove_unlabeled:
        df = df[df['has_labels']].copy()
        print(f"Removed unlabeled samples. Dataset size: {len(df)}")

    train_val, test = train_test_split(
        df,
        test_size=test_size,
        random_state=random_state,
        stratify=df['source'] if 'source' in df.columns else None,
    )

    val_ratio = val_size / (1 - test_size)
    train, val = train_test_split(
        train_val,
        test_size=val_ratio,
        random_state=random_state,
        stratify=train_val['source'] if 'source' in train_val.columns else None,
    )

    print(f"\nDataset split:")
    print(f"  Train: {len(train)} samples ({len(train)/len(df)*100:.1f}%)")
    print(f"  Val:   {len(val)} samples ({len(val)/len(df)*100:.1f}%)")
    print(f"  Test:  {len(test)} samples ({len(test)/len(df)*100:.1f}%)")

    return train, val, test


def load_and_merge_all_datasets(remove_unlabeled: bool = False) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Complete pipeline: load, merge, and split all datasets.

    Args:
        remove_unlabeled: Whether to filter out samples with no labels

    Returns:
        Tuple of (train_df, val_df, test_df)
    """
    jigsaw_df = load_jigsaw_dataset()
    ethos_df = load_ethos_dataset()
    merged_df = merge_datasets(jigsaw_df, ethos_df)

    train_df, val_df, test_df = prepare_datasets_for_training(
        merged_df,
        remove_unlabeled=remove_unlabeled,
    )

    return train_df, val_df, test_df
