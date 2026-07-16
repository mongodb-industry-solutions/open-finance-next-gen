"""
Debug API Router

Exposes MongoDB collection data for debugging purposes.
This endpoint is for development/debugging only.
"""

from fastapi import APIRouter, Depends
from typing import Dict, Any
from database.connection import MongoDBConnection
from dependencies import get_mongo_connection
from bson import json_util
import json
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME", "open_finance_test")
LEAFYBANK_DB_NAME = os.getenv("LEAFYBANK_DB_NAME", "leafy_bank_test")

# Define collections to expose for each database
OPENFINANCE_COLLECTIONS = [
    "institutions",
    "external_accounts",
    "external_products",
    "external_transactions",
    "tokens",
    "customers",
]

LEAFYBANK_COLLECTIONS = [
    "accounts",
    "customers",
    "transactions",
    "openBankingConsents",
]


def serialize_mongo_doc(doc: Dict) -> Dict:
    """Convert MongoDB document to JSON-serializable format."""
    return json.loads(json_util.dumps(doc))


@router.get("/databases")
async def list_databases(
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """List available databases and their collections."""
    return {
        "databases": [
            {
                "name": OPENFINANCE_DB_NAME,
                "collections": OPENFINANCE_COLLECTIONS,
            },
            {
                "name": LEAFYBANK_DB_NAME,
                "collections": LEAFYBANK_COLLECTIONS,
            },
        ]
    }


@router.get("/collections/{db_name}/{collection_name}")
async def get_collection_data(
    db_name: str,
    collection_name: str,
    limit: int = 50,
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """
    Get documents from a collection.

    Args:
        db_name: Database name (open_finance_test or leafy_bank_test)
        collection_name: Name of the collection
        limit: Maximum number of documents to return (default 50)
    """
    # Validate database and collection
    valid_collections = {
        OPENFINANCE_DB_NAME: OPENFINANCE_COLLECTIONS,
        LEAFYBANK_DB_NAME: LEAFYBANK_COLLECTIONS,
    }

    if db_name not in valid_collections:
        return {"error": f"Invalid database: {db_name}"}

    if collection_name not in valid_collections[db_name]:
        return {"error": f"Invalid collection: {collection_name} for database {db_name}"}

    try:
        db = mongo_connection.get_database(db_name)
        collection = db[collection_name]

        # Get document count
        total_count = collection.count_documents({})

        # Get documents with limit
        documents = list(collection.find().limit(limit))

        # Serialize documents
        serialized_docs = [serialize_mongo_doc(doc) for doc in documents]

        return {
            "database": db_name,
            "collection": collection_name,
            "total_count": total_count,
            "returned_count": len(serialized_docs),
            "limit": limit,
            "documents": serialized_docs,
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/collections/{db_name}")
async def list_collection_stats(
    db_name: str,
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """
    Get stats for all collections in a database.

    Args:
        db_name: Database name
    """
    valid_collections = {
        OPENFINANCE_DB_NAME: OPENFINANCE_COLLECTIONS,
        LEAFYBANK_DB_NAME: LEAFYBANK_COLLECTIONS,
    }

    if db_name not in valid_collections:
        return {"error": f"Invalid database: {db_name}"}

    try:
        db = mongo_connection.get_database(db_name)
        stats = []

        for collection_name in valid_collections[db_name]:
            collection = db[collection_name]
            count = collection.count_documents({})
            stats.append({
                "name": collection_name,
                "count": count,
            })

        return {
            "database": db_name,
            "collections": stats,
        }
    except Exception as e:
        return {"error": str(e)}
