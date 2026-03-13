"""
Migrate existing consents from `consents` to `encrypted_consents`.

Reads all documents from the old plaintext collection via a plain client,
inserts each into the new encrypted collection via an encrypted client
(auto-encrypts on write), and verifies decryption round-trips correctly.

Prerequisites:
    - Run setup_encrypted_consents.py first to create the encrypted collection
    - backend/encryption_config.json must exist

Usage:
    cd backend && poetry run python ../scripts/migrate_consents.py
"""

import os
import sys
from pathlib import Path

from bson import json_util
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.encryption_options import AutoEncryptionOpts

# Load .env from backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
load_dotenv(BACKEND_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    print("ERROR: MONGODB_URI not set in backend/.env")
    sys.exit(1)

DB_NAME = os.getenv("OPENFINANCE_DB_NAME", "open_finance_test")
OLD_COLL = "consents"
NEW_COLL = "encrypted_consents"
KEY_VAULT_NAMESPACE = "encryption.__keyVault"
MASTER_KEY_PATH = BACKEND_DIR / "master-key.bin"
CONFIG_PATH = BACKEND_DIR / "encryption_config.json"
CRYPT_SHARED_LIB_PATH = os.getenv(
    "CRYPT_SHARED_LIB_PATH",
    str(BACKEND_DIR / "lib" / "mongo_crypt_v1.dylib"),
)


def main():
    print("=" * 60)
    print("Migrate Consents: plaintext → encrypted")
    print("=" * 60)

    # Validate prerequisites
    if not CONFIG_PATH.exists():
        print(f"ERROR: {CONFIG_PATH} not found.")
        print("Run setup_encrypted_consents.py first.")
        sys.exit(1)

    if not MASTER_KEY_PATH.exists():
        print(f"ERROR: {MASTER_KEY_PATH} not found.")
        sys.exit(1)

    # Load encryption config
    config = json_util.loads(CONFIG_PATH.read_text())
    master_key = MASTER_KEY_PATH.read_bytes()
    kms_providers = {"local": {"key": master_key}}

    # Create plain client (for reading old collection)
    plain_client = MongoClient(MONGODB_URI)
    old_coll = plain_client[DB_NAME][OLD_COLL]

    # Create encrypted client (for writing to new collection)
    auto_opts = AutoEncryptionOpts(
        kms_providers,
        config["key_vault_namespace"],
        encrypted_fields_map=config["encrypted_fields_map"],
        crypt_shared_lib_path=CRYPT_SHARED_LIB_PATH,
    )
    encrypted_client = MongoClient(MONGODB_URI, auto_encryption_opts=auto_opts)
    new_coll = encrypted_client[DB_NAME][NEW_COLL]

    # Check existing document count in new collection
    existing_count = new_coll.count_documents({})
    if existing_count > 0:
        print(f"\nWARNING: {NEW_COLL} already has {existing_count} documents.")
        print("Skipping migration to avoid duplicates.")
        print("To re-migrate, run setup_encrypted_consents.py first (drops and recreates the collection).")
        plain_client.close()
        encrypted_client.close()
        return

    # Read all documents from old collection
    old_docs = list(old_coll.find())
    print(f"\nFound {len(old_docs)} documents in {DB_NAME}.{OLD_COLL}")

    if not old_docs:
        print("Nothing to migrate.")
        plain_client.close()
        encrypted_client.close()
        return

    # Migrate each document
    migrated = 0
    errors = 0
    for doc in old_docs:
        consent_id = doc.get("ConsentId", "unknown")
        # Remove _id so MongoDB generates a new one
        doc.pop("_id", None)
        try:
            new_coll.insert_one(doc)
            migrated += 1
        except Exception as e:
            print(f"  ERROR migrating {consent_id}: {e}")
            errors += 1

    print(f"\nMigrated: {migrated}, Errors: {errors}")

    # Verify: read back and check decryption
    print("\nVerifying decryption...")
    verified = 0
    for doc in old_docs:
        consent_id = doc.get("ConsentId")
        if not consent_id:
            continue
        result = new_coll.find_one({"ConsentId": consent_id})
        if result and result.get("Consumer", {}).get("UserName") == doc.get("Consumer", {}).get("UserName"):
            verified += 1
        else:
            print(f"  VERIFY FAILED: {consent_id}")

    print(f"Verified: {verified}/{migrated}")

    # Verify encryption on disk for one document
    if migrated > 0:
        sample_id = old_docs[0].get("ConsentId")
        raw = plain_client[DB_NAME][NEW_COLL].find_one({"ConsentId": sample_id})
        if raw:
            raw_username = raw.get("Consumer", {}).get("UserName")
            raw_type = type(raw_username).__name__
            if raw_type == "Binary":
                print(f"\nEncryption on disk confirmed: Consumer.UserName is Binary for {sample_id}")
            else:
                print(f"\nWARNING: Consumer.UserName is {raw_type} for {sample_id}, expected Binary")

    # Cleanup
    plain_client.close()
    encrypted_client.close()

    print("\n" + "=" * 60)
    print(f"Migration complete. Old collection '{OLD_COLL}' preserved as archive.")
    print("=" * 60)


if __name__ == "__main__":
    main()
