from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
import json

from dependencies import get_auth, get_bearer_token, get_encrypted_mongo_connection, get_mongo_connection
from services.auth import Auth
from services.open_finance.customer_data_service import CustomerDataService
from services.open_finance.cached_data_service import CachedDataService
from services.aggregations.account_aggregations import AccountAggregations
from services.internal.users_service import UsersService
from services.consents.consent_validator import ConsentValidator
from encoder.json_encoder import MyJSONEncoder

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize the encrypted MongoDB connection (Queryable Encryption on consents)
encrypted_connection = get_encrypted_mongo_connection()

# Get the database names from the environment variables
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")  # external institution data
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")  # consents

# Collection names
CONSENTS_COLLECTION = "openBankingConsents"
EXTERNAL_ACCOUNTS_COLLECTION = "external_accounts"
EXTERNAL_PRODUCTS_COLLECTION = "external_products"
EXTERNAL_TRANSACTIONS_COLLECTION = "external_transactions"

# Cache collection (Leafy Bank database): external data is fetched with a consent
# and stored here until the consent is revoked or expires. One document per resource,
# tagged by ResourceType (ACCOUNT / PRODUCT / TRANSACTION).
CACHED_DATA_COLLECTION = "cachedExternalData"

# Consents live in the Leafy Bank database (QE-encrypted); external data stays in open_finance
consent_validator = ConsentValidator(
    encrypted_connection,
    db_name=LEAFYBANK_DB_NAME,
    consents_collection_name=CONSENTS_COLLECTION
)

# Initialize the CustomerDataService with encrypted connection
customer_data_service = CustomerDataService(
    encrypted_connection,
    OPENFINANCE_DB_NAME,
    consent_validator,
    EXTERNAL_ACCOUNTS_COLLECTION,
    EXTERNAL_PRODUCTS_COLLECTION,
    EXTERNAL_TRANSACTIONS_COLLECTION
)

# Cache lives in the Leafy Bank database on the plain (unencrypted) connection
plain_connection = get_mongo_connection()
cached_data_service = CachedDataService(
    plain_connection,
    LEAFYBANK_DB_NAME,
    customer_data_service,
    CACHED_DATA_COLLECTION
)

# Internal (Leafy Bank, BIAN) account balances for the global position. UsersService
# resolves the username to a BIAN customerId; AccountAggregations sums balance.current.
ACCOUNTS_COLLECTION = "accounts"
CUSTOMERS_COLLECTION = "customers"
users_service = UsersService(plain_connection, LEAFYBANK_DB_NAME, CUSTOMERS_COLLECTION)
account_aggregations = AccountAggregations(
    plain_connection,
    db1_name=LEAFYBANK_DB_NAME,
    collection1_name=ACCOUNTS_COLLECTION,
    db2_name=OPENFINANCE_DB_NAME,
    collection2_name=EXTERNAL_ACCOUNTS_COLLECTION,
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# Define Pydantic Models
class ExternalDataResponse(BaseModel):
    accounts: Optional[List[Dict]] = None
    products: Optional[List[Dict]] = None
    transactions: Optional[List[Dict]] = None
    consent_id: str
    consent_status: str  # CONSUMED after successful retrieval
    source_institution: str
    purpose: str


class ExternalTransactionsResponse(BaseModel):
    transactions: List[Dict]
    consent_id: str
    source_institution: str
    purpose: str


class FetchAndCacheResponse(ExternalDataResponse):
    cached_counts: Dict


class CachedInstitutionData(BaseModel):
    institution: str
    consent_id: str
    accounts: List[Dict]
    products: List[Dict]
    transactions: List[Dict]


class CachedDataResponse(BaseModel):
    user_identifier: str
    institutions: List[CachedInstitutionData]


class InstitutionPosition(BaseModel):
    institution: str
    balance: float
    debt: float


class GlobalPositionResponse(BaseModel):
    total_balance: float
    total_debt: float
    net_worth: float
    by_institution: List[InstitutionPosition]


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
    3. Retrieves data from the source institution based on consent permissions:
       - FINANCIAL_ADVICE: accounts, transactions, products (each gated by consent permissions)
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
        logger.error(f"Validation error retrieving external data: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving external data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{user_identifier}/fetch-and-cache", response_model=FetchAndCacheResponse)
@limiter.limit("60/minute")
async def fetch_and_cache_external_data(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The DURATION_BASED ConsentId to use for data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Fetch all consent-permitted external data and cache it in the Leafy Bank database.

    Requires bearer token authentication + a valid, AUTHORISED, DURATION_BASED consent.

    This endpoint:
    1. Validates bearer token and verifies user identity
    2. Retrieves accounts, transactions, and products gated by consent permissions
    3. Stores them in the cachedExternalData and cachedExternalTransactions collections,
       tagged with the ConsentId
    4. Returns the retrieved data plus per-resource cached counts

    Cached data is deleted automatically when the consent is revoked or expires.
    """
    try:
        # Validate bearer token and verify authenticated user matches path
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        # Fetch and cache the data using the consent
        result = cached_data_service.fetch_and_cache(
            consent_id=consent_id,
            user_name=user_auth['UserName']
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error fetching and caching external data: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error fetching and caching external data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{user_identifier}/cached-data", response_model=CachedDataResponse)
@limiter.limit("60/minute")
async def retrieve_cached_data(
    request: Request,
    user_identifier: str,
    resource_type: Optional[str] = Query(
        None, description="Restrict to ACCOUNT, PRODUCT, or TRANSACTION"
    ),
    consent_id: Optional[str] = Query(None, description="Restrict to a single consent (one bank)"),
    consent_ids: Optional[str] = Query(
        None,
        description="Comma-separated ConsentIds to restrict to the current browser "
                    "session's banks (prevents cross-session duplicate data)."
    ),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Return a user's cached external data across all connected banks.

    Requires bearer token authentication.

    Reads previously cached data (populated by /fetch-and-cache) grouped by source
    institution. Does not touch the live source, so one-time consents are never
    consumed. Revoked or expired data is already purged and never appears.
    """
    try:
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        consent_id_list = (
            [c for c in (cid.strip() for cid in consent_ids.split(",")) if c]
            if consent_ids is not None else None
        )

        result = cached_data_service.read_cached_data(
            user_name=user_auth['UserName'],
            resource_type=resource_type,
            consent_id=consent_id,
            consent_ids=consent_id_list,
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving cached data: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{user_identifier}/global-position", response_model=GlobalPositionResponse)
@limiter.limit("60/minute")
async def retrieve_global_position(
    request: Request,
    user_identifier: str,
    consent_ids: Optional[str] = Query(
        None,
        description="Comma-separated ConsentIds to restrict the external position to the "
                    "current browser session's banks (prevents double-counting)."
    ),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Compute the user's global position across all connected banks.

    Requires bearer token authentication.

    Combines internal Leafy Bank account balances with cached external data
    (populated by /fetch-and-cache): total balance from accounts, total debt from
    external loans, net worth, and a per-institution breakdown.
    """
    try:
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != user_identifier:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: Bearer token does not match the requested user."
            )

        # Resolve internal Leafy Bank balance (BIAN accounts) to fold into the position.
        # No external accounts passed -> get_user_account_balances returns internal-only.
        internal_balance = 0.0
        customer_id = users_service.get_customer_id(user_auth['UserName'])
        if customer_id:
            internal_balance = account_aggregations.get_user_account_balances(
                customer_id=customer_id, user_id=None
            )["total_balance"]

        consent_id_list = (
            [c for c in (cid.strip() for cid in consent_ids.split(",")) if c]
            if consent_ids is not None else None
        )

        result = cached_data_service.compute_global_position(
            user_name=user_auth['UserName'],
            internal_balance=internal_balance,
            consent_ids=consent_id_list,
        )

        return Response(
            content=json.dumps(result, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error computing global position: {str(e)}")
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
        logger.error(f"Permission error retrieving external transactions: {str(pe)}")
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        logger.error(f"Validation error retrieving external transactions: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving external transactions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
