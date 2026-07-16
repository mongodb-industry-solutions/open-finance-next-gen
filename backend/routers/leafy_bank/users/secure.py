from fastapi import APIRouter, HTTPException, Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import Dict
from bson import ObjectId
import logging
import json

from dependencies import get_mongo_connection
from utils.security import sanitize_log_input
from services.internal.users_service import UsersService
from encoder.json_encoder import MyJSONEncoder
from pydantic import BaseModel

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize the MongoDB connection
connection = get_mongo_connection()

# Get the database name from the environment variable
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")

# Collection names
CUSTOMERS_COLLECTION = "customers"

# Initialize the UsersService
users_service = UsersService(connection, LEAFYBANK_DB_NAME, CUSTOMERS_COLLECTION)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)

# Define Pydantic Models


class FindUserRequest(BaseModel):
    user_identifier: str


class FindUserResponse(BaseModel):
    user: Dict


# Define the endpoints

# Endpoint to find a user by their identifier (username or ID)
@router.post("/find-user", response_model=FindUserResponse)
@limiter.limit("60/minute")
async def find_user(
    request: Request,
    user_data: FindUserRequest
):
    """
    Find a user by their identifier (username or ID).

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank
        user_identifier = user_data.user_identifier

        logger.info("Finding user: %s", sanitize_log_input(user_identifier))

        # Convert string-based ObjectId to a valid ObjectId, if applicable
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)

        user = users_service.get_user(user_identifier)
        if not user:
            logger.error("User with identifier %s not found.", sanitize_log_input(user_identifier))
            raise HTTPException(status_code=404, detail="User not found.")

        return Response(content=json.dumps({"user": user}, cls=MyJSONEncoder), media_type="application/json")
    except HTTPException as he:
        raise he  # Propagate pre-raised HTTPException
    except Exception as e:
        logger.error("Error finding user: %s", sanitize_log_input(str(e)))
        raise HTTPException(status_code=500, detail="Internal Server Error")
