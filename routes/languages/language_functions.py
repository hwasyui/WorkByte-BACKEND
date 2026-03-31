import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from functions.db_manager import get_db
from functions.logger import logger
from typing import List, Optional, Dict
import uuid

def convert_uuids_to_str(data: Dict) -> Dict:
    """Convert all UUID objects in dict to strings"""
    if not data:
        return data
    result = {}
    for key, value in data.items():
        if hasattr(value, '__class__') and 'UUID' in value.__class__.__name__:
            result[key] = str(value)
        else:
            result[key] = value
    return result


class LanguageFunctions:
    """Handle all language-related database operations"""

    @staticmethod
    def get_all_languages(limit: Optional[int] = None, offset: int = 0) -> List[Dict]:
        """Fetch all languages"""
        try:
            db = get_db()
            rows = db.fetch_data(
                table_name="language",
                columns=["language_id", "language_name", "iso_code", "created_at"],
                order_by="language_name ASC",
                limit=limit
            )
            
            logger("LANGUAGE_FUNCTIONS", f"Fetched {len(rows)} languages", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error fetching languages: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_language_by_id(language_id: str) -> Optional[Dict]:
        """Fetch a single language by ID"""
        try:
            db = get_db()
            conditions = [("language_id", "=", language_id)]
            rows = db.fetch_data(
                table_name="language",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                logger("LANGUAGE_FUNCTIONS", f"Language {language_id} found", level="INFO")
                return convert_uuids_to_str(dict(rows[0]))
            
            return None
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error fetching language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def get_language_by_name(language_name: str) -> Optional[Dict]:
        """Fetch a language by name"""
        try:
            db = get_db()
            conditions = [("language_name", "=", language_name)]
            rows = db.fetch_data(
                table_name="language",
                conditions=conditions,
                limit=1
            )
            
            if rows:
                return convert_uuids_to_str(dict(rows[0]))
            return None
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error fetching language by name: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def create_language(language_name: str, iso_code: Optional[str] = None) -> Dict:
        """Create a new language"""
        try:
            db = get_db()
            # Check if language already exists
            existing = LanguageFunctions.get_language_by_name(language_name)
            if existing:
                logger("LANGUAGE_FUNCTIONS", f"Language {language_name} already exists", level="WARNING")
                raise ValueError(f"Language '{language_name}' already exists")
            
            language_id = str(uuid.uuid4())
            
            language_data = {
                "language_id": language_id,
                "language_name": language_name,
                "iso_code": iso_code
            }
            
            db.insert_data(table_name="language", data=language_data)
            
            logger("LANGUAGE_FUNCTIONS", f"Language {language_id} created: {language_name}", level="INFO")
            return convert_uuids_to_str(language_data)
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error creating language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def update_language(language_id: str, update_data: Dict) -> Optional[Dict]:
        """Update language information"""
        try:
            db = get_db()
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}
            
            if not update_data:
                logger("LANGUAGE_FUNCTIONS", "No data to update", level="WARNING")
                return LanguageFunctions.get_language_by_id(language_id)
            
            # If updating language_name, check for duplicates
            if "language_name" in update_data:
                existing = LanguageFunctions.get_language_by_name(update_data["language_name"])
                if existing and existing["language_id"] != language_id:
                    raise ValueError(f"Language name '{update_data['language_name']}' already exists")
            
            conditions = [("language_id", "=", language_id)]
            db.update_data(table_name="language", data=update_data, conditions=conditions)
            
            logger("LANGUAGE_FUNCTIONS", f"Language {language_id} updated", level="INFO")
            return LanguageFunctions.get_language_by_id(language_id)
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error updating language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def delete_language(language_id: str) -> bool:
        """Delete a language"""
        try:
            db = get_db()
            conditions = [("language_id", "=", language_id)]
            db.delete_data(table_name="language", conditions=conditions)
            
            logger("LANGUAGE_FUNCTIONS", f"Language {language_id} deleted", level="INFO")
            return True
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error deleting language: {str(e)}", level="ERROR")
            raise

    @staticmethod
    def search_languages_by_name(search_term: str) -> List[Dict]:
        """Search languages by name"""
        try:
            db = get_db()
            query = "SELECT * FROM language WHERE language_name ILIKE '%' || :search_term || '%' ORDER BY language_name ASC"
            rows = db.execute_query(query, {"search_term": search_term})
            
            logger("LANGUAGE_FUNCTIONS", f"Found {len(rows)} languages matching '{search_term}'", level="INFO")
            return [convert_uuids_to_str(dict(row)) for row in rows]
        
        except Exception as e:
            logger("LANGUAGE_FUNCTIONS", f"Error searching languages: {str(e)}", level="ERROR")
            raise
