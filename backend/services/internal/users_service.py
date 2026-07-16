import logging
from bson import ObjectId
from typing import Union
from database.connection import MongoDBConnection

logger = logging.getLogger(__name__)


class UsersService:
    """This class provides methods to interact with customers in the database (BIAN schema)."""

    def __init__(self, connection: MongoDBConnection, db_name: str, customers_collection_name: str):
        """Initialize the UsersService with the MongoDB connection and collection name.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.
            customers_collection_name (str): The name of the customers collection.

        Returns:
            None
        """
        self.customers_collection = connection.get_collection(
            db_name, customers_collection_name)

    def get_user(self, user_identifier: Union[str, ObjectId]) -> dict:
        """Retrieve a specific customer by userName, ObjectId, or customerId.
        Args:
            user_identifier (Union[str, ObjectId]): The identifier — userName, ObjectId,
                or BIAN customerId (e.g. "CUST-00528224").
        Returns:
            dict: The customer document if found, otherwise None.
        """
        # Determine the query based on the identifier type/shape
        if isinstance(user_identifier, ObjectId):
            query = {"_id": user_identifier}
        elif isinstance(user_identifier, str) and user_identifier.startswith("CUST-"):
            query = {"customerId": user_identifier}
        else:
            query = {"identification.userName": user_identifier}
        # Retrieve the customer matching the query
        customer = self.customers_collection.find_one(query)
        if customer:
            logger.info(f"Returning customer with ObjectId {customer['_id']}")
            return customer
        else:
            logger.error("No customer found with the given identifier.")
            return None

    def get_customer_id(self, user_identifier: Union[str, ObjectId]) -> Union[str, None]:
        """Resolve any user identifier to the BIAN customerId.

        Used by the accounts/transactions/aggregation paths, which now join on
        `customerSnapshot.customerId` instead of the legacy `AccountUser.UserName`.

        Args:
            user_identifier (Union[str, ObjectId]): userName, ObjectId, or customerId.
        Returns:
            str | None: The customerId (e.g. "CUST-00528224"), or None if not found.
        """
        customer = self.get_user(user_identifier)
        return customer.get("customerId") if customer else None
