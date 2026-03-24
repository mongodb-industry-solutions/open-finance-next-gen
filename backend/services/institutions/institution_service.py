import logging
from typing import Optional
from database.connection import MongoDBConnection

logger = logging.getLogger(__name__)


class InstitutionService:
    """This class provides methods to interact with institutions in the database."""

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        collection_name: str,
        external_accounts_collection_name: str = "external_accounts",
    ):
        """Initialize the InstitutionService with the MongoDB connection and collection name.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.
            collection_name (str): The name of the institutions collection.
            external_accounts_collection_name (str): The name of the external accounts collection.

        Returns:
            None
        """
        self.institutions_collection = connection.get_collection(db_name, collection_name)
        self.external_accounts_collection = connection.get_collection(
            db_name, external_accounts_collection_name
        )

    def list_institutions(self) -> list[dict]:
        """Retrieve all institutions.

        Returns:
            list[dict]: A list of all institutions.
        """
        institutions = list(self.institutions_collection.find({}))
        logger.info(f"Retrieved {len(institutions)} institutions")
        return institutions

    def get_institution_by_name(self, institution_name: str) -> Optional[dict]:
        """Retrieve an institution by its name.

        Args:
            institution_name (str): The institution name to search for (InstitutionName field).

        Returns:
            Optional[dict]: The institution document if found, otherwise None.
        """
        institution = self.institutions_collection.find_one(
            {"InstitutionName": institution_name}
        )
        if institution:
            logger.info(f"Institution found: {institution_name}")
        else:
            logger.info(f"No institution found with name: {institution_name}")
        return institution

    def list_institutions_for_user(self, user_name: str) -> list[dict]:
        """Retrieve institutions where the user has external accounts.

        Uses a distinct query on external_accounts to find which banks the user
        has data at, then returns only those institution documents.

        Args:
            user_name (str): The username to filter by.

        Returns:
            list[dict]: Institutions where the user has accounts.
        """
        bank_names = self.external_accounts_collection.distinct(
            "AccountBank", {"AccountUser.UserName": user_name}
        )
        if not bank_names:
            logger.info(f"No external accounts found for user: {user_name}")
            return []

        institutions = list(
            self.institutions_collection.find({"InstitutionName": {"$in": bank_names}})
        )
        logger.info(
            f"Retrieved {len(institutions)} institutions for user {user_name} "
            f"(banks: {bank_names})"
        )
        return institutions

    def get_active_institutions(self) -> list[dict]:
        """Retrieve all active institutions.

        Returns:
            list[dict]: A list of all active institutions.
        """
        query = {"InstitutionStatus": "Active"}
        institutions = list(self.institutions_collection.find(query))
        logger.info(f"Retrieved {len(institutions)} active institutions")
        return institutions

    def institution_exists(self, institution_name: str) -> bool:
        """Check if an institution exists by name.

        Args:
            institution_name (str): The institution name to check.

        Returns:
            bool: True if institution exists, False otherwise.
        """
        institution = self.institutions_collection.find_one(
            {"InstitutionName": institution_name}
        )
        return institution is not None
