"""
Skill name normalization utility.
Ensures skill variations like "BACKEND", "backend", "Back End", "backend dev", "backend engineer"
are treated uniformly and intelligently.
"""

import re


# Abbreviation mappings: expand common abbreviations to canonical forms
ABBREVIATION_MAP = {
    "dev": "developer",
    "eng": "engineer",
    "mgmt": "management",
    "sr": "senior",
    "jr": "junior",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "ui": "user interface",
    "ux": "user experience",
    "db": "database",
    "api": "application programming interface",
    "devops": "dev ops",
    "qa": "quality assurance",
    "qe": "quality engineer",
}

# Semantic role/context removal: remove role descriptors to get core skill
# Maps patterns to their core skill name
ROLE_DESCRIPTORS = [
    r"\s+(engineer|engineering)$",  # "backend engineer" → "backend"
    r"\s+(developer|development)$",  # "backend developer" → "backend"
    r"\s+(dev)$",                     # "backend dev" → "backend"
    r"\s+(specialist)$",              # "backend specialist" → "backend"
    r"\s+(expert)$",                  # "backend expert" → "backend"
    r"\s+(architect|architecture)$",  # "backend architect" → "backend"
    r"^(senior|junior|sr|jr)\s+",    # "senior backend" → "backend"
    r"^(lead|tech lead)\s+",          # "lead backend" → "backend"
]


def expand_abbreviations(name: str) -> str:
    """
    Expand common abbreviations to their full forms.

    Examples:
        "backend dev" → "backend developer"
        "ml engineer" → "machine learning engineer"
        "sr backend" → "senior backend"
    """
    words = name.split()
    expanded = []

    for word in words:
        # Check if word is in abbreviation map (remove trailing punctuation first)
        clean_word = word.rstrip('.,;:!?')
        if clean_word in ABBREVIATION_MAP:
            expanded.append(ABBREVIATION_MAP[clean_word])
        else:
            expanded.append(word)

    return " ".join(expanded)


def remove_role_descriptors(name: str) -> str:
    """
    Remove role/context descriptors to get the core skill.

    By default, keeps role descriptors. Use this when you want to unify
    "Backend Engineer", "Backend Developer", "Backend Dev" → "Backend"

    Examples (when enabled):
        "backend engineer" → "backend"
        "senior backend" → "backend"
        "ml architect" → "ml"
    """
    result = name
    for pattern in ROLE_DESCRIPTORS:
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result.strip()


def normalize_skill_name(skill_name: str, remove_role_context: bool = False) -> str:
    """
    Normalize skill name for consistent matching and storage.

    Rules (always applied):
    - Strip leading/trailing whitespace
    - Convert to lowercase
    - Normalize spacing (multiple spaces → single space)

    Optional rules (when remove_role_context=True):
    - Expand abbreviations (dev → developer)
    - Remove role descriptors (engineer, developer, specialist)
      This unifies "Backend Engineer" and "Backend Developer" → "backend"

    Args:
        skill_name: The skill name to normalize
        remove_role_context: If True, removes role descriptors. Use for skill
                           deduplication (default: False for backward compatibility)

    Examples (remove_role_context=False):
        "BACKEND" → "backend"
        "Back End" → "back end"
        "Backend Engineer" → "backend engineer"
        "backend dev" → "backend developer"

    Examples (remove_role_context=True):
        "BACKEND" → "backend"
        "Backend Engineer" → "backend"
        "backend dev" → "backend"
        "Senior Backend Developer" → "backend"
    """
    if not skill_name:
        return ""

    # Step 1: Strip and lowercase
    normalized = skill_name.strip().lower()

    # Step 2: Expand abbreviations
    normalized = expand_abbreviations(normalized)

    # Step 3: Normalize spacing (collapse multiple spaces)
    normalized = re.sub(r'\s+', ' ', normalized)

    # Step 4: Optionally remove role context
    if remove_role_context:
        normalized = remove_role_descriptors(normalized)
        # Re-normalize spacing after role removal
        normalized = re.sub(r'\s+', ' ', normalized).strip()

    return normalized


def get_skill_name_for_db(skill_name: str) -> str:
    """
    Get normalized name for database storage.
    Uses remove_role_context=False by default (preserves role context).
    Use this when creating/searching skills in the DB.
    """
    return normalize_skill_name(skill_name, remove_role_context=False)


def skill_names_equal(name1: str, name2: str, strict: bool = True) -> bool:
    """
    Check if two skill names refer to the same skill.

    Args:
        name1, name2: Skill names to compare
        strict: If True, role context matters (default: backward compatible)
               If False, unifies role variations (Backend vs Backend Engineer)
    """
    if strict:
        # Old behavior: "backend" != "backend engineer"
        return normalize_skill_name(name1) == normalize_skill_name(name2)
    else:
        # New behavior: unify role variations
        return (
            normalize_skill_name(name1, remove_role_context=True) ==
            normalize_skill_name(name2, remove_role_context=True)
        )
