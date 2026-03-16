from fastapi import APIRouter, Depends, Request, HTTPException, Response, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from dependencies import get_auth, get_bearer_token, get_encrypted_mongo_connection
from pydantic import BaseModel
from typing import List, Dict, Optional
from bson import ObjectId

from dependencies import get_mongo_connection
from services.auth import Auth
from services.external.external_accounts import ExternalAccounts
from services.external.external_products import ExternalFinancialProducts
from services.aggregations.account_aggregations import AccountAggregations
from services.aggregations.product_aggregations import ProductAggregations
from services.consents.consent_validator import ConsentValidator

from encoder.json_encoder import MyJSONEncoder

import json
import logging

import os
from dotenv import load_dotenv

load_dotenv()

# Load environment variables
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")

logger = logging.getLogger(__name__)

router = APIRouter()

# Define the database and collection names

# External
open_finance_db_name = OPENFINANCE_DB_NAME
external_accounts_collection_name = "external_accounts"
external_products_collection_name = "external_products"

# Internal
leafy_bank_db_name = LEAFYBANK_DB_NAME
accounts_collection_name = "accounts"

# Initialize the MongoDB connection
connection = get_mongo_connection()

# Initialize the ExternalAccounts service
external_accounts_service = ExternalAccounts(connection, db_name=open_finance_db_name,
                                             external_accounts_collection_name=external_accounts_collection_name)

# Initialize the ExternalProducts service
external_products_service = ExternalFinancialProducts(connection,  db_name=open_finance_db_name,
                                                      external_products_collection_name=external_products_collection_name
                                                      )

# Initialize the AccountAggregations service
account_aggr_service = AccountAggregations(connection, db1_name=leafy_bank_db_name, collection1_name=accounts_collection_name,
                                           db2_name=open_finance_db_name, collection2_name=external_accounts_collection_name)


# Initialize the ProductAggregations service
product_aggr_service = ProductAggregations(connection, db_name=open_finance_db_name,
                                           collection_name=external_products_collection_name)

# Initialize the ConsentValidator with encrypted connection (Queryable Encryption)
encrypted_connection = get_encrypted_mongo_connection()
consents_collection_name = "encrypted_consents"
consent_validator = ConsentValidator(encrypted_connection, db_name=open_finance_db_name,
                                     consents_collection_name=consents_collection_name)

limiter = Limiter(key_func=get_remote_address)


@router.post("/validate-token")
@limiter.limit("30/minute")
async def validate_token(
    request: Request,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Endpoint for simple Bearer Token health check."""
    user = auth.bearer_token_validation(bearer_token=bearer_token)
    return {"message": f"Bearer Token is valid for user: {user['UserName']}"}


class FetchExternalAccountsResponse(BaseModel):
    accounts: List[Dict]


class FetchExternalProductsResponse(BaseModel):
    products: List[Dict]


@router.get("/fetch-external-accounts-for-user-and-institution/", response_model=FetchExternalAccountsResponse)
@limiter.limit("60/minute")
async def fetch_external_accounts_for_user_and_institution(
    request: Request,
    user_identifier: str,
    institution_name: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Get external accounts for a specific user and institution. Requires valid consent with ACCOUNTS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}")
    if user_auth['UserName'] != user_identifier and str(user_auth['_id']) != user_identifier:
        raise HTTPException(
            status_code=403, detail="Unauthorized: The Bearer Token does not belong to the user_identifier.")
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            consent_id, user_auth['UserName'], "ACCOUNTS_READ"
        )
        if source_institution != institution_name:
            raise ValueError(
                f"Consent is for institution '{source_institution}', not '{institution_name}'."
            )

        if not user_identifier:
            raise HTTPException(
                status_code=400, detail="User identifier is required")
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)
        accounts = external_accounts_service.get_external_accounts_for_user_and_institution(user_identifier, institution_name)
        logger.info(
            f"Found {len(accounts)} external accounts for user {user_identifier} at institution {institution_name}")

        # Record access and consume one-time consents
        consent_validator.record_data_access(consent_id, "EXTERNAL_ACCOUNTS")
        consent_validator.consume_if_one_time(consent)

        return Response(content=json.dumps({"accounts": accounts}, cls=MyJSONEncoder), media_type="application/json")
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving external accounts for user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fetch-external-products-for-user-and-institution/", response_model=FetchExternalProductsResponse)
@limiter.limit("60/minute")
async def fetch_external_products_for_user_and_institution(
    request: Request,
    user_identifier: str,
    institution_name: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Get external financial products for a specific user and institution. Requires valid consent with LOANS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}")
    if user_auth['UserName'] != user_identifier and str(user_auth['_id']) != user_identifier:
        raise HTTPException(
            status_code=403, detail="Unauthorized: The Bearer Token does not belong to the user_identifier.")
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            consent_id, user_auth['UserName'], "LOANS_READ"
        )
        if source_institution != institution_name:
            raise ValueError(
                f"Consent is for institution '{source_institution}', not '{institution_name}'."
            )

        if not user_identifier:
            raise HTTPException(
                status_code=400, detail="User identifier is required")
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)
        products = external_products_service.get_external_products_for_user_and_institution(user_identifier, institution_name)
        logger.info(
            f"Found {len(products)} external products for user {user_identifier} at institution {institution_name}")

        # Record access and consume one-time consents
        consent_validator.record_data_access(consent_id, "EXTERNAL_PRODUCTS")
        consent_validator.consume_if_one_time(consent)

        return Response(content=json.dumps({"products": products}, cls=MyJSONEncoder), media_type="application/json")
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving external products for user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fetch-external-accounts-for-user/", response_model=FetchExternalAccountsResponse)
@limiter.limit("60/minute")
async def fetch_all_external_accounts_for_user(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Get external accounts for a specific user, scoped to the consented institution. Requires valid consent with ACCOUNTS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}")
    if user_auth['UserName'] != user_identifier and str(user_auth['_id']) != user_identifier:
        raise HTTPException(
            status_code=403, detail="Unauthorized: The Bearer Token does not belong to the user_identifier.")
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            consent_id, user_auth['UserName'], "ACCOUNTS_READ"
        )

        if not user_identifier:
            raise HTTPException(
                status_code=400, detail="User identifier is required")
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)
        # Scope results to the consented institution only
        accounts = external_accounts_service.get_external_accounts_for_user_and_institution(user_identifier, source_institution)
        logger.info(
            f"Found {len(accounts)} external accounts for user {user_identifier} at consented institution {source_institution}")

        # Record access and consume one-time consents
        consent_validator.record_data_access(consent_id, "EXTERNAL_ACCOUNTS")
        consent_validator.consume_if_one_time(consent)

        return Response(content=json.dumps({"accounts": accounts}, cls=MyJSONEncoder), media_type="application/json")
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(
            f"Error retrieving all external accounts for user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/fetch-external-products-for-user/", response_model=FetchExternalProductsResponse)
@limiter.limit("60/minute")
async def fetch_all_external_products_for_user(
    request: Request,
    user_identifier: str,
    consent_id: str = Query(..., description="The ConsentId authorizing data retrieval"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Get external financial products for a specific user, scoped to the consented institution. Requires valid consent with LOANS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}")
    if user_auth['UserName'] != user_identifier and str(user_auth['_id']) != user_identifier:
        raise HTTPException(
            status_code=403, detail="Unauthorized: The Bearer Token does not belong to the user_identifier.")
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            consent_id, user_auth['UserName'], "LOANS_READ"
        )

        if not user_identifier:
            raise HTTPException(
                status_code=400, detail="User identifier is required")
        if ObjectId.is_valid(user_identifier):
            user_identifier = ObjectId(user_identifier)
        # Scope results to the consented institution only
        products = external_products_service.get_external_products_for_user_and_institution(user_identifier, source_institution)
        logger.info(
            f"Found {len(products)} external products for user {user_identifier} at consented institution {source_institution}")

        # Record access and consume one-time consents
        consent_validator.record_data_access(consent_id, "EXTERNAL_PRODUCTS")
        consent_validator.consume_if_one_time(consent)

        return Response(content=json.dumps({"products": products}, cls=MyJSONEncoder), media_type="application/json")
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(
            f"Error retrieving all external products for user: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class TotalBalanceRequest(BaseModel):
    user_id: str  # The user's ObjectId as a string
    # List of connected external account IDs (optional)
    connected_external_accounts: Optional[List[str]]
    consent_id: str  # The ConsentId authorizing external data access


class TotalBalanceResponse(BaseModel):
    total_balance: float


@router.post("/calculate-total-balance-for-user/", response_model=TotalBalanceResponse)
@limiter.limit("60/minute")
async def calculate_total_balance_for_user(
    request: Request,
    # Using a Pydantic model to validate request data
    total_balance_request: TotalBalanceRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth),
):
    """Endpoint to retrieve the total balance for a specific user. Requires valid consent with ACCOUNTS_BALANCES_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
    # Ensure the authenticated user matches the user_id being queried
    if user_auth["_id"] != ObjectId(total_balance_request.user_id):
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: User ID does not match the authenticated user.",
        )
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}, UserId: {user_auth['_id']}"
    )
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            total_balance_request.consent_id, user_auth['UserName'], "ACCOUNTS_BALANCES_READ"
        )

        logger.info(
            f"Calculating total balance for user_id: {total_balance_request.user_id}"
        )
        # Call the `get_user_account_balances` method to get the total balance
        balance_data = account_aggr_service.get_user_account_balances(
            total_balance_request.user_id,
            total_balance_request.connected_external_accounts,
        )

        # Record access and consume one-time consents
        consent_validator.record_data_access(total_balance_request.consent_id, "ACCOUNT_BALANCES")
        consent_validator.consume_if_one_time(consent)

        # Return the total balance in the response
        return Response(
            content=json.dumps(balance_data, cls=MyJSONEncoder),
            media_type="application/json",
        )
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error calculating total balance for user: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


class TotalDebtRequest(BaseModel):
    user_id: str  # The user's ObjectId as a string
    # List of connected external product IDs
    connected_external_products: Optional[List[str]]
    consent_id: str  # The ConsentId authorizing external data access


class TotalDebtResponse(BaseModel):
    total_debt: float


@router.post("/calculate-total-debt-for-user/", response_model=TotalDebtResponse)
@limiter.limit("60/minute")
async def calculate_total_debt_for_user(
    request: Request,
    total_debt_request: TotalDebtRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Endpoint to retrieve the total debt for a specific user. Requires valid consent with LOANS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

    # Ensure the authenticated user matches the user_id being queried
    if user_auth["_id"] != ObjectId(total_debt_request.user_id):
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: User ID does not match the authenticated user."
        )
    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}, UserId: {user_auth['_id']}"
    )
    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            total_debt_request.consent_id, user_auth['UserName'], "LOANS_READ"
        )

        logger.info(
            f"Calculating total debt for user_id: {total_debt_request.user_id} with connected products: {total_debt_request.connected_external_products}"
        )
        # Call the `get_user_total_debt` method to get the total debt
        debt_data = product_aggr_service.get_user_total_debt(
            total_debt_request.user_id,
            total_debt_request.connected_external_products
        )

        # Record access and consume one-time consents
        consent_validator.record_data_access(total_debt_request.consent_id, "EXTERNAL_PRODUCTS_DEBT")
        consent_validator.consume_if_one_time(consent)

        # Return the total debt in the response
        return Response(
            content=json.dumps(debt_data, cls=MyJSONEncoder),
            media_type="application/json"
        )
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error calculating total debt for user: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


class ExternalAccountRequest(BaseModel):
    account_bank: str
    user_name: str
    user_id: str
    consent_id: str  # The ConsentId authorizing external data access


@router.post("/retrieve-external-account-for-user")
@limiter.limit("30/minute")
async def retrieve_external_account_for_user(
    request: Request,
    account_data: ExternalAccountRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Endpoint to simulate the retrieval of an external account. Requires valid consent with ACCOUNTS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}")

    # Validation: Both conditions must match
    if user_auth['UserName'] != account_data.user_name or str(user_auth['_id']) != account_data.user_id:
        logger.error(
            "Unauthorized access attempt with mismatched user.")
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: The Bearer Token does not belong to the provided user_name or user_id."
        )

    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            account_data.consent_id, user_auth['UserName'], "ACCOUNTS_READ"
        )
        if source_institution != account_data.account_bank:
            raise ValueError(
                f"Consent is for institution '{source_institution}', not '{account_data.account_bank}'."
            )

        account_id = external_accounts_service.retrieve_external_account_for_user(
            account_bank=account_data.account_bank,
            user_name=account_data.user_name,
            user_id=account_data.user_id
        )

        # Record access and consume one-time consents
        consent_validator.record_data_access(account_data.consent_id, "EXTERNAL_ACCOUNT_RETRIEVAL")
        consent_validator.consume_if_one_time(consent)

        return {"message": f"External account retrieved for {account_data.user_name}.", "account_id": str(account_id)}
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal Server Error")


class ExternalProductRequest(BaseModel):
    product_bank: str
    user_name: str
    user_id: str
    consent_id: str  # The ConsentId authorizing external data access


@router.post("/retrieve-external-product-for-user")
@limiter.limit("30/minute")
async def retrieve_external_product_for_user(
    request: Request,
    product_data: ExternalProductRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """Endpoint to simulate the retrieval of an external financial product. Requires valid consent with LOANS_READ permission."""
    user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

    logger.info(
        f"Authenticated User: UserName: {user_auth['UserName']}; UserId: {user_auth['_id']}"
    )

    # Validation: Ensure the provided user matches the authenticated user
    if user_auth['UserName'] != product_data.user_name or str(user_auth['_id']) != product_data.user_id:
        logger.error(
            "Unauthorized access attempt with mismatched user."
        )
        raise HTTPException(
            status_code=403,
            detail="Unauthorized: The Bearer Token does not belong to the provided user_name or user_id."
        )

    try:
        # Validate consent
        consent, source_institution = consent_validator.validate_consent(
            product_data.consent_id, user_auth['UserName'], "LOANS_READ"
        )
        if source_institution != product_data.product_bank:
            raise ValueError(
                f"Consent is for institution '{source_institution}', not '{product_data.product_bank}'."
            )

        # Retrieve the external financial product for the user
        product_id = external_products_service.retrieve_external_product_for_user(
            product_bank=product_data.product_bank,
            user_name=product_data.user_name,
            user_id=product_data.user_id
        )

        # Record access and consume one-time consents
        consent_validator.record_data_access(product_data.consent_id, "EXTERNAL_PRODUCT_RETRIEVAL")
        consent_validator.consume_if_one_time(consent)

        return {
            "message": f"External financial product retrieved for {product_data.user_name}.",
            "product_id": str(product_id)
        }
    except ValueError as ve:
        logger.error(f"Consent validation error: {str(ve)}")
        raise HTTPException(status_code=403, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error retrieving external financial product: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
