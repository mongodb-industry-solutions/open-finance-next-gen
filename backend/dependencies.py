from fastapi import Security, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database.connection import MongoDBConnection
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


def get_tokens_collection(db_connection: MongoDBConnection = Depends(get_mongo_connection)) -> Collection:
    return db_connection.get_database(OPENFINANCE_DB_NAME)["tokens"]


def get_auth(mongo_connection: MongoDBConnection = Depends(get_mongo_connection)) -> Auth:
    return Auth(connection=mongo_connection, db_name=OPENFINANCE_DB_NAME)


def get_bearer_token(credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)) -> str:
    """Extract and validate the Bearer token from the Authorization header."""
    if credentials.scheme != "Bearer":
        raise HTTPException(status_code=403, detail="Bearer token is malformed or missing.")
    return credentials.credentials
