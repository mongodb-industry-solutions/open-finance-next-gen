from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict, Optional
from pydantic import BaseModel
import logging
import json

from dependencies import get_auth, get_bearer_token, get_mongo_connection
from services.auth import Auth
from services.internal.product_service import ProductService
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

# Collection name
PRODUCTS_COLLECTION = "products"

# Initialize the ProductService
product_service = ProductService(
    connection, LEAFYBANK_DB_NAME, PRODUCTS_COLLECTION
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# Define Pydantic Models
class ProductListResponse(BaseModel):
    products: List[Dict]


class ProductResponse(BaseModel):
    product: Dict


class ProductMatchResponse(BaseModel):
    matches: List[Dict]
    current_rate: float
    product_type: str


# Define API Endpoints

# IMPORTANT: /match must be defined BEFORE /{product_id}
# Otherwise FastAPI treats "match" as a product_id path parameter

@router.get("/", response_model=ProductListResponse)
@limiter.limit("60/minute")
async def list_products(
    request: Request,
    product_type: Optional[str] = Query(None, description="Filter by product type: Loan, Mortgage, CreditCard")
):
    """
    List all active Leafy Bank products.

    Optionally filter by product type (Loan, Mortgage, CreditCard).

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank
        products = product_service.list_products(product_type=product_type)

        return Response(
            content=json.dumps({"products": products}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error listing products: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/match", response_model=ProductMatchResponse)
@limiter.limit("60/minute")
async def match_products(
    request: Request,
    product_type: str = Query(..., description="Product type: Loan, CreditCard"),
    current_rate: float = Query(..., description="Your current interest rate/APR to compare against"),
    current_amount: Optional[float] = Query(None, description="Loan amount to check eligibility"),
    loan_sub_type: Optional[str] = Query(None, description="Loan subtype: Personal, PayrollDeductible, Vehicle")
):
    """
    Find Leafy Bank products with better rates than your current product.

    This is the core endpoint for loan/credit card portability - it finds
    better alternatives to the user's current financial products.

    For loans, provide loan_sub_type to match the correct Leafy Bank product
    (e.g., a Vehicle loan at 4.63% matches Leafy Bank's Vehicle loan at 3.10%).

    Returns products sorted by rate (best first) with a 'rate_improvement' field
    showing how much you could save.

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank

        # Validate product_type
        valid_types = ["Loan", "CreditCard"]
        if product_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid product_type. Must be one of: {valid_types}"
            )

        # Validate loan_sub_type if provided
        if loan_sub_type:
            valid_sub_types = ["Personal", "PayrollDeductible", "Vehicle"]
            if loan_sub_type not in valid_sub_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid loan_sub_type. Must be one of: {valid_sub_types}"
                )

        matches = product_service.match_products(
            product_type=product_type,
            current_rate=current_rate,
            current_amount=current_amount,
            loan_sub_type=loan_sub_type
        )

        return Response(
            content=json.dumps({
                "matches": matches,
                "current_rate": current_rate,
                "product_type": product_type
            }, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error matching products: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


@router.get("/{product_id}", response_model=ProductResponse)
@limiter.limit("60/minute")
async def get_product(
    request: Request,
    product_id: str
):
    """
    Get details of a specific Leafy Bank product by its ProductId.

    Example product IDs: lb-loan-001, lb-mortgage-001, lb-cc-001

    This is a Leafy Bank endpoint - user is already authenticated via session.
    No bearer token required.
    """
    try:
        # No bearer token validation - user is already logged into Leafy Bank

        product = product_service.get_product(product_id)

        if not product:
            raise HTTPException(
                status_code=404,
                detail=f"Product '{product_id}' not found."
            )

        return Response(
            content=json.dumps({"product": product}, cls=MyJSONEncoder),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error getting product {product_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
