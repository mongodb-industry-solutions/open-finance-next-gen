"""
Scenario Service

Manages variable transaction scenarios — pre-built transaction packs that load
on top of base seed data to change the spending profile per demo scenario.

Variable transactions follow ISO 20022 format but have NO MCC codes (no BkTxCd.Prtry),
are flagged with TxVar: true for identification/cleanup, and have rich AddtlNtryInf
descriptions for future vector search classification.

Scenario JSON files are organized per-user under data/scenarios/{user_name}/.
Each user has tailored merchants and amounts matching their persona (e.g. hellyrig
uses budget stores and NeoFinance, fridaklo uses premium dining and Green Bank).
"""

import copy
from typing import List, Dict, Optional
from database.connection import MongoDBConnection
import json
import os
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

VALID_SCENARIOS = ["overspender", "balanced", "saver"]

# Known user profiles with their external bank account details.
# Used to patch scenario templates with the correct Dbtr/Acct for each user.
USER_PROFILES = {
    "fridaklo": {
        "Dbtr": {"Nm": "fridaklo", "Id": {"$oid": "65a546ae4a8f64e8f88fb89e"}},
        "Acct": {"Id": "204515807", "Tp": "CACC", "Svcr": "Green Bank"},
    },
    "hellyrig": {
        "Dbtr": {"Nm": "hellyrig", "Id": {"$oid": "67a8c3de4b9f52d1a33ec701"}},
        "Acct": {"Id": "730891245", "Tp": "CACC", "Svcr": "NeoFinance"},
    },
}


class ScenarioService:
    """Service for loading, clearing, and querying variable transaction scenarios."""

    def __init__(self, connection: MongoDBConnection, db_name: str, collection_name: str):
        self.collection = connection.get_collection(db_name, collection_name)
        self.scenarios_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "..", "data", "scenarios"
        )

    def load_scenario(self, scenario_name: str, user_name: str = "fridaklo") -> Dict:
        """Clear existing variable txns for user and load a user-specific scenario pack.

        Scenario files live at data/scenarios/{user_name}/{scenario_name}.json.
        Each file already contains the correct Dbtr/Acct for the user — no patching needed.

        Idempotent — loading the same scenario twice doesn't double transactions.

        Args:
            scenario_name: One of 'overspender', 'balanced', 'saver'
            user_name: User to load for (must exist in USER_PROFILES)

        Returns:
            Dict with scenario name, cleared count, loaded count

        Raises:
            ValueError: Invalid scenario name or unknown user
            FileNotFoundError: Scenario JSON file not found
        """
        if scenario_name not in VALID_SCENARIOS:
            raise ValueError(
                f"Invalid scenario: '{scenario_name}'. Valid options: {VALID_SCENARIOS}"
            )

        if user_name not in USER_PROFILES:
            raise ValueError(
                f"Unknown user: '{user_name}'. Valid users: {list(USER_PROFILES.keys())}"
            )

        scenario_file = os.path.join(self.scenarios_dir, user_name, f"{scenario_name}.json")
        if not os.path.exists(scenario_file):
            raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

        with open(scenario_file, "r") as f:
            raw_transactions = json.load(f)

        # Validate and collect transactions
        profile = USER_PROFILES[user_name]
        transactions = []
        for i, txn in enumerate(raw_transactions):
            txn = copy.deepcopy(txn)

            # Validate structure
            if not txn.get("TxVar"):
                raise ValueError(f"Transaction {i}: missing TxVar: true flag")
            if txn.get("BkTxCd", {}).get("Prtry"):
                raise ValueError(
                    f"Transaction {i}: has BkTxCd.Prtry (MCC code) — variable transactions must be untagged"
                )

            transactions.append(txn)

        # Clear existing variable transactions for this user first
        clear_result = self.collection.delete_many({"TxVar": True, "Dbtr.Nm": user_name})
        cleared = clear_result.deleted_count

        # Insert new scenario transactions
        result = self.collection.insert_many(transactions)
        loaded = len(result.inserted_ids)

        logging.info(
            f"Scenario '{scenario_name}': cleared {cleared} variable txns, "
            f"loaded {loaded} for user '{user_name}' at {profile['Acct']['Svcr']}"
        )

        return {
            "scenario": scenario_name,
            "user_name": user_name,
            "bank": profile["Acct"]["Svcr"],
            "cleared": cleared,
            "loaded": loaded,
            "message": f"Loaded {loaded} variable transactions for '{user_name}' ({scenario_name})"
        }

    def clear_variable_transactions(self, user_name: Optional[str] = None) -> Dict:
        """Delete all variable transactions (TxVar: true). Base data is never touched.

        Args:
            user_name: Optional filter — if provided, only clear that user's variable txns

        Returns:
            Dict with deleted count
        """
        query = {"TxVar": True}
        if user_name:
            query["Dbtr.Nm"] = user_name

        result = self.collection.delete_many(query)
        deleted = result.deleted_count

        logging.info(
            f"Cleared {deleted} variable transactions"
            + (f" for user '{user_name}'" if user_name else "")
        )

        return {
            "cleared": deleted,
            "user_filter": user_name,
            "message": f"Cleared {deleted} variable transactions"
        }

    def get_scenario_status(self, user_name: Optional[str] = None) -> Dict:
        """Get counts of base vs variable transactions and amount breakdown.

        Args:
            user_name: Optional filter by Dbtr.Nm

        Returns:
            Dict with base_count, variable_count, total, and variable amount sum
        """
        base_query = {"TxVar": {"$ne": True}}
        variable_query = {"TxVar": True}
        if user_name:
            base_query["Dbtr.Nm"] = user_name
            variable_query["Dbtr.Nm"] = user_name

        base_count = self.collection.count_documents(base_query)
        variable_count = self.collection.count_documents(variable_query)

        # Sum variable transaction amounts
        variable_amount_pipeline = [
            {"$match": variable_query},
            {"$group": {"_id": None, "total": {"$sum": "$Amt.value"}}}
        ]
        amount_result = list(self.collection.aggregate(variable_amount_pipeline))
        variable_total_amount = round(amount_result[0]["total"], 2) if amount_result else 0

        return {
            "base_transactions": base_count,
            "variable_transactions": variable_count,
            "total_transactions": base_count + variable_count,
            "variable_total_amount": variable_total_amount,
            "user_filter": user_name,
        }

    def list_available_scenarios(self) -> List[Dict]:
        """Return metadata for available scenario packs.

        Lists which scenario files exist per user based on the scenarios directory.
        """
        scenarios = [
            {
                "name": "overspender",
                "transactions": 15,
                "profile": "High discretionary spending — drops spending score"
            },
            {
                "name": "balanced",
                "transactions": 15,
                "profile": "Even distribution — keeps spending score stable"
            },
            {
                "name": "saver",
                "transactions": 15,
                "profile": "Low discretionary, high savings — raises spending score"
            },
        ]

        # Check which users have scenario files
        available_users = []
        for user_name in USER_PROFILES:
            user_dir = os.path.join(self.scenarios_dir, user_name)
            if os.path.isdir(user_dir):
                available_users.append(user_name)

        return {
            "scenarios": scenarios,
            "users": available_users,
        }
