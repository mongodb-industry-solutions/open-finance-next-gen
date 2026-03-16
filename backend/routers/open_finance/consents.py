from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
import json

from dependencies import get_auth, get_bearer_token, get_encrypted_mongo_connection, get_mongo_connection
from utils.security import sanitize_log_input
from services.auth import Auth
from services.consents.consent_service import ConsentService
from services.internal.users_service import UsersService
from encoder.json_encoder import MyJSONEncoder

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize the encrypted MongoDB connection (Queryable Encryption on consents)
encrypted_connection = get_encrypted_mongo_connection()

# Get the database name from the environment variable
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")

# Collection names
CONSENTS_COLLECTION = "encrypted_consents"
INSTITUTIONS_COLLECTION = "institutions"

# Initialize UsersService for user lookups (users are in leafy_bank database)
connection = get_mongo_connection()
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")
USERS_COLLECTION = "users"
users_service = UsersService(connection, LEAFYBANK_DB_NAME, USERS_COLLECTION)

# Initialize the ConsentService with encrypted connection
consent_service = ConsentService(
    encrypted_connection,
    OPENFINANCE_DB_NAME,
    CONSENTS_COLLECTION,
    INSTITUTIONS_COLLECTION
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# Define Pydantic Models
class CreateConsentRequest(BaseModel):
    consumer_id: str  # UserName or UserId - validated against bearer token
    purpose: Optional[str] = None  # None = general access (all permissions). Or: PERSONAL_LOAN_PORTABILITY | PAYROLL_LOAN_PORTABILITY | VEHICLE_LOAN_PORTABILITY | FINANCIAL_ADVICE
    source_institution_name: str  # must match an existing institution's InstitutionName
    expiration_days: int  # Required: 3-12 (treated as minutes in demo mode)
    permissions: Optional[List[str]] = None  # Optional: subset of purpose's default permissions. Auto-assigned if omitted.


class UpdateStatusRequest(BaseModel):
    status: str  # the target status
    rejection_reason: Optional[Dict] = None  # required if status is REJECTED


class ConsentResponse(BaseModel):
    consent: Dict


class ConsentListResponse(BaseModel):
    consents: List[Dict]


class MessageResponse(BaseModel):
    message: str


# Define API Endpoints

@router.post("/", response_model=ConsentResponse, status_code=201)
@limiter.limit("30/minute")
async def create_consent(
    request: Request,
    consent_data: CreateConsentRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Create a new consent for data sharing. Requires bearer token authentication.

    The source_institution_name must be a valid institution in the system.
    Valid purposes: PERSONAL_LOAN_PORTABILITY, PAYROLL_LOAN_PORTABILITY, VEHICLE_LOAN_PORTABILITY, FINANCIAL_ADVICE
    Or omit purpose (null) for general access — grants all permissions.

    Permissions are auto-assigned based on purpose (or all permissions if no purpose).
    If permissions are provided, they must be a subset of the allowed set (user can remove but not add).
    """
    try:
        # Validate Bearer Token and verify ownership
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != consent_data.consumer_id and str(user_auth['_id']) != consent_data.consumer_id:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only create consents for yourself."
            )

        # Look up the user's real ObjectId for Consumer.UserId
        user = users_service.get_user(consent_data.consumer_id)
        if not user:
            raise HTTPException(status_code=404, detail="Consumer user not found")

        # Create the consent
        consent = consent_service.create_consent(
            consumer_user_name=consent_data.consumer_id,
            consumer_user_id=str(user["_id"]),
            purpose=consent_data.purpose,
            source_institution_name=consent_data.source_institution_name,
            expiration_days=consent_data.expiration_days,
            permissions=consent_data.permissions
        )

        return Response(
            content=json.dumps({"consent": consent}, cls=MyJSONEncoder),
            media_type="application/json",
            status_code=201
        )

    except ValueError as ve:
        logger.error(f"Validation error creating consent: {str(ve)}")
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error creating consent: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/", response_model=ConsentListResponse)
@limiter.limit("60/minute")
async def list_consents(
    request: Request,
    consumer_id: str = Query(..., description="The consumer's UserName or UserId"),
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    List all consents for a specific user. Requires bearer token authentication.
    """
    try:
        # Validate Bearer Token and verify ownership
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)
        if user_auth['UserName'] != consumer_id and str(user_auth['_id']) != consumer_id:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only list your own consents."
            )

        consents = consent_service.list_consents_for_user(consumer_id)

        return Response(
            content=json.dumps({"consents": consents}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error listing consents: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{consent_id}", response_model=ConsentResponse)
@limiter.limit("60/minute")
async def get_consent(
    request: Request,
    consent_id: str,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Get a specific consent by its ConsentId. Requires bearer token authentication.
    """
    try:
        # Validate Bearer Token
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

        consent = consent_service.get_consent(consent_id)

        if not consent:
            raise HTTPException(status_code=404, detail=f"Consent '{consent_id}' not found.")

        # Verify the consent belongs to the authenticated user
        if consent['Consumer']['UserName'] != user_auth['UserName']:
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only view your own consents."
            )

        return Response(
            content=json.dumps({"consent": consent}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error getting consent {sanitize_log_input(consent_id)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.patch("/{consent_id}/status", response_model=ConsentResponse)
@limiter.limit("30/minute")
async def update_consent_status(
    request: Request,
    consent_id: str,
    status_data: UpdateStatusRequest,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Update the status of a consent.

    This is a GREEN BANK (source institution) endpoint - requires bearer token authentication.
    User must authenticate at the source bank before updating consent status.

    Valid transitions:
    - AWAITING_AUTHORISATION -> AUTHORISED, REJECTED
    - AUTHORISED -> CONSUMED, REVOKED

    If setting status to REJECTED, rejection_reason is required.
    """
    try:
        # Validate Bearer Token and get authenticated user
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

        # Fetch consent to verify ownership
        consent = consent_service.get_consent(consent_id)
        if not consent:
            raise HTTPException(status_code=404, detail=f"Consent '{consent_id}' not found.")

        # Verify the consent belongs to the authenticated user
        if consent['Consumer']['UserName'] != user_auth['UserName']:
            logger.error("Unauthorized: consent does not belong to authenticated user")
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only modify your own consents."
            )

        # Update the status
        updated_consent = consent_service.update_status(
            consent_id=consent_id,
            new_status=status_data.status,
            rejection_reason=status_data.rejection_reason
        )

        if not updated_consent:
            raise HTTPException(status_code=500, detail="Failed to update consent status.")

        return Response(
            content=json.dumps({"consent": updated_consent}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error updating consent status: {str(ve)}")
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error updating consent status {sanitize_log_input(consent_id)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.post("/{consent_id}/approve", response_model=ConsentResponse)
@limiter.limit("30/minute")
async def approve_consent(
    request: Request,
    consent_id: str,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Approve a consent at the source institution (Green Bank).

    This is a GREEN BANK (source institution) endpoint - requires bearer token authentication.
    User must first authenticate at the source bank (GET /public/get-authorization)
    before they can approve the consent.

    Transitions consent from AWAITING_AUTHORISATION to AUTHORISED.
    """
    try:
        # Validate Bearer Token and get authenticated user
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

        # Fetch consent to verify ownership
        consent = consent_service.get_consent(consent_id)
        if not consent:
            raise HTTPException(status_code=404, detail=f"Consent '{consent_id}' not found.")

        # Verify the consent belongs to the authenticated user
        if consent['Consumer']['UserName'] != user_auth['UserName']:
            logger.error("Unauthorized: consent does not belong to authenticated user")
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only approve your own consents."
            )

        # Simulate approval
        updated_consent = consent_service.simulate_approval(consent_id)

        if not updated_consent:
            raise HTTPException(status_code=500, detail="Failed to approve consent.")

        return Response(
            content=json.dumps({"consent": updated_consent}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error approving consent: {str(ve)}")
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error approving consent {sanitize_log_input(consent_id)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.delete("/{consent_id}", response_model=ConsentResponse)
@limiter.limit("30/minute")
async def revoke_consent(
    request: Request,
    consent_id: str,
    bearer_token: str = Depends(get_bearer_token),
    auth: Auth = Depends(get_auth)
):
    """
    Revoke an authorized consent at the source institution (Green Bank).

    This is a GREEN BANK (source institution) endpoint - requires bearer token authentication.
    User must authenticate at the source bank before they can revoke the consent.

    Transitions consent from AUTHORISED to REVOKED.
    Only authorized consents can be revoked.
    """
    try:
        # Validate Bearer Token and get authenticated user
        user_auth = auth.bearer_token_validation(bearer_token=bearer_token)

        # Fetch consent to verify ownership
        consent = consent_service.get_consent(consent_id)
        if not consent:
            raise HTTPException(status_code=404, detail=f"Consent '{consent_id}' not found.")

        # Verify the consent belongs to the authenticated user
        if consent['Consumer']['UserName'] != user_auth['UserName']:
            logger.error("Unauthorized: consent does not belong to authenticated user")
            raise HTTPException(
                status_code=403,
                detail="Unauthorized: You can only revoke your own consents."
            )

        # Revoke the consent
        updated_consent = consent_service.revoke_consent(consent_id)

        if not updated_consent:
            raise HTTPException(status_code=500, detail="Failed to revoke consent.")

        return Response(
            content=json.dumps({"consent": updated_consent}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except ValueError as ve:
        logger.error(f"Validation error revoking consent: {str(ve)}")
        raise HTTPException(status_code=400, detail=str(ve))
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error revoking consent {sanitize_log_input(consent_id)}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
