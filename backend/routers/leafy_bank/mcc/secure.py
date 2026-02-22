from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from typing import List, Dict
from pydantic import BaseModel
import logging
import json
import os
from dotenv import load_dotenv

from dependencies import get_mongo_connection
from services.internal.mcc_classification_service import MCCClassificationService
from encoder.json_encoder import MyJSONEncoder
from fastapi.responses import Response

load_dotenv()

# Set up logging configuration
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

router = APIRouter()

# Initialize the MongoDB connection (singleton)
connection = get_mongo_connection()

# Get the database name from the environment variable
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME")

# Initialize the MCC classification service
mcc_service = MCCClassificationService(
    connection,
    LEAFYBANK_DB_NAME,
    "mcc_codes"
)

# Define a rate limiter
limiter = Limiter(key_func=get_remote_address)


# --- Request/Response models ---

class TransactionInput(BaseModel):
    merchant: str = ""
    description: str = ""
    amount: float = 0.0
    bank: str = ""
    mcc: str = ""


class ClassifyRequest(BaseModel):
    transactions: List[TransactionInput]


class ClassifyResponse(BaseModel):
    classifications: List[Dict]
    total_classified: int


class MCCCodesResponse(BaseModel):
    codes: List[Dict]
    total: int


# --- POST /classify ---

@router.post("/classify", response_model=ClassifyResponse)
@limiter.limit("60/minute")
async def classify_transactions(request: Request, body: ClassifyRequest):
    """
    Classify untagged transactions by MCC code via Atlas Vector Search.

    Accepts a list of transactions with merchant names and descriptions,
    embeds them with voyage-finance-2, and matches against the MCC reference
    collection using MongoDB Atlas Vector Search.

    This is a Leafy Bank internal endpoint — no consent or auth required.
    Classification is ephemeral: results are returned but not persisted.

    Returns each transaction with its matched MCC code, spending category,
    and vector search confidence score.
    """
    try:
        txn_dicts = [txn.model_dump() for txn in body.transactions]
        classifications = mcc_service.classify_batch(txn_dicts)

        classified_count = sum(
            1 for c in classifications if c.get("CategoryId") != "uncategorized"
        )

        return Response(
            content=json.dumps(
                {"classifications": classifications, "total_classified": classified_count},
                cls=MyJSONEncoder
            ),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error classifying transactions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


# --- GET /codes ---

@router.get("/codes", response_model=MCCCodesResponse)
@limiter.limit("60/minute")
async def get_mcc_codes(request: Request):
    """
    Retrieve all MCC reference codes (without embeddings).

    Returns the full MCC code catalog with categories, descriptions,
    and keywords. Useful for understanding the classification taxonomy.
    """
    try:
        codes = mcc_service.get_all_codes()

        return Response(
            content=json.dumps(
                {"codes": codes, "total": len(codes)},
                cls=MyJSONEncoder
            ),
            media_type="application/json"
        )

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Error retrieving MCC codes: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
