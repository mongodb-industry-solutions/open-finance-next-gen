from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
import json

from dependencies import get_auth, get_bearer_token, get_mongo_connection
from services.auth import Auth
from services.open_finance.customer_data_service import CustomerDataService
from encoder.json_encoder import MyJSONEncoder

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
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")

# Collection names
CONSENTS_COLLECTION = "consents"
EXTERNAL_ACCOUNTS_COLLECTION = "external_accounts"
EXTERNAL_PRODUCTS_COLLECTION = "external_products"
EXTERNAL_TRANSACTIONS_COLLECTION = "external_transactions"
EXTERNAL_REPAYMENT_HISTORY_COLLECTION = "external_repayment_history"
EXTERNAL_CUSTOMER_IDENTIFICATION_COLLECTION = "external_customer_identification"

# Initialize the CustomerDataService
customer_data_service = CustomerDataService(
    connection,
    OPENFINANCE_DB_NAME,
    CONSENTS_COLLECTION,
    EXTERNAL_ACCOUNTS_COLLECTION,
    EXTERNAL_PRODUCTS_COLLECTION,
    EXTERNAL_TRANSACTIONS_COLLECTION,
    EXTERNAL_REPAYMENT_HISTORY_COLLECTION,
    EXTERNAL_CUSTOMER_IDENTIFICATION_COLLECTION
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# Define Pydantic Models
class ExternalDataResponse(BaseModel):
    accounts: Optional[List[Dict]] = None
    products: Optional[List[Dict]] = None
    transactions: Optional[List[Dict]] = None
    repayment_history: Optional[List[Dict]] = None
    customer_identification: Optional[List[Dict]] = None
    consent_id: str
    consent_status: str  # CONSUMED after successful retrieval
    source_institution: str
    purpose: str


class ExternalTransactionsResponse(BaseModel):
    transactions: List[Dict]
    consent_id: str
    source_institution: str
    purpose: str


# Define API Endpoints

@router.get("/{user_identifier}/external-data", response_model=ExternalDataResponse)
@limiter.limit("60/minute")
async def retrieve_external_data(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId to use for data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Retrieve external data using an authorized consent.

    Requires bearer token authentication + a valid, authorized consent.

    This endpoint:
    1. Validates bearer token and verifies user identity
    2. Validates the consent exists and is AUTHORISED
    3. Retrieves data from the source institution based on consent purpose and permissions:
       - PERSONAL/PAYROLL/VEHICLE_LOAN_PORTABILITY: loans, accounts, transactions,
         repayment history, customer identification (each gated by consent permissions)
       - FINANCIAL_ADVICE: transactions and accounts
    4. Transitions the consent to CONSUMED for one-time consents
    """
    try:
        # Validate bearer token and verify authenticated user matches path
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        # Retrieve data using the consent
        result = customer_data_service.retrieve_data_with_consent(
            consent_id=consent_id,
            user_name=user_auth['UserName']
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logging.error(f"Validation error retrieving external data: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error retrieving external data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{user_identifier}/external-transactions", response_model=ExternalTransactionsResponse)
@limiter.limit("60/minute")
async def retrieve_external_transactions(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId to use for data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Retrieve only external transactions using an authorized consent.

    Requires bearer token authentication + a valid, authorized consent
    with TRANSACTIONS_READ permission.

    This endpoint:
    1. Validates bearer token and verifies user identity
    2. Validates the consent exists, is AUTHORISED, and has TRANSACTIONS_READ permission
    3. Retrieves transactions from the source institution
    """
    try:
        # Validate bearer token and verify authenticated user matches path
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        # Retrieve transactions using the consent
        result = customer_data_service.retrieve_transactions_with_consent(
            consent_id=consent_id,
            user_name=user_auth['UserName']
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except PermissionError as pe:
        logging.error(f"Permission error retrieving external transactions: {str(pe)}")
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        logging.error(f"Validation error retrieving external transactions: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error retrieving external transactions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
