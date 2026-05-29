import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid

def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings."""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


class SkillFunctions:
    """Handle all skill-related database operations with simple string matching."""

    @staticmethod
    def get_all_skills(limit: Optional[int] = None) -> List[Dict]:
        """Fetch all skills with optional limit."""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="skill",
                columns=["skill_id", "skill_name", "skill_category", "search_tokens", "created_at"],
                order_by="skill_name ASC",
                limit=limit
            )

            logger("SKILL_FUNCTIONS", f"Fetched {len(rows)} skills", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error fetching skills: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_skill_by_id(skill_id: str) -> Optional[Dict]:
        """Fetch a single skill by ID."""
        try:
            db = get_db()
            conditions = [("skill_id", "=", skill_id)]
            rows = db.fetch_data(
                table_name="skill",
                conditions=conditions,
                limit=1
            )

            if rows:
                logger("SKILL_FUNCTIONS", f"Skill {skill_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))

            return None

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error fetching skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_skill_by_name(skill_name: str) -> Optional[Dict]:
        """Fetch a skill by name (case-insensitive)."""
        try:
            db = get_db()
            query = """
                SELECT skill_id, skill_name, skill_category, search_tokens, created_at
                FROM skill
                WHERE LOWER(skill_name) = LOWER(:name)
                LIMIT 1.
            """
            rows = db.execute_query(query, {"name": skill_name})

            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error fetching skill by name: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_skill(skill_name: str, skill_category: Optional[str] = None,
                    description: Optional[str] = None) -> Dict:
        """Create a new skill."""
        try:
            db = get_db()

            # Check if skill already exists (case-insensitive)
            existing = SkillFunctions.get_skill_by_name(skill_name)
            if existing:
                logger("SKILL_FUNCTIONS", f"Skill '{skill_name}' already exists", level="WARNING")
                raise ValueError(f"Skill '{skill_name}' already exists")

            skill_id = str(uuid.uuid4())
            search_tokens = description or ""

            skill_data = {
                "skill_id": skill_id,
                "skill_name": skill_name,
                "skill_category": skill_category,
                "search_tokens": search_tokens
            }

            db.insert_data(table_name="skill", data=skill_data)

            logger("SKILL_FUNCTIONS", f"Skill {skill_id} created: '{skill_name}'", level="INFO")
            return convert_uuids_to_str(skill_data)

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error creating skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_skill(skill_id: str, update_data: Dict) -> Optional[Dict]:
        """Update skill information."""
        try:
            db = get_db()
            update_data = {k: v for k, v in update_data.items() if v is not None}

            if not update_data:
                logger("SKILL_FUNCTIONS", "No data to update", level="WARNING")
                return SkillFunctions.get_skill_by_id(skill_id)

            # If updating skill_name, check for duplicates (case-insensitive)
            if "skill_name" in update_data:
                existing = SkillFunctions.get_skill_by_name(update_data["skill_name"])
                if existing and existing["skill_id"] != skill_id:
                    raise ValueError(f"Skill name '{update_data['skill_name']}' already exists")

            conditions = [("skill_id", "=", skill_id)]
            db.update_data(table_name="skill", data=update_data, conditions=conditions)

            logger("SKILL_FUNCTIONS", f"Skill {skill_id} updated", level="INFO")
            return SkillFunctions.get_skill_by_id(skill_id)

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error updating skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_skill(skill_id: str) -> bool:
        """Delete a skill."""
        try:
            db = get_db()
            conditions = [("skill_id", "=", skill_id)]
            db.delete_data(table_name="skill", conditions=conditions)

            logger("SKILL_FUNCTIONS", f"Skill {skill_id} deleted", level="INFO")
            return True

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error deleting skill: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_skills_autocomplete(query: str, limit: int = 10) -> List[Dict]:
        """
        Autocomplete suggestions with prefix matching (case-insensitive).
        Returns skills where skill_name or search_tokens match the query.
        """
        try:
            if not query or len(query.strip()) == 0:
                logger("SKILL_FUNCTIONS", "Empty query, returning top skills", level="INFO")
                return SkillFunctions.get_all_skills(limit=limit)

            db = get_db()
            query_lower = query.lower()

            # Prefix match on skill_name OR contains match on skill_name/search_tokens
            sql = """
                SELECT skill_id, skill_name, skill_category, search_tokens, created_at
                FROM skill
                WHERE LOWER(skill_name) LIKE :prefix
                   OR LOWER(skill_name) ILIKE :contains
                   OR (search_tokens IS NOT NULL AND LOWER(search_tokens) ILIKE :contains)
                ORDER BY
                    CASE WHEN LOWER(skill_name) LIKE :prefix THEN 0 ELSE 1 END,
                    skill_name ASC
                LIMIT :limit.
            """

            rows = db.execute_query(
                sql,
                {
                    "prefix": f"{query_lower}%",
                    "contains": f"%{query_lower}%",
                    "limit": limit
                }
            )

            results = [convert_uuids_to_str(dict(row)) for row in rows]
            logger("SKILL_FUNCTIONS", f"Autocomplete '{query}' found {len(results)} matches", level="INFO")
            return results

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error in autocomplete search: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_skills_by_category(category: str, limit: Optional[int] = None) -> List[Dict]:
        """Get all skills in a category."""
        try:
            db = get_db()
            conditions = [("skill_category", "=", category)]
            rows = db.fetch_data(
                table_name="skill",
                conditions=conditions,
                order_by="skill_name ASC",
                limit=limit
            )

            logger("SKILL_FUNCTIONS", f"Found {len(rows)} skills in category '{category}'", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error fetching skills by category: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_skills_by_alphabet(letter: str, limit: Optional[int] = None) -> List[Dict]:
        """Get skills starting with a specific letter (case-insensitive)."""
        try:
            db = get_db()
            query = """
                SELECT skill_id, skill_name, skill_category, search_tokens, created_at
                FROM skill
                WHERE LOWER(skill_name) LIKE :prefix
                ORDER BY skill_name ASC.
            """

            params = {"prefix": f"{letter.lower()}%"}
            if limit:
                query += f" LIMIT {limit}"

            rows = db.execute_query(query, params)

            logger("SKILL_FUNCTIONS", f"Found {len(rows)} skills starting with '{letter}'", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]

        except Exception as e:
            logger("SKILL_FUNCTIONS", f"Error fetching skills by alphabet: {str(e)}", level="ERROR")
            raise
