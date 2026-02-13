from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from pydantic import BaseModel
import logging
import json

from dependencies import get_mongo_connection
from services.internal.spending_best_practices_service import SpendingBestPracticesService
from encoder.json_encoder import MyJSONEncoder
from fastapi.responses import Response

import os
from dotenv import load_dotenv

load_dotenv()

# Set up logging configuration
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

router = APIRouter()

# Initialize the MongoDB connection
connection = get_mongo_connection()

# Get the database name from the environment variable
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")

# Initialize service
spending_service = SpendingBestPracticesService(
    connection,
    LEAFYBANK_DB_NAME,
    "spending_best_practices"
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# --- N4: Spending Best Practices ---

class SpendingBestPracticesResponse(BaseModel):
    categories: List[Dict]


@router.get("/best-practices", response_model=SpendingBestPracticesResponse)
@limiter.limit("60/minute")
async def get_spending_best_practices(request: Request):
    """
    Retrieve spending category definitions with MCC mappings.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No consent required — this is Leafy Bank's own reference data.

    Returns all 9 spending categories with ideal %, min/max ranges, MCC codes.
    """
    try:
        categories = spending_service.get_best_practices()

        return Response(
            content=json.dumps({"categories": categories}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error retrieving spending best practices: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
