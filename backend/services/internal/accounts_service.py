import logging
from database.connection import MongoDBConnection

logger = logging.getLogger(__name__)


class AccountsService:
    """This class provides methods to interact with accounts in the database (BIAN schema)."""

    def __init__(self, connection: MongoDBConnection, db_name: str, accounts_collection_name: str):
        """Initialize the AccountService with the MongoDB connection and collection name.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.
            accounts_collection_name (str): The name of the accounts collection.

        Returns:
            None
        """
        self.accounts_collection = connection.get_collection(
            db_name, accounts_collection_name)

    def get_accounts_for_user(self, customer_id: str) -> list[dict]:
        """Retrieve accounts for a specific customer.

        Under the BIAN schema, accounts are linked to their owner via
        `customerSnapshot.customerId`, so callers must resolve any user identifier
        to a customerId first (see UsersService.get_customer_id).

        Args:
            customer_id (str): The BIAN customerId (e.g. "CUST-00528224").

        Returns:
            list[dict]: A list of accounts associated with the customer.
        """
        accounts = list(self.accounts_collection.find(
            {"customerSnapshot.customerId": customer_id}))
        return accounts
