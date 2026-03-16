from fastapi import Security, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database.connection import MongoDBConnection
from database.encrypted_connection import create_encrypted_connection, load_encryption_config
from pymongo.collection import Collection
from services.auth import Auth
import os
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
OPENFINANCE_DB_NAME = os.getenv("OPENFINANCE_DB_NAME")

# Add the HTTPBearer security scheme
bearer_scheme = HTTPBearer()

# Singleton MongoDB connection - reused across all requests
_mongo_connection: MongoDBConnection | None = None


def get_mongo_connection() -> MongoDBConnection:
    global _mongo_connection
    if _mongo_connection is None:
        _mongo_connection = MongoDBConnection(uri=MONGODB_URI)
    return _mongo_connection


# Singleton encrypted MongoDB connection - for Queryable Encryption on consents
_encrypted_mongo_connection: MongoDBConnection | None = None


def get_encrypted_mongo_connection() -> MongoDBConnection:
    """Singleton encrypted MongoClient for Queryable Encryption on consents."""
    global _encrypted_mongo_connection
    if _encrypted_mongo_connection is None:
        config_path = os.getenv(
            "ENCRYPTION_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "encryption_config.json"),
        )
        if not os.path.exists(config_path):
            raise RuntimeError(
                f"encryption_config.json not found at {config_path}. "
                "Run: cd backend && poetry run python ../scripts/setup_encrypted_consents.py"
            )
        config = load_encryption_config(config_path)
        _encrypted_mongo_connection = create_encrypted_connection(MONGODB_URI, config)
    return _encrypted_mongo_connection


def get_tokens_collection(db_connection: MongoDBConnection = Depends(get_mongo_connection)) -> Collection:
    return db_connection.get_database(OPENFINANCE_DB_NAME)["tokens"]


def get_auth(mongo_connection: MongoDBConnection = Depends(get_mongo_connection)) -> Auth:
    return Auth(connection=mongo_connection, db_name=OPENFINANCE_DB_NAME)


def get_bearer_token(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)) -> str:
    """Extract and validate the Bearer token from the Authorization header."""
    if credentials.scheme != "Bearer":
        raise HTTPException(status_code=403, detail="Bearer token is malformed or missing.")
    return credentials.credentials
