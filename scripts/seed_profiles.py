"""Seed all spending profile transactions into the external_transactions collection.

Loads all 6 scenario JSON files (3 profiles x 2 users) and inserts them into MongoDB
as permanent, read-only data. These transactions have a `Profile` field and are
filtered at query time — no load/clear needed for multi-user demo isolation.

Usage:
    cd repos/open-finance-next-gen/backend && poetry run python ../scripts/seed_profiles.py

Requires MONGODB_URI and OPENFINANCE_DB_NAME in backend/.env
"""

import json
import os
import sys

from dotenv import load_dotenv
from pymongo import MongoClient

# Load env from the backend directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPT_DIR, "..", "backend")
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("OPENFINANCE_DB_NAME", "open_finance_test")
COLLECTION = "external_transactions_test"
SCENARIOS_DIR = os.path.join(BACKEND_DIR, "data", "scenarios")

USERS = ["fridaklo", "hellyrig"]
PROFILES = ["overspender", "balanced", "saver"]


def main():
    if not MONGODB_URI:
        print("ERROR: MONGODB_URI not set in .env")
        sys.exit(1)

    client = MongoClient(MONGODB_URI)
    collection = client[DB_NAME][COLLECTION]

    # Step 1: Clean up any existing profile or TxVar transactions
    deleted_txvar = collection.delete_many({"TxVar": True}).deleted_count
    if deleted_txvar:
        print(f"Cleaned up {deleted_txvar} old TxVar transactions")

    deleted_profile = collection.delete_many({"Profile": {"$exists": True}}).deleted_count
    if deleted_profile:
        print(f"Cleaned up {deleted_profile} existing profile transactions")

    # Step 2: Load and insert all profile transactions
    total_inserted = 0
    for user in USERS:
        for profile in PROFILES:
            filepath = os.path.join(SCENARIOS_DIR, user, f"{profile}.json")
            if not os.path.exists(filepath):
                print(f"WARNING: Missing {filepath}, skipping")
                continue

            with open(filepath) as f:
                transactions = json.load(f)

            # Validate each transaction has Profile and no TxVar
            for i, txn in enumerate(transactions):
                if txn.get("TxVar"):
                    print(
                        f"ERROR: {filepath} txn {i} still has TxVar:true — "
                        "update JSON files first (replace TxVar with Profile)"
                    )
                    sys.exit(1)
                if txn.get("Profile") != profile:
                    print(
                        f"ERROR: {filepath} txn {i} has Profile='{txn.get('Profile')}' "
                        f"expected '{profile}'"
                    )
                    sys.exit(1)

            result = collection.insert_many(transactions)
            count = len(result.inserted_ids)
            total_inserted += count
            print(f"  Inserted {count} txns: {user}/{profile}")

    # Step 3: Verify
    base_count = collection.count_documents({"Profile": {"$exists": False}})
    profile_count = collection.count_documents({"Profile": {"$exists": True}})

    print(f"\nProfile breakdown:")
    for profile in PROFILES:
        p_count = collection.count_documents({"Profile": profile})
        print(f"  {profile}: {p_count} transactions")

    print(f"\nDone. Inserted {total_inserted} profile transactions.")
    print(f"Collection totals: {base_count} base + {profile_count} profile = {base_count + profile_count}")


if __name__ == "__main__":
    main()
