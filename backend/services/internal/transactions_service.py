import logging
from bson import ObjectId
from typing import Union
from database.connection import MongoDBConnection

logger = logging.getLogger(__name__)


class TransactionsService:
    """This class reads customer transactions from the database (BIAN schema)."""

    def __init__(self, connection: MongoDBConnection, db_name: str):
        """Initialize the TransactionsService with the MongoDB connection and database name.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.

        Returns:
            None
        """
        self.db = connection.get_database(db_name)
        self.transactions_collection = self.db['transactions']
        self.customers_collection = self.db['customers']
        self.accounts_collection = self.db['accounts']

    def _resolve_customer(self, user_identifier: Union[str, ObjectId]) -> Union[dict, None]:
        """Resolve a user identifier (userName, ObjectId, or customerId) to a customer doc."""
        if isinstance(user_identifier, ObjectId):
            query = {"_id": user_identifier}
        elif isinstance(user_identifier, str) and user_identifier.startswith("CUST-"):
            query = {"customerId": user_identifier}
        else:
            query = {"identification.userName": user_identifier}
        return self.customers_collection.find_one(query, {"customerId": 1})

    def _account_ids_for_customer(self, customer_id: str) -> list[str]:
        """Return the BIAN accountId list owned by a customer."""
        accounts = self.accounts_collection.find(
            {"customerSnapshot.customerId": customer_id}, {"accountId": 1})
        return [acct["accountId"] for acct in accounts if acct.get("accountId")]

    def is_valid_user(self, user_identifier: Union[str, ObjectId]) -> bool:
        """Check if the customer exists in the system.
        Args:
            user_identifier (Union[str, ObjectId]): userName, ObjectId, or customerId.
        Returns:
            bool: True if the customer exists, False otherwise.
        """
        return self._resolve_customer(user_identifier) is not None

    def get_all_transactions_for_user(self, user_identifier: Union[str, ObjectId]) -> list[dict]:
        """Get all transactions for a customer (both incoming and outgoing).

        Under the BIAN schema, transactions reference parties by accountId, so this
        resolves the customer's accounts first, then matches on payer/payee accountId.

        Args:
            user_identifier (Union[str, ObjectId]): userName, ObjectId, or customerId.

        Returns:
            list[dict]: All transactions for the customer, sorted by bookingDate descending.
        """
        customer = self._resolve_customer(user_identifier)
        if not customer:
            logger.info(f"No customer found for identifier {user_identifier}")
            return []

        account_ids = self._account_ids_for_customer(customer["customerId"])
        if not account_ids:
            return []

        transactions = list(self.transactions_collection.find({
            "$or": [
                {"payer.accountId": {"$in": account_ids}},
                {"payee.accountId": {"$in": account_ids}},
            ]
        }))

        # Sort by booking date descending (BIAN bookingDate is an ISO date string)
        transactions.sort(key=lambda x: x.get("bookingDate") or "", reverse=True)

        logger.info(f"Retrieved {len(transactions)} total transactions for {customer['customerId']}")
        return transactions

    def get_recent_transactions_for_user(self, user_identifier: Union[str, ObjectId]) -> list[dict]:
        """Get the 20 most recent transactions for a customer.

        The legacy RecentTransactions[] array no longer exists under the BIAN schema,
        so this returns the latest 20 transactions by bookingDate.

        Args:
            user_identifier (Union[str, ObjectId]): userName, ObjectId, or customerId.
        Returns:
            list[dict]: Up to 20 recent transactions, sorted by bookingDate descending.
        """
        return self.get_all_transactions_for_user(user_identifier)[:20]
