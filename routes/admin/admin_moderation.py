"""
Keyword-based content moderation and scam detection.
Placeholder until the ML content_moderation model is integrated.

Content moderation labels (matching future ML output):
  toxic, severe_toxic, obscene, threat, insult, identity_hate
"""
import re
from typing import Dict, List

# Keywords grouped by moderation label
_LABEL_KEYWORDS: Dict[str, List[str]] = {
    "toxic": [
        "hate you", "you're stupid", "you are stupid", "idiot", "moron", "dumb",
        "shut up", "loser", "worthless", "pathetic", "retard", "freak", "you suck",
        "imbecile", "you're an idiot", "complete idiot",
    ],
    "severe_toxic": [
        "fuck", "fucking", "shit", "bitch", "asshole", "bastard", "cunt",
        "motherfucker", "piece of shit", "bullshit", "piss off", "go to hell",
    ],
    "obscene": [
        "porn", "pornography", "sex for money", "nude model", "naked photo",
        "xxx content", "adult entertainment", "escort service", "prostitut",
        "erotic service", "sexual favor", "only fans",
    ],
    "threat": [
        "i will kill", "kill you", "threaten you", "hurt you", "beat you",
        "destroy you", "come after you", "you will regret", "i'll find you",
        "watch your back", "you're dead", "i know where you live",
    ],
    "insult": [
        "disgusting", "you're trash", "garbage person", "you're a pig", "scum",
        "vermin", "parasite", "waste of space", "good for nothing", "you're nothing",
        "nobody wants you", "you're worthless",
    ],
    "identity_hate": [
        "racist", "racism", "sexist", "sexism", "homophob", "nazi",
        "white supremac", "ethnic cleansing", "religious hatred",
        "go back to your country", "your kind",
    ],
}

# Scam indicator keywords for job posts
_SCAM_KEYWORDS: List[str] = [
    "guaranteed income",
    "easy money",
    "get rich quick",
    "no experience needed earn",
    "unlimited earning",
    "upfront payment required",
    "pay to work",
    "investment required before",
    "send money first",
    "wire transfer payment",
    "pay via bitcoin",
    "gift card payment",
    "western union",
    "moneygram",
    "100% profit guaranteed",
    "risk free investment",
    "guaranteed results",
    "secret earning system",
    "pyramid scheme",
    "multi-level marketing",
    "mlm opportunity",
    "passive income guaranteed",
    "no skills required to earn",
    "double your money",
    "make money fast",
    "earn thousands weekly",
    "work from home easy money",
    "become rich overnight",
    "financial freedom in days",
    "zero risk high profit",
    "registration fee required",
    "deposit required to start",
    "pay for training",
]

# Score ≥ this triggers a flag for review (≈ 1 keyword hit → 16.7%)
SCAM_FLAG_THRESHOLD = 0.10
# Score ≥ this + 30 days → auto-remove
SCAM_AUTO_REMOVE_THRESHOLD = 0.85


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def scan_content(text: str) -> Dict:
    """
    Scan text for harmful content using keyword matching.
    Returns per-label scores (0.0–1.0) and list of triggered labels.
    Scoring: 1 hit → 0.4, 2 hits → 0.7, 3+ hits → 1.0.
    """
    normalized = _normalize(text)
    scores: Dict[str, float] = {}
    detected: List[str] = []

    for label, keywords in _LABEL_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in normalized)
        score = round(min(hits * 0.35, 1.0), 4)
        scores[label] = score
        if hits > 0:
            detected.append(label)

    return {
        "toxic_score":          scores["toxic"],
        "severe_toxic_score":   scores["severe_toxic"],
        "obscene_score":        scores["obscene"],
        "threat_score":         scores["threat"],
        "insult_score":         scores["insult"],
        "identity_hate_score":  scores["identity_hate"],
        "detected_labels":      detected,
        "is_flagged":           len(detected) > 0,
    }


def scan_for_scam(text: str) -> Dict:
    """
    Scan job post text for scam indicators.
    Score = matched_count / 6  (6 keywords ≈ 100%; 5 ≈ 83% → near auto-remove).
    """
    normalized = _normalize(text)
    matched = [kw for kw in _SCAM_KEYWORDS if kw in normalized]
    score = round(min(len(matched) / 6.0, 1.0), 4)
    return {
        "scam_score":        score,
        "detected_keywords": matched,
        "is_flagged":        score >= SCAM_FLAG_THRESHOLD,
    }
