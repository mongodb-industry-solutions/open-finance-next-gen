from fastapi import APIRouter, HTTPException, Response, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from pydantic import BaseModel
import logging
import json

from dependencies import get_mongo_connection
from utils.security import sanitize_log_input
from services.institutions.institution_service import InstitutionService
from encoder.json_encoder import MyJSONEncoder

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize the MongoDB connection
connection = get_mongo_connection()

# Get the database name from the environment variable
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")

# Collection name
INSTITUTIONS_COLLECTION = "institutions"

# Initialize the InstitutionService
institution_service = InstitutionService(
    connection, OPENFINANCE_DB_NAME, INSTITUTIONS_COLLECTION
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# Define Pydantic Models
class InstitutionListResponse(BaseModel):
    institutions: List[Dict]


class InstitutionResponse(BaseModel):
    institution: Dict


# Define API Endpoints

@router.get("/", response_model=InstitutionListResponse)
@limiter.limit("60/minute")
async def list_institutions(
    request: Request
):
    """
    List all available institutions in the Open Finance ecosystem.
    Returns institutions that users can connect to for data sharing.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank
        institutions = institution_service.list_institutions()
        return Response(
            content=json.dumps({"institutions": institutions}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error listing institutions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{institution_name}", response_model=InstitutionResponse)
@limiter.limit("60/minute")
async def get_institution(
    request: Request,
    institution_name: str
):
    """
    Get details of a specific institution by name.
    The institution_name should match the InstitutionName field (e.g., 'Green Bank').

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank
        institution = institution_service.get_institution_by_name(institution_name)

        if not institution:
            logger.error(f"Institution not found: {sanitize_log_input(institution_name)}")
            raise HTTPException(
                status_code=404,
                detail=f"Institution '{institution_name}' not found."
            )

        return Response(
            content=json.dumps({"institution": institution}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error getting institution {sanitize_log_input(institution_name)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
