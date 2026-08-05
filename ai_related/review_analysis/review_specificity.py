"""
Component 5: does the review describe THIS engagement, or could it be pasted onto
any job on the platform?

Why this exists
---------------
Measured against the live pipeline, four templated 5-star reviews - the classic
marketplace shapes ("A+++++ super fast delivery", "Excellent work. Very
professional and easy to work with", keyword-stuffed superlatives, and a bare
"Really good work, thanks!") - all published. Neither model objected:

  * The authenticity classifier scored them 0.011-0.064 raw P(fake), BELOW the
    0.080-0.119 it gave genuine reviews. Its ranking is inverted on this domain,
    which is why it has now been removed from the pipeline entirely.
  * The LLM's authenticity score landed at 0.48-0.72 against a PASS_THRESHOLD of
    0.42, so all four cleared the gate. On two of the four it did not even
    produce a flag reason.

Raising the threshold does not separate them: genuine reviews measured 0.68-0.94
and templated ones 0.48-0.72, so the distributions overlap. A prompt rubric helps
(and has been added) but it is still a judgement call the model can have a bad day
about, on the exact axis where being wrong is cheapest to exploit.

Why arithmetic instead of a model
---------------------------------
Same reasoning as review_consistency.py, which this deliberately mirrors. There is
no in-domain labelled corpus for "templated freelance review" and inventing one
would teach a model a fabricated correlation. There does not need to be: the
platform already knows what the project was about - job title, description, role
skills, the freelancer's own submission notes, the generated question - and a
review that shares no content word with ANY of that, while being short enough to
be a template, is boilerplate by construction. That is set intersection.

Deterministic also means a reviewer can be shown exactly why their review was
held, and the rule cannot drift between deployments.

Deliberate limits
-----------------
* Exact token match, no stemming. Stemming would raise the false-CLEAR rate
  ("delivery" in a template matching "delivered" in a submission note), and a
  false clear is the failure this exists to prevent.
* Words that are generic to every review on the platform are stripped from BOTH
  sides (_GENERIC_TERMS). Otherwise a job description containing "delivery" would
  clear a template whose only content word is "delivery".
* Reviews longer than MAX_TEMPLATE_WORDS are never called generic. The gate is a
  positive test for boilerplate, and boilerplate is short; a long review sharing
  no vocabulary is far more likely an unusual genuine account than a template.
  The cap bounds how much a purely lexical test can cost.

* Missing vocabulary is NOT sufficient on its own. An earlier version of this
  rule held a review on that alone and produced a false positive on the first
  genuine review it met: "Second project with him and the same experience: on
  time, clear updates twice a week, and he pushed back on a requirement that
  would have broken our returns flow. The migration ran on the first attempt in
  production." Fifty-two words, obviously written by someone who was there, and
  not one of them in the contract's vocabulary - because it describes the
  ENGAGEMENT (how often updates came, what got pushed back on) rather than the
  SUBJECT MATTER (stock, warehouse, reconciliation). That is a normal way to
  write a genuine review, and no lexical rule keyed on domain nouns can see it.

  So the missing vocabulary now has to be corroborated: the text must also either
  read as marketplace boilerplate (_BOILERPLATE_MARKERS) or be too short to carry
  any information at all (MIN_SUBSTANTIVE_WORDS). Both conditions only ever get
  evaluated on reviews that already matched nothing about the project, which is
  what keeps the marker list from mattering to normal reviews - a genuine review
  that says "highly recommended" AND names one project detail never reaches it.

Outcome, not verdict: a generic review is HELD for admin review (flagged), never
suppressed. The evidence is lexical, and lexical evidence is not strong enough to
write a review off.
"""
import re
from typing import Dict, Iterable, List, Optional, Set

# Longest review this rule will call templated. See the module docstring.
MAX_TEMPLATE_WORDS = 60

# At or below this, a review with no project vocabulary is held without needing a
# boilerplate marker: there is no length at which "Really good work, thanks!"
# conveys something only this client could know.
MIN_SUBSTANTIVE_WORDS = 10

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Stock marketplace phrasing. Only ever consulted for a review that already shares
# NO vocabulary with its project, so a genuine review that happens to say "highly
# recommended" alongside one real detail never gets here.
#
# Deliberately excludes the ambiguous warm phrases - "pleasure to work with",
# "easy to work with", "great communication" - which genuine reviewers write as
# often as templates do. The list is meant to be short and obviously-boilerplate;
# anything requiring an argument does not belong in it.
_BOILERPLATE_MARKERS = (
    "a+", "a++", "aaa+", "10/10", "100%",
    "5 stars", "5 star", "five stars", "five star",
    "highly recommend", "highly recommended",
    "recommend to everyone", "recommended to everyone", "recommend to anyone",
    "would hire again", "will hire again", "hire again",
    "would buy again", "will buy again", "order again",
    "would definitely", "will definitely", "definitely recommend",
    "exactly as described", "as described",
    "fast delivery", "fast shipping", "super fast", "very fast",
    "great seller", "good seller", "best seller",
    "best freelancer", "best developer", "best on the platform",
    "top notch", "thank you so much", "highly professional",
)

# Grammar. Carries no information about what a project was.
_STOPWORDS = {
    "a", "about", "after", "again", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "been", "before", "being", "but", "by", "can", "could", "did", "do",
    "does", "doing", "done", "for", "from", "get", "got", "had", "has", "have", "he",
    "her", "him", "his", "how", "i", "if", "in", "into", "is", "it", "its", "just",
    "me", "more", "most", "much", "my", "no", "not", "now", "of", "off", "on", "one",
    "only", "or", "other", "our", "out", "over", "own", "she", "so", "some", "such",
    "than", "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "through", "to", "too", "under", "up", "us", "very", "was", "we", "well",
    "were", "what", "when", "where", "which", "while", "who", "why", "will", "with",
    "would", "you", "your",
}

# Words that appear in praise, complaint and job posts alike. Stripped from both
# sides so they can never be the thing that makes a review look specific.
_GENERIC_TERMS = {
    "able", "amazing", "awesome", "bad", "best", "better", "big", "budget", "build",
    "building", "built", "business", "client", "communication", "communicate",
    "company", "cost", "customer", "deadline", "deliver", "delivered", "delivery",
    "developer", "development", "excellent", "expect", "expected", "experience",
    "fast", "feedback", "freelancer", "good", "great", "hire", "hired", "job",
    "great", "highly", "issue", "issues", "money", "need", "needed", "nice",
    "outstanding", "perfect", "platform", "price", "pro", "problem", "problems",
    "process", "professional", "professionalism", "project", "quality", "quick",
    "quickly", "rate", "recommend", "recommended", "reliable", "requirement",
    "requirements", "review", "satisfied", "scope", "seller", "service", "services",
    "skill", "skills", "star", "stars", "super", "task", "team", "thank", "thanks",
    "time", "timeline", "top", "value", "work", "worked", "working", "would",
}

_IGNORED = _STOPWORDS | _GENERIC_TERMS


def _tokens(text: Optional[str]) -> Set[str]:
    """Content tokens: lowercase, 3+ characters, minus grammar and review filler."""
    if not text:
        return set()
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= 3 and t not in _IGNORED
    }


def _word_count(text: Optional[str]) -> int:
    return len(_TOKEN_RE.findall((text or "").lower()))


def _boilerplate_markers(text: str) -> List[str]:
    """Which stock phrases appear. Substring match on whitespace-normalised text,
    so punctuation between words ("A+++++ super fast") does not hide a marker."""
    normalised = re.sub(r"\s+", " ", (text or "").lower())
    return [marker for marker in _BOILERPLATE_MARKERS if marker in normalised]


def build_project_vocabulary(sources: Iterable[Optional[str]]) -> Set[str]:
    """Every content word the platform knows about this engagement.

    Callers pass whatever they have: job title, job description, role title,
    contract title, role skills, submission notes, the generated question. More
    sources make the test more forgiving, which is the direction to err in - each
    one is another way for a genuine review to prove it was there.
    """
    vocabulary: Set[str] = set()
    for source in sources:
        vocabulary |= _tokens(source)
    return vocabulary


def measure_specificity(
    review_text: str,
    answer_text: str,
    project_vocabulary: Set[str],
) -> Dict:
    """
    Does this review name anything from the project it is reviewing?

    Args:
        review_text: the overall comment.
        answer_text: the answer to the generated question. Counted, because
            answering the question with a project detail is exactly the evidence
            this is looking for - a reviewer who names nothing in the comment but
            answers the question concretely was demonstrably there.
        project_vocabulary: from build_project_vocabulary.

    Returns:
        {
            "shared_terms": list[str],    up to 10, for the flag reason
            "shared_term_count": int,
            "word_count": int,            comment + answer
            "boilerplate_markers": list[str],   stock phrases found
            "is_generic": bool,           see below
            "measurable": bool,           False when the platform had no project
                                          vocabulary to compare against
        }

        is_generic requires ALL of:
          * no shared vocabulary with the project,
          * short enough to be a template (MAX_TEMPLATE_WORDS), and
          * either stock marketplace phrasing, or too short to say anything
            (MIN_SUBSTANTIVE_WORDS).

        The third condition is what stops the rule holding genuine reviews that
        describe the engagement rather than the subject matter - see the module
        docstring for the false positive that put it there.

        is_generic is False whenever measurable is False. With nothing to compare
        against, the honest answer is "unknown", and unknown must not hold a
        review - the same rule the rest of the pipeline follows for missing data.
    """
    combined = " ".join(part for part in [(review_text or "").strip(),
                                          (answer_text or "").strip()] if part)
    word_count = _word_count(combined)
    markers = _boilerplate_markers(combined)

    if not project_vocabulary:
        return {
            "shared_terms": [],
            "shared_term_count": 0,
            "word_count": word_count,
            "boilerplate_markers": markers,
            "is_generic": False,
            "measurable": False,
        }

    shared = _tokens(combined) & project_vocabulary
    is_generic = (
        not shared
        and word_count <= MAX_TEMPLATE_WORDS
        and (bool(markers) or word_count <= MIN_SUBSTANTIVE_WORDS)
    )
    return {
        "shared_terms": sorted(shared)[:10],
        "shared_term_count": len(shared),
        "word_count": word_count,
        "boilerplate_markers": markers,
        "is_generic": is_generic,
        "measurable": True,
    }


def specificity_flag_reason(specificity: Dict) -> Optional[str]:
    """The reason string for a held review, or None when nothing is wrong.

    Written to be shown to the reviewer as well as the admin: it says what the
    test was and what would have satisfied it, because "your review was held" with
    no actionable reason is worse than not holding it.
    """
    if not specificity.get("is_generic"):
        return None

    markers = specificity.get("boilerplate_markers") or []
    corroboration = (
        f"and uses stock marketplace phrasing ({', '.join(markers[:4])})" if markers
        else f"and is only {specificity['word_count']} words long"
    )
    return (
        f"Review references nothing from this project - none of its "
        f"{specificity['word_count']} words match the job title, description, role "
        f"skills, submission notes or the question asked - {corroboration}. Naming one "
        f"concrete thing from the engagement would clear this check"
    )


def describe_project_terms(project_vocabulary: Set[str], limit: int = 15) -> List[str]:
    """A sample of the vocabulary, for the admin panel. Sorted so it is stable."""
    return sorted(project_vocabulary)[:limit]


def fetch_project_vocabulary(contract_id: str, extra: Iterable[Optional[str]] = ()) -> Set[str]:
    """Everything the platform recorded about a contract, as a vocabulary.

    The one function here that touches the database. It lives in this module
    rather than in either pipeline because both sides need exactly the same
    vocabulary from exactly the same tables, and a second copy of this query is
    a second thing to keep in step - the duplication that review_decision.py
    exists to have stopped.

    `extra` is for text the caller already holds and this query cannot reach,
    which in practice is the generated question: it lives on the review's written
    content, not on the contract.

    get_db is imported lazily so the scoring functions above stay importable, and
    unit-testable, with no database configured. Any failure returns an empty
    vocabulary, which measure_specificity reports as unmeasurable and therefore
    never holds a review on - a broken query must not start flagging reviews.
    """
    from functions.db_manager import get_db
    from functions.logger import logger

    sources: List[Optional[str]] = list(extra)
    try:
        db = get_db()
        rows = db.execute_query(
            """SELECT c.contract_title, c.role_title AS contract_role_title,
                      jp.job_title, jp.job_description, jr.role_title
               FROM contract c
               JOIN job_post jp ON jp.job_post_id = c.job_post_id
               JOIN job_role jr ON jr.job_role_id = c.job_role_id
               WHERE c.contract_id = :cid""",
            {"cid": contract_id},
        )
        for row in rows or []:
            sources.extend([
                row.get("contract_title"), row.get("contract_role_title"),
                row.get("job_title"), row.get("job_description"), row.get("role_title"),
            ])

        skill_rows = db.execute_query(
            """SELECT s.skill_name FROM job_role_skill jrs
               JOIN skill s ON s.skill_id = jrs.skill_id
               JOIN contract c ON c.job_role_id = jrs.job_role_id
               WHERE c.contract_id = :cid""",
            {"cid": contract_id},
        )
        sources.extend(row.get("skill_name") for row in (skill_rows or []))

        # The freelancer's own account of what they delivered - the richest
        # project-specific source there is, and the one a template is least
        # likely to accidentally overlap with.
        note_rows = db.execute_query(
            """SELECT note, revision_note FROM contract_submission
               WHERE contract_id = :cid ORDER BY submitted_at DESC LIMIT 10""",
            {"cid": contract_id},
        )
        for row in note_rows or []:
            sources.extend([row.get("note"), row.get("revision_note")])
    except Exception as e:
        logger("REVIEW_SPECIFICITY",
               f"Could not build project vocabulary for {contract_id}: {str(e)[:200]}",
               level="WARNING")
        return set()

    return build_project_vocabulary(sources)
