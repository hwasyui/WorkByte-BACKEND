from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import sys, os, uvicorn, json, re
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.functions import get_table_testing
from functions.logger import logger

app = FastAPI(title="CAPSTONE - BACKEND API", description="API for CAPSTONE project", version="1.0")

@app.get("/testing_tables")
def get_testing_tables():
    try: 
        data = get_table_testing()
        return {"data": [row.to_dict() for row in data]}
    except Exception as e:
        logger("API", f"Error fetching testing tables: {str(e)}", level="ERROR")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error fetching testing tables")
        

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0", port=8000, workers=1)

