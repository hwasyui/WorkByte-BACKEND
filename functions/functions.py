import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger
from functions.database import Database

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

db = Database(
    db_user=os.getenv("DB_USER", "capstone"),
    db_password=os.getenv("DB_PASSWORD", "capstone"),
    db_host=os.getenv("DB_HOST", "capstone-postgresql"),
    db_port=int(os.getenv("DB_PORT", 5432)),
    db_name=os.getenv("DB_NAME", "capstone")
)

def get_table_testing():
    try:
        row = db.fetch_data("testing_table")
        logger("FUNCTIONS", f"Fetched {len(row)} rows from testing table", level="INFO")
        return row
    except Exception as e:
        logger("FUNCTIONS", f"Error fetching data from testing table: {str(e)}", level="ERROR")
        return []