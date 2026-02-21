"""
Demo API Router

Endpoints for demo scenario management — loading variable transaction packs
that change the spending profile per user.
"""

from fastapi import APIRouter, Depends
from typing import Dict, Any, Optional
from database.connection import MongoDBConnection
from dependencies import get_mongo_connection
from services.internal.scenario_service import ScenarioService
from bson import json_util
import json
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME", "open_finance_test")

# Must match the collection used in routers/open_finance/customer_data.py
# TODO: revert to "external_transactions" when ISO 20022 migration is complete
EXTERNAL_TRANSACTIONS_COLLECTION = "external_transactions_test"


def serialize_mongo_doc(doc: Dict) -> Dict:
    """Convert MongoDB document to JSON-serializable format."""
    return json.loads(json_util.dumps(doc))


@router.post("/scenarios/{scenario_name}")
async def load_scenario(
    scenario_name: str,
    user_name: str = "fridaklo",
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """
    Load a variable transaction scenario pack for a user.

    Clears any existing variable transactions for the user, then loads the
    scenario's transactions with the user's identity patched in.
    Idempotent — loading the same scenario twice won't double transactions.

    Args:
        scenario_name: One of 'overspender', 'balanced', 'saver'
        user_name: User to load transactions for (fridaklo or hellyrig)
    """
    try:
        scenario_service = ScenarioService(
            mongo_connection, OPENFINANCE_DB_NAME, EXTERNAL_TRANSACTIONS_COLLECTION
        )
        result = scenario_service.load_scenario(scenario_name, user_name)
        return result
    except (ValueError, FileNotFoundError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@router.delete("/scenarios")
async def clear_scenarios(
    user_name: Optional[str] = None,
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """
    Clear all variable transactions (TxVar: true). Base data is never touched.

    Args:
        user_name: Optional — if provided, only clear that user's variable transactions
    """
    try:
        scenario_service = ScenarioService(
            mongo_connection, OPENFINANCE_DB_NAME, EXTERNAL_TRANSACTIONS_COLLECTION
        )
        result = scenario_service.clear_variable_transactions(user_name)
        return result
    except Exception as e:
        return {"error": str(e)}


@router.get("/scenarios")
async def get_scenarios(
    user_name: Optional[str] = None,
    mongo_connection: MongoDBConnection = Depends(get_mongo_connection)
) -> Dict[str, Any]:
    """
    Get scenario status and list available scenario packs.

    Returns current variable transaction counts and available scenarios.

    Args:
        user_name: Optional — if provided, filter status by user
    """
    try:
        scenario_service = ScenarioService(
            mongo_connection, OPENFINANCE_DB_NAME, EXTERNAL_TRANSACTIONS_COLLECTION
        )
        status = scenario_service.get_scenario_status(user_name)
        available = scenario_service.list_available_scenarios()
        return {
            **status,
            "available_scenarios": available,
        }
    except Exception as e:
        return {"error": str(e)}
