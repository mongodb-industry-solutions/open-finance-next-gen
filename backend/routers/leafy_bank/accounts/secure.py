from fastapi import APIRouter, HTTPException, Response, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from pydantic import BaseModel
from bson import ObjectId
import logging
import json

from dependencies import get_mongo_connection
from utils.security import sanitize_log_input
from services.internal.accounts_service import AccountsService
from services.internal.users_service import UsersService
from encoder.json_encoder import MyJSONEncoder

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
ACCOUNTS_COLLECTION = "accounts"
CUSTOMERS_COLLECTION = "customers"

# Initialize the AccountsService
accounts_service = AccountsService(
    connection, LEAFYBANK_DB_NAME, ACCOUNTS_COLLECTION
)

# UsersService resolves a user identifier to the BIAN customerId used to join accounts
users_service = UsersService(connection, LEAFYBANK_DB_NAME, CUSTOMERS_COLLECTION)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)

# Define Pydantic Models


class FetchAccountsForUserRequest(BaseModel):
    user_identifier: str


class FetchAccountsResponse(BaseModel):
    accounts: List[Dict]


# Define API Endpoints

# Endpoint to fetch accounts for a specific user
@router.post("/fetch-accounts-for-user", response_model=FetchAccountsResponse)
@limiter.limit("60/minute")
async def fetch_accounts_for_user(
    request: Request,
    user_data: FetchAccountsForUserRequest
):
    """
    Fetch all accounts for a specific user.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank
        user_identifier = user_data.user_identifier

        logger.info("Fetching accounts for user: %s", sanitize_log_input(user_identifier))

        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)

        # Resolve to the BIAN customerId used to join accounts
        customer_id = users_service.get_customer_id(user_identifier)
        if not customer_id:
            raise HTTPException(status_code=404, detail="User not found.")

        accounts = accounts_service.get_accounts_for_user(customer_id)
        return Response(content=json.dumps({"accounts": accounts}, cls=MyJSONEncoder), media_type="application/json")

    except HTTPException as he:
        raise he  # Propagate pre-raised HTTPException
    except Exception as e:
        logger.error("Error fetching accounts for user: %s", sanitize_log_input(str(e)))
        raise HTTPException(status_code=500, detail="Internal Server Error")
