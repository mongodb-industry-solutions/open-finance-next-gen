"""Seed the mcc_codes collection with embeddings from voyage-finance-2.

Usage:
    cd repos/open-finance-next-gen/backend && poetry run python ../scripts/seed_mcc_codes.py

Requires MONGODB_URI, LEAFYBANK_DB_NAME, and VOYAGE_API_KEY in backend/.env
"""

import json
import os
import sys

import voyageai
from dotenv import load_dotenv
from pymongo import MongoClient

# Load env from the backend directory
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPT_DIR, "..", "backend")
load_dotenv(os.path.join(BACKEND_DIR, ".env"))

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("LEAFYBANK_DB_NAME")
COLLECTION = "mcc_codes"
SEED_FILE = os.path.join(BACKEND_DIR, "data", "seed", "mcc_codes.json")

VOYAGE_MODEL = "voyage-finance-2"
EMBEDDING_DIMS = 1024


def main():
    # Validate env
    if not MONGODB_URI:
        print("ERROR: MONGODB_URI not set in .env")
        sys.exit(1)
    if not os.getenv("VOYAGE_API_KEY"):
        print("ERROR: VOYAGE_API_KEY not set in .env")
        sys.exit(1)

    # Load seed data
    with open(SEED_FILE) as f:
        mcc_codes = json.load(f)

    print(f"Loaded {len(mcc_codes)} MCC codes from {SEED_FILE}")

    # Generate embeddings in batch
    vo = voyageai.Client()
    embedding_texts = [doc["EmbeddingText"] for doc in mcc_codes]

    print(f"Generating embeddings with {VOYAGE_MODEL} ({EMBEDDING_DIMS} dims)...")
    result = vo.embed(embedding_texts, model=VOYAGE_MODEL, input_type="document")

    # Attach embeddings to documents
    for doc, embedding in zip(mcc_codes, result.embeddings):
        doc["embedding"] = embedding

    print(f"Generated {len(result.embeddings)} embeddings")

    # Insert into MongoDB
    client = MongoClient(MONGODB_URI)
    collection = client[DB_NAME][COLLECTION]

    # Clear existing and insert fresh
    deleted = collection.delete_many({}).deleted_count
    if deleted:
        print(f"Cleared {deleted} existing documents from {DB_NAME}.{COLLECTION}")

    insert_result = collection.insert_many(mcc_codes)
    print(f"Inserted {len(insert_result.inserted_ids)} MCC code documents into {DB_NAME}.{COLLECTION}")

    # Verify
    count = collection.count_documents({})
    sample = collection.find_one({}, {"MCC": 1, "CategoryName": 1, "embedding": {"$slice": 3}})
    embedding_len = len(sample.get("embedding", []))

    print(f"\nVerification: {count} documents in {DB_NAME}.{COLLECTION}")
    print(f"Sample: MCC={sample['MCC']}, Category={sample['CategoryName']}, embedding dims={embedding_len}")

    if embedding_len != EMBEDDING_DIMS:
        print(f"WARNING: Expected {EMBEDDING_DIMS} dims, got {embedding_len}")

    print(f"\nNext step: Create the vector search index in Atlas UI or CLI:")
    print(f"  Index name: mcc_codes_vector_index")
    print(f"  Collection: {DB_NAME}.{COLLECTION}")
    print(f"  Field: embedding -> vector, {EMBEDDING_DIMS} dimensions, cosine similarity")


if __name__ == "__main__":
    main()
