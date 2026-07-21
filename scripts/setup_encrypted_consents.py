"""
One-time setup: Create the encrypted consents collection with Queryable Encryption.

Creates the `openbankingConsents` collection in the LEAFYBANK_DB_NAME database,
generates data encryption keys (DEKs) for each encrypted field, creates indexes,
and saves the resulting encrypted_fields_map to encryption_config.json for runtime use.

Supports two KMS providers:
  KMS_PROVIDER=local (default) — uses master-key.bin for dev
  KMS_PROVIDER=aws             — uses AWS KMS (requires AWS_KMS_KEY_ARN)

Usage:
    cd backend && poetry run python ../scripts/setup_encrypted_consents.py
"""

import os
import sys
from pathlib import Path

from bson import json_util
from bson.codec_options import CodecOptions
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.encryption import ClientEncryption
from pymongo.encryption_options import AutoEncryptionOpts

# Load .env from backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
load_dotenv(BACKEND_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    print("ERROR: MONGODB_URI not set in backend/.env")
    sys.exit(1)

DB_NAME = os.getenv("LEAFYBANK_DB_NAME", "leafy_bank_bian")
COLL_NAME = "openbankingConsents"
KEY_VAULT_NAMESPACE = "encryption.__keyVault_consents"
MASTER_KEY_PATH = BACKEND_DIR / "master-key.bin"
CRYPT_SHARED_LIB_PATH = os.getenv(
    "CRYPT_SHARED_LIB_PATH",
    str(BACKEND_DIR / "lib" / "mongo_crypt_v1.dylib"),
)
CONFIG_OUTPUT_PATH = BACKEND_DIR / "encryption_config.json"

# 4 encrypted fields (Purpose excluded — can be None/BSON null)
ENCRYPTED_FIELDS = {
    "fields": [
        {
            "path": "Consumer.UserName",
            "bsonType": "string",
            "keyId": None,
            "queries": [{"queryType": "equality"}],
        },
        {
            "path": "Consumer.UserId",
            "bsonType": "string",
            "keyId": None,
        },
        {
            "path": "Permissions",
            "bsonType": "array",
            "keyId": None,
        },
        {
            "path": "SourceInstitution.InstitutionName",
            "bsonType": "string",
            "keyId": None,
        },
    ]
}


KMS_PROVIDER = os.getenv("KMS_PROVIDER", "local")
AWS_KMS_KEY_ARN = os.getenv("AWS_KMS_KEY_ARN")
AWS_KMS_REGION = os.getenv("AWS_KMS_REGION", "us-east-1")


def load_master_key() -> bytes:
    """Load the existing master key from file."""
    if not MASTER_KEY_PATH.exists():
        print(f"ERROR: Master key not found at {MASTER_KEY_PATH}")
        print("Generate one with: python -c \"import os; open('master-key.bin','wb').write(os.urandom(96))\"")
        sys.exit(1)
    key = MASTER_KEY_PATH.read_bytes()
    if len(key) != 96:
        print(f"ERROR: Master key must be 96 bytes, got {len(key)}")
        sys.exit(1)
    print(f"Loaded master key from {MASTER_KEY_PATH}")
    return key


def build_kms_config() -> tuple[dict, str, dict | None]:
    """Build KMS providers, provider name, and master_key arg.

    For aws: credentials auto-discovered from AWS SSO (local) or IRSA (Kanopy).
             Requires AWS_KMS_KEY_ARN env var.
    For local: reads master-key.bin from disk.

    Returns:
        (kms_providers, kms_provider_name, master_key_arg)
        master_key_arg is None for local, dict for aws.
    """
    if KMS_PROVIDER == "aws":
        if not AWS_KMS_KEY_ARN:
            print("ERROR: KMS_PROVIDER=aws but AWS_KMS_KEY_ARN not set")
            sys.exit(1)
        print(f"Using AWS KMS: {AWS_KMS_KEY_ARN}")
        kms_providers = {"aws": {}}
        master_key_arg = {"key": AWS_KMS_KEY_ARN, "region": AWS_KMS_REGION}
        return kms_providers, "aws", master_key_arg

    master_key = load_master_key()
    return {"local": {"key": master_key}}, "local", None


def main():
    print("=" * 60)
    print("Setup: Encrypted Consents Collection")
    print(f"KMS Provider: {KMS_PROVIDER}")
    print("=" * 60)

    # Step 1: Build KMS config
    kms_providers, kms_provider_name, master_key_arg = build_kms_config()

    # Step 2: Key vault setup
    key_vault_client = MongoClient(MONGODB_URI)
    kv_db, kv_coll = KEY_VAULT_NAMESPACE.split(".", 1)
    key_vault = key_vault_client[kv_db][kv_coll]
    key_vault.create_index(
        "keyAltNames",
        unique=True,
        partialFilterExpression={"keyAltNames": {"$exists": True}},
    )
    print(f"Key vault ready: {KEY_VAULT_NAMESPACE}")

    # Step 2b: Clean existing DEKs from key vault (idempotent re-runs)
    # Only delete DEKs created by this app's KMS provider — safe for shared clusters
    dek_filter = {"masterKey.provider": {"$in": ["local", KMS_PROVIDER]}}
    existing_dek_count = key_vault.count_documents(dek_filter)
    if existing_dek_count > 0:
        print(f"Cleaning {existing_dek_count} existing DEK(s) from key vault (provider: local or {KMS_PROVIDER})...")
        key_vault.delete_many(dek_filter)
        print("Key vault cleaned.")
    else:
        print("Key vault is empty — no cleanup needed.")

    # Step 3: Create encrypted collection
    client_encryption = ClientEncryption(
        kms_providers,
        KEY_VAULT_NAMESPACE,
        key_vault_client,
        CodecOptions(),
    )

    db = key_vault_client[DB_NAME]

    # Drop existing collection if it exists (idempotent re-runs)
    existing_colls = db.list_collection_names()
    if COLL_NAME in existing_colls:
        print(f"Dropping existing collection: {DB_NAME}.{COLL_NAME}")
        db.drop_collection(COLL_NAME)

    print(f"\nCreating encrypted collection: {DB_NAME}.{COLL_NAME}")
    create_kwargs = {"kms_provider": kms_provider_name}
    if master_key_arg:
        create_kwargs["master_key"] = master_key_arg
    _, ef_map = client_encryption.create_encrypted_collection(
        db, COLL_NAME, ENCRYPTED_FIELDS, **create_kwargs
    )
    print("Encrypted collection created with auto-generated data keys.")

    # Step 4: Create indexes on unencrypted fields
    encrypted_coll_plain = key_vault_client[DB_NAME][COLL_NAME]
    encrypted_coll_plain.create_index("ConsentId", unique=True)
    # NOTE: TTL indexes are NOT allowed on QE-encrypted collections (MongoDB restriction).
    # Consent expiration is handled in application code (consent_service checks ExpirationDateTime).
    # NOTE: Consumer.UserName index NOT created — QE handles equality queries internally.
    print("Indexes created: ConsentId (unique)")

    # Step 5: Save encryption config for runtime
    config = {
        "key_vault_namespace": KEY_VAULT_NAMESPACE,
        "encrypted_fields_map": {
            f"{DB_NAME}.{COLL_NAME}": ef_map,
        },
    }

    config_json = json_util.dumps(config, indent=2)
    CONFIG_OUTPUT_PATH.write_text(config_json)
    print(f"\nEncryption config saved to: {CONFIG_OUTPUT_PATH}")

    # Step 6: Verify round-trip
    print("\nVerifying config round-trip...")
    loaded = json_util.loads(CONFIG_OUTPUT_PATH.read_text())
    ef_key = f"{DB_NAME}.{COLL_NAME}"
    loaded_fields = loaded["encrypted_fields_map"][ef_key]["fields"]
    print(f"  Fields in config: {len(loaded_fields)}")
    for f in loaded_fields:
        key_id_type = type(f["keyId"]).__name__
        queryable = "equality" if f.get("queries") else "no"
        print(f"  - {f['path']} ({f['bsonType']}, keyId: {key_id_type}, queryable: {queryable})")

    # Step 7: Quick insert/query test
    print("\nRunning quick insert/query test...")
    auto_opts = AutoEncryptionOpts(
        kms_providers,
        KEY_VAULT_NAMESPACE,
        encrypted_fields_map={ef_key: ef_map},
        crypt_shared_lib_path=CRYPT_SHARED_LIB_PATH,
    )
    encrypted_client = MongoClient(MONGODB_URI, auto_encryption_opts=auto_opts)
    encrypted_coll = encrypted_client[DB_NAME][COLL_NAME]

    from datetime import datetime, timezone

    test_doc = {
        "ConsentId": "__setup_test__",
        "Status": "TEST",
        "ConsentType": "DURATION_BASED",
        "Consumer": {"UserName": "__test_user__", "UserId": "__test_id__"},
        "Permissions": ["TEST_PERM"],
        "Purpose": None,  # Verify None works (Purpose not encrypted)
        "SourceInstitution": {"InstitutionName": "Test Bank", "InstitutionId": "test"},
        "CreationDateTime": datetime.now(timezone.utc),
        "StatusUpdateDateTime": datetime.now(timezone.utc),
    }

    encrypted_coll.insert_one(test_doc)
    result = encrypted_coll.find_one({"Consumer.UserName": "__test_user__"})
    if result and result["Consumer"]["UserName"] == "__test_user__":
        print("  Insert + equality query on encrypted field: OK")
    else:
        print("  ERROR: Query failed!")
        sys.exit(1)

    # Verify encryption on disk
    raw = key_vault_client[DB_NAME][COLL_NAME].find_one({"ConsentId": "__setup_test__"})
    raw_username_type = type(raw.get("Consumer", {}).get("UserName")).__name__
    if raw_username_type == "Binary":
        print("  Encryption on disk verified: Consumer.UserName is Binary")
    else:
        print(f"  WARNING: Consumer.UserName is {raw_username_type}, expected Binary")

    # Clean up test doc
    encrypted_coll.delete_one({"ConsentId": "__setup_test__"})
    print("  Test document cleaned up.")

    # Cleanup
    client_encryption.close()
    encrypted_client.close()
    key_vault_client.close()

    print("\n" + "=" * 60)
    print("Setup complete!")
    print(f"  Collection: {DB_NAME}.{COLL_NAME}")
    print(f"  Config: {CONFIG_OUTPUT_PATH}")
    print(f"  Encrypted fields: {len(ENCRYPTED_FIELDS['fields'])}")
    print(f"  Queryable fields: Consumer.UserName (equality)")
    print("=" * 60)


if __name__ == "__main__":
    main()
