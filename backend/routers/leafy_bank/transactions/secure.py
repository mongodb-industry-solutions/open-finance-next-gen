from fastapi import APIRouter, HTTPException, Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from bson import ObjectId
import logging
import json

from dependencies import get_mongo_connection
from utils.security import sanitize_log_input
from services.internal.transactions_service import TransactionsService
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

# Initialize the TransactionsService
transactions_service = TransactionsService(connection, LEAFYBANK_DB_NAME)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)

# Define Pydantic Models


class UserIdentifierRequest(BaseModel):
    user_identifier: str


class RecentTransactionsResponse(BaseModel):
    transactions: List[Dict]

# Endpoint to fetch recent transactions for a user


@router.post("/fetch-recent-transactions-for-user", response_model=RecentTransactionsResponse)
@limiter.limit("60/minute")
async def fetch_recent_transactions_for_user(
    request: Request,
    user_data: UserIdentifierRequest
):
    """
    Fetch recent transactions for a user based on the provided user identifier.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank

        # Extract `user_identifier` from request and ensure it's not null
        user_identifier = user_data.user_identifier
        if not user_identifier:
            logger.error("Missing user identifier in request.")
            raise HTTPException(
                status_code=400, detail="User identifier is required.")

        logger.info("Fetching recent transactions for user: %s", sanitize_log_input(user_identifier))

        # If `user_identifier` is an ObjectId-like string, convert it to ObjectId
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)

        # Validate if the user exists
        if not transactions_service.is_valid_user(user_identifier):
            logger.error("User with identifier %s not found.", sanitize_log_input(user_identifier))
            raise HTTPException(status_code=404, detail="User not found.")

        # Retrieve recent transactions for the valid user
        transactions = transactions_service.get_recent_transactions_for_user(
            user_identifier)

        if transactions:
            logger.info(
                "Found %d recent transactions for user %s.", len(transactions), sanitize_log_input(user_identifier))
        else:
            logger.info(
                "No recent transactions found for user %s.", sanitize_log_input(user_identifier))

        return Response(
            content=json.dumps(
                {"transactions": transactions}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he  # Propagate pre-raised HTTPException
    except Exception as e:
        logger.error(
            "Error retrieving recent transactions for user: %s", sanitize_log_input(str(e)))
        raise HTTPException(status_code=500, detail="Internal Server Error")


# --- N6: User Spending Transactions ---

class SpendingTransactionsResponse(BaseModel):
    transactions: List[Dict]
    total_count: int


@router.get("/spending/{user_identifier}", response_model=SpendingTransactionsResponse)
@limiter.limit("60/minute")
async def get_spending_transactions(
    request: Request,
    user_identifier: str
):
    """
    Retrieve all transactions for a user (CREDIT + DEBIT).

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No consent required — this is Leafy Bank's internal transaction data.

    Unlike fetch-recent-transactions-for-user (which returns the latest 20), this returns
    ALL transaction records for the customer's accounts — both incoming (income) and
    outgoing (spending). The agent tool needs both: income to calculate total income,
    spending per category, then % = category / income.
    """
    try:
        # Validate if the user exists
        if not transactions_service.is_valid_user(user_identifier):
            logger.error("User with identifier %s not found.", sanitize_log_input(user_identifier))
            raise HTTPException(status_code=404, detail="User not found.")

        transactions = transactions_service.get_all_transactions_for_user(user_identifier)

        return Response(
            content=json.dumps(
                {"transactions": transactions, "total_count": len(transactions)},
                cls=MyJSONEncoder
            ),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(
            "Error retrieving spending transactions for user %s: %s", sanitize_log_input(user_identifier), sanitize_log_input(str(e)))
        raise HTTPException(status_code=500, detail="Internal Server Error")
