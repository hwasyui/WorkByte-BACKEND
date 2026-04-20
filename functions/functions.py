import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger
from functions.db_manager import get_db

def get_table_testing():
    try:
        row = get_db().fetch_data("testing_table")
        logger("FUNCTIONS", f"Fetched {len(row)} rows from testing table", level="INFO")
        return row
    except Exception as e:
        logger("FUNCTIONS", f"Error fetching data from testing table: {str(e)}", level="ERROR")
        return []