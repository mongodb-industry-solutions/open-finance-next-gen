from fastapi import APIRouter, Depends, HTTPException, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
import json

from dependencies import get_auth, get_bearer_token, get_mongo_connection, get_encrypted_mongo_connection
from utils.security import sanitize_log_input
from services.auth import Auth
from services.open_finance.customer_identification_service import CustomerIdentificationService
from services.open_finance.repayment_history_service import RepaymentHistoryService
from services.internal.credit_bureau_service import CreditBureauService
from encoder.json_encoder import MyJSONEncoder
from fastapi.responses import Response

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize the MongoDB connections
connection = get_mongo_connection()
encrypted_connection = get_encrypted_mongo_connection()

# Database names
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")

# Collection names
ENCRYPTED_CONSENTS_COLLECTION = "encrypted_consents"

# Initialize services (use encrypted connection for consent-gated services)
customer_identification_service = CustomerIdentificationService(
    encrypted_connection,
    OPENFINANCE_DB_NAME,
    ENCRYPTED_CONSENTS_COLLECTION,
    "external_customer_identification"
)

repayment_history_service = RepaymentHistoryService(
    encrypted_connection,
    OPENFINANCE_DB_NAME,
    ENCRYPTED_CONSENTS_COLLECTION,
    "external_repayment_history"
)

credit_bureau_service = CreditBureauService(
    connection,
    LEAFYBANK_DB_NAME,
    "credit_bureau_scores"
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# --- N1: Customer Identification (consent-gated) ---

class CustomerIdentificationResponse(BaseModel):
    customer_identification: List[Dict]
    consent_id: str
    consent_status: str
    source_institution: str


@router.get("/{user_identifier}/identification", response_model=CustomerIdentificationResponse)
@limiter.limit("60/minute")
async def get_customer_identification(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Retrieve customer identification (KYC) data from an external bank.

    Requires bearer token authentication + a valid, authorized consent
    with CUSTOMER_IDENTIFICATION_READ permission.

    Returns customer name, ID, DOB, address, employment from the consented institution.
    """
    try:
        # Validate bearer token and verify authenticated user matches path
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        result = customer_identification_service.get_identification(
            consent_id=consent_id,
            user_name=user_auth['UserName']
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error retrieving customer identification: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving customer identification: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# --- N2: Repayment History (consent-gated) ---

class RepaymentHistoryResponse(BaseModel):
    repayment_history: List[Dict]
    consent_id: str
    consent_status: str
    source_institution: str


@router.get("/{user_identifier}/repayment-history", response_model=RepaymentHistoryResponse)
@limiter.limit("60/minute")
async def get_repayment_history(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Retrieve loan repayment history from an external bank.

    Requires bearer token authentication + a valid, authorized consent
    with REPAYMENT_HISTORY_READ permission.

    Returns payment dates, amounts, statuses (OnTime/Late) for the consented institution.
    """
    try:
        # Validate bearer token and verify authenticated user matches path
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        result = repayment_history_service.get_repayment_history(
            consent_id=consent_id,
            user_name=user_auth['UserName']
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error retrieving repayment history: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving repayment history: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# --- N3: Credit Bureau Score (no consent needed) ---

class CreditScoreResponse(BaseModel):
    credit_score: Dict


@router.get("/{user_identifier}/credit-score", response_model=CreditScoreResponse)
@limiter.limit("60/minute")
async def get_credit_score(
    request: Request,
    user_identifier: str
):
    """
    Retrieve credit bureau score for a user.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No consent required — this is Leafy Bank's own internal data.

    Returns score (number or null), status (Active/NoHistory), bureau, factors.
    """
    try:
        record = credit_bureau_service.get_credit_score(user_identifier)

        return Response(
            content=json.dumps({"credit_score": record}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving credit score for {sanitize_log_input(user_identifier)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
