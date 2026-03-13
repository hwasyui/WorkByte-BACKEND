import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger
from functions.database import Database

db = Database(
    db_user="capstone",
    db_password="capstone",
    db_host="capstone-postgresql",
    db_port=5432,
    db_name="capstone"
)

def get_table_testing():
    try:
        row = db.fetch_data("testing_table")
        logger("FUNCTIONS", f"Fetched {len(row)} rows from testing table", level="INFO")
        return row
    except Exception as e:
        logger("FUNCTIONS", f"Error fetching data from testing table: {str(e)}", level="ERROR")
        return []