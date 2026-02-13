from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from pydantic import BaseModel
import logging
import json

from dependencies import get_mongo_connection
from services.internal.portability_rules_service import PortabilityRulesService
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
portability_service = PortabilityRulesService(
    connection,
    LEAFYBANK_DB_NAME,
    "portability_underwriting_rules"
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# --- N5: Portability Underwriting Rules ---

class UnderwritingRulesResponse(BaseModel):
    rules: List[Dict]


@router.get("/underwriting-rules", response_model=UnderwritingRulesResponse)
@limiter.limit("60/minute")
async def get_underwriting_rules(request: Request):
    """
    Retrieve portability underwriting rule tiers.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No consent required — this is Leafy Bank's own reference data.

    Returns all rules with LoanSubTypes, amount ranges, paths, and tiers.
    """
    try:
        rules = portability_service.get_underwriting_rules()

        return Response(
            content=json.dumps({"rules": rules}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error retrieving underwriting rules: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
