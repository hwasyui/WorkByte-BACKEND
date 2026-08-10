from typing import Dict, List
from functions.logger import logger

_SCOPE_BY_JOB_POST = "p.job_post_id = :scope_id"
_SCOPE_BY_JOB_ROLE = "p.job_role_id = :scope_id"


def empty_score() -> Dict:
    """The score payload for a proposal we could not rank at all."""
    return {
        "relevance_score":   None,
        "relevance_method":  "unavailable",
        "relevance_ready":   False,
        "vector_similarity": None,
    }


def _fetch_proposal_ids(db, scope_sql: str, scope_id: str) -> List[str]:
    rows = db.execute_query(
        f"SELECT p.proposal_id::text AS proposal_id FROM proposal p WHERE {scope_sql}",
        {"scope_id": scope_id},
    )
    return [row["proposal_id"] for row in rows]


def _fetch_vector_similarities(db, scope_sql: str, scope_id: str) -> Dict[str, float]:
    """Cosine similarity per proposal, for the proposals whose freelancer AND role are
    both already embedded. Missing rows mean the sweep has not caught up yet, which the
    caller reports as unranked rather than guessing at."""
    rows = db.execute_query(
        f"""
        SELECT p.proposal_id::text AS proposal_id,
               GREATEST(
                   0::float8,
                   LEAST(1::float8, 1 - (fe.embedding_vector <=> jre.embedding_vector))
               ) AS vector_similarity
        FROM proposal p
        JOIN freelancer_embedding fe
          ON fe.freelancer_id = p.freelancer_id
         AND fe.embedding_vector IS NOT NULL
        JOIN job_role_embedding jre
          ON jre.job_role_id = p.job_role_id
         AND jre.embedding_vector IS NOT NULL
        WHERE {scope_sql}
        """,
        {"scope_id": scope_id},
    )
    return {row["proposal_id"]: float(row["vector_similarity"]) for row in rows}


def _from_vector(vector_similarity: float) -> Dict:
    return {
        "relevance_score":   round(vector_similarity * 100),
        "relevance_method":  "vector_similarity",
        "relevance_ready":   True,
        "vector_similarity": round(vector_similarity, 4),
    }


def _score(db, scope_sql: str, scope_id: str) -> Dict[str, Dict]:
    """Score every proposal in scope. Never raises - a ranking failure degrades the
    bidding view to unranked rather than taking the whole view down with it."""
    try:
        proposal_ids = _fetch_proposal_ids(db, scope_sql, scope_id)
        if not proposal_ids:
            return {}

        vectors = _fetch_vector_similarities(db, scope_sql, scope_id)

        scores = {
            pid: (_from_vector(vectors[pid]) if pid in vectors else empty_score())
            for pid in proposal_ids
        }

        logger(
            "APPLICANT_RANKER",
            f"Scored {len(vectors)}/{len(scores)} proposals | scope={scope_sql} | scope_id={scope_id}",
            level="INFO",
        )
        return scores

    except Exception as e:
        logger(
            "APPLICANT_RANKER",
            f"Ranking failed, returning unranked | scope_id={scope_id} | error={e}",
            level="ERROR",
        )
        return {}


def score_proposals_for_job_post(db, job_post_id: str) -> Dict[str, Dict]:
    """Score every proposal on a job post. Each proposal is still scored against its own
    role, so the numbers are for display and for sorting WITHIN a role - not for ordering
    the post's applicants as one list."""
    return _score(db, _SCOPE_BY_JOB_POST, job_post_id)


def score_proposals_for_job_role(db, job_role_id: str) -> Dict[str, Dict]:
    """Score every proposal on one role. These are directly comparable to each other."""
    return _score(db, _SCOPE_BY_JOB_ROLE, job_role_id)
