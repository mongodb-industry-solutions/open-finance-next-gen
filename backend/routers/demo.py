"""
Demo API Router

Endpoints for demo profile metadata. Spending profiles are baked into the
database at seed time — no load/clear needed. Clients pass a `profile` query
parameter to filter transactions at query time.
"""

from fastapi import APIRouter
from typing import Dict, Any, List

router = APIRouter()

PROFILES: List[Dict[str, Any]] = [
    {
        "name": "overspender",
        "transactions": 15,
        "description": "High discretionary spending — drops spending score",
    },
    {
        "name": "balanced",
        "transactions": 15,
        "description": "Even distribution — keeps spending score stable",
    },
    {
        "name": "saver",
        "transactions": 15,
        "description": "Low discretionary, high savings — raises spending score",
    },
]

USERS = ["fridaklo", "hellyrig"]


@router.get("/profiles")
async def get_profiles() -> Dict[str, Any]:
    """
    List available spending profiles and demo users.

    Profiles are pre-seeded in the database. Select one via the `profile`
    query parameter on the external-data or external-transactions endpoints.
    """
    return {
        "profiles": PROFILES,
        "users": USERS,
    }