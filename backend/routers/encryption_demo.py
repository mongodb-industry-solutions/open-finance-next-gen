"""
Encryption Demo Router

Demonstrates MongoDB Queryable Encryption by showing the same consent document
as seen by the encrypted client (decrypted) vs a plain client (encrypted binary blobs).
"""

from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from encoder.json_encoder import MyJSONEncoder

from dependencies import get_mongo_connection, get_encrypted_mongo_connection

import json
import os
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")
ENCRYPTED_CONSENTS_COLLECTION = "encrypted_consents"

ENCRYPTED_FIELD_PATHS = [
    "Consumer.UserName",
    "Consumer.UserId",
    "Permissions",
    "SourceInstitution.InstitutionName",
]

limiter = Limiter(key_func=get_remote_address)


def _get_nested_value(doc: dict, dotted_path: str):
    """Extract a value from a nested dict using a dotted path."""
    parts = dotted_path.split(".")
    current = doc
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _serialize_value(val):
    """Convert a value to a JSON-safe representation."""
    type_name = type(val).__name__
    if type_name == "Binary":
        return f"Binary({len(val)} bytes)"
    if isinstance(val, list):
        return val
    return val


@router.get("/compare/{consent_id}")
@limiter.limit("60/minute")
async def compare_encrypted_vs_plain(request: Request, consent_id: str):
    """
    Show the same consent document through two lenses:
    1. Encrypted client: fields are decrypted (normal JSON)
    2. Plain client: sensitive fields appear as Binary blobs

    This proves encryption is active on disk.
    """
    encrypted_conn = get_encrypted_mongo_connection()
    plain_conn = get_mongo_connection()

    encrypted_coll = encrypted_conn.get_collection(OPENFINANCE_DB_NAME, ENCRYPTED_CONSENTS_COLLECTION)
    plain_coll = plain_conn.get_collection(OPENFINANCE_DB_NAME, ENCRYPTED_CONSENTS_COLLECTION)

    # Read via encrypted client (decrypted view)
    decrypted_doc = encrypted_coll.find_one({"ConsentId": consent_id})
    if not decrypted_doc:
        raise HTTPException(status_code=404, detail=f"Consent '{consent_id}' not found.")

    # Read via plain client (raw/encrypted view)
    raw_doc = plain_coll.find_one({"ConsentId": consent_id})

    # Build field-by-field comparison
    fields_comparison = {}
    all_encrypted = True
    for field_path in ENCRYPTED_FIELD_PATHS:
        decrypted_val = _get_nested_value(decrypted_doc, field_path)
        raw_val = _get_nested_value(raw_doc, field_path) if raw_doc else None

        is_encrypted = type(raw_val).__name__ == "Binary"
        if not is_encrypted:
            all_encrypted = False

        fields_comparison[field_path] = {
            "decrypted_value": _serialize_value(decrypted_val),
            "raw_type": type(raw_val).__name__,
            "is_encrypted": is_encrypted,
        }

    # Build decrypted document view (excluding _id and internal QE fields)
    doc_view = {}
    for key, val in decrypted_doc.items():
        if key.startswith("_") or key.startswith("__"):
            continue
        doc_view[key] = val

    return json.loads(json.dumps({
        "consent_id": consent_id,
        "encryption_verified": all_encrypted,
        "encrypted_fields": fields_comparison,
        "queryable_fields": ["Consumer.UserName"],
        "decrypted_document": doc_view,
        "encryption_metadata": {
            "encryption_type": "Queryable Encryption (automatic)",
            "kms_provider": "local (demo) — production: AWS KMS",
            "atlas_tier": "M30 Dedicated",
            "collection": ENCRYPTED_CONSENTS_COLLECTION,
        },
    }, cls=MyJSONEncoder))
