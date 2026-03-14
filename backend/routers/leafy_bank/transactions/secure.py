from fastapi import APIRouter, Depends, HTTPException, Request, Response
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from bson import ObjectId
import logging
import json

from dependencies import get_auth, get_bearer_token, get_mongo_connection
from services.auth import Auth
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

        logger.info(f"Fetching recent transactions for user: {user_identifier}")

        # If `user_identifier` is an ObjectId-like string, convert it to ObjectId
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)

        # Validate if the user exists
        if not transactions_service.is_valid_user(user_identifier):
            logger.error(f"User with identifier {user_identifier} not found.")
            raise HTTPException(status_code=404, detail="User not found.")

        # Retrieve recent transactions for the valid user
        transactions = transactions_service.get_recent_transactions_for_user(
            user_identifier)

        if transactions:
            logger.info(
                f"Found {len(transactions)} recent transactions for user {user_identifier}.")
        else:
            logger.info(
                f"No recent transactions found for user {user_identifier}.")

        return Response(
            content=json.dumps(
                {"transactions": transactions}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he  # Propagate pre-raised HTTPException
    except Exception as e:
        logger.error(
            f"Error retrieving recent transactions for user: {str(e)}")
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

    Unlike fetch-recent-transactions-for-user (which returns the last 20 from the user's
    RecentTransactions array), this queries the transactions collection directly for ALL
    transaction records — both CREDIT (income) and DEBIT (spending) from checking and
    credit card accounts. The agent tool needs both: CREDIT to calculate total income,
    DEBIT to calculate spending per category, then % = category / income.
    """
    try:
        # Validate if the user exists
        if not transactions_service.is_valid_user(user_identifier):
            logger.error(f"User with identifier {user_identifier} not found.")
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
            f"Error retrieving spending transactions for user {user_identifier}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
