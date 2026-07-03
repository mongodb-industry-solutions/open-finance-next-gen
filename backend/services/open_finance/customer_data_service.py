import logging
from typing import Dict, List
from database.connection import MongoDBConnection
from services.consents.consent_validator import ConsentValidator

logger = logging.getLogger(__name__)


class CustomerDataService:
    """This class provides consent-gated data retrieval from external institutions."""

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        consent_validator: ConsentValidator,
        external_accounts_collection_name: str,
        external_products_collection_name: str,
        external_transactions_collection_name: str
    ):
        """Initialize the CustomerDataService with MongoDB connection and collection names.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database holding external institution data.
            consent_validator (ConsentValidator): Validates consents and records their usage.
            external_accounts_collection_name (str): The name of the external accounts collection.
            external_products_collection_name (str): The name of the external products collection.
            external_transactions_collection_name (str): The name of the external transactions collection.

        Returns:
            None
        """
        self.consent_validator = consent_validator
        self.external_accounts_collection = connection.get_collection(db_name, external_accounts_collection_name)
        self.external_products_collection = connection.get_collection(db_name, external_products_collection_name)
        self.external_transactions_collection = connection.get_collection(db_name, external_transactions_collection_name)

    def _build_transaction_query(self, user_name: str, institution_name: str) -> dict:
        """Build MongoDB query for external transactions (BIAN-aligned schema).

        The account holder appears as `payer` on outgoing transactions and `payee`
        on incoming ones, so match on either side. Institution name is only
        recorded in `createdBy` (as "EXTERNAL-<institution>") in this schema.
        """
        return {
            "$or": [
                {"payer.name": user_name},
                {"payee.name": user_name},
            ],
            "createdBy": f"EXTERNAL-{institution_name}",
        }

    def retrieve_data_with_consent(self, consent_id: str, user_name: str) -> Dict:
        """Retrieve external data based on an authorized consent.

        This method:
        1. Validates consent exists and is AUTHORISED
        2. Verifies consent belongs to the requesting user
        3. Retrieves data based on the consent's purpose
        4. Transitions consent to CONSUMED
        5. Returns the retrieved data

        Args:
            consent_id (str): The ConsentId to use for data retrieval.
            user_name (str): The username of the requesting user.

        Returns:
            Dict: Retrieved data with keys: accounts, products, transactions (based on purpose).

        Raises:
            ValueError: If consent is invalid, not authorized, or doesn't belong to user.
        """
        # Step 1: Validate consent (existence, expiry, status, ownership)
        consent, source_institution = self.consent_validator.validate_consent(consent_id, user_name)

        purpose = consent.get("Purpose")
        permissions = consent.get("Permissions", [])

        logger.info(f"Retrieving data for user {user_name} from {source_institution} for purpose {purpose or 'GENERAL_ACCESS'}")

        # Step 2: Query data based on purpose, gated by consent permissions
        result = self._query_data_by_purpose(user_name, source_institution, purpose, permissions)

        # Step 3: Record data access in StatusHistory (audit trail)
        accessed_resources = [k.upper() for k, v in result.items() if v is not None]
        self.consent_validator.record_data_access(consent_id, f"EXTERNAL_DATA ({', '.join(accessed_resources)})")

        # Step 4: Consume one-time consents (duration-based stay AUTHORISED until expiry)
        consent_type = consent.get("ConsentType", "DURATION_BASED")
        result["consent_status"] = self.consent_validator.consume_if_one_time(consent)

        # Step 5: Add consent info to result
        result["consent_id"] = consent_id
        result["consent_type"] = consent_type
        result["source_institution"] = source_institution
        result["purpose"] = purpose or "GENERAL_ACCESS"

        return result

    def _query_data_by_purpose(self, user_name: str, institution_name: str, purpose: str, permissions: List[str]) -> Dict:
        """Query appropriate collections based on consent purpose, gated by permissions.

        Each data source is only queried if the corresponding permission is present
        on the consent. This allows users to remove permissions and gracefully skip
        data sources they didn't consent to share.

        Args:
            user_name (str): The username to query data for.
            institution_name (str): The institution to query data from.
            purpose (str): The consent purpose determining what data to retrieve.
            permissions (List[str]): The permissions granted on the consent.

        Returns:
            Dict: Retrieved data with appropriate keys based on purpose and permissions.
        """
        result = {
            "accounts": None,
            "products": None,
            "transactions": None,
        }

        if purpose == "FINANCIAL_ADVICE" or purpose is None:
            # Financial advice (and general access): accounts, transactions, products
            if "ACCOUNTS_READ" in permissions:
                accounts = list(self.external_accounts_collection.find({
                    "AccountUser.UserName": user_name,
                    "AccountBank": institution_name
                }))
                result["accounts"] = accounts
                logger.info(f"{purpose or 'GENERAL_ACCESS'}: Retrieved {len(accounts)} accounts")

            if "TRANSACTIONS_READ" in permissions:
                txn_query = self._build_transaction_query(user_name, institution_name)
                transactions = list(self.external_transactions_collection.find(txn_query))
                result["transactions"] = transactions
                logger.info(f"{purpose or 'GENERAL_ACCESS'}: Retrieved {len(transactions)} transactions")

            if "LOANS_READ" in permissions:
                products = list(self.external_products_collection.find({
                    "ProductCustomer.UserName": user_name,
                    "ProductBank": institution_name
                }))
                result["products"] = products
                logger.info(f"{purpose or 'GENERAL_ACCESS'}: Retrieved {len(products)} products")

            if "ACCOUNTS_BALANCES_READ" in permissions:
                # Balance data comes from accounts — already retrieved above
                logger.info(f"{purpose or 'GENERAL_ACCESS'}: ACCOUNTS_BALANCES_READ granted (balances included in accounts)")

        else:
            logger.warning(f"Unknown purpose: {purpose}")

        return result

    def retrieve_transactions_with_consent(self, consent_id: str, user_name: str) -> Dict:
        """Retrieve only external transactions based on an authorized consent.

        Args:
            consent_id (str): The ConsentId to use for data retrieval.
            user_name (str): The username of the requesting user.

        Returns:
            Dict: Retrieved transactions with consent metadata.

        Raises:
            ValueError: If consent is invalid, not authorized, or doesn't belong to user.
            PermissionError: If consent lacks TRANSACTIONS_READ permission.
        """
        # Step 1: Validate consent (existence, expiry, status, ownership)
        consent, source_institution = self.consent_validator.validate_consent(consent_id, user_name)

        # Step 2: Verify TRANSACTIONS_READ permission (distinct error for the caller)
        permissions = consent.get("Permissions", [])
        if "TRANSACTIONS_READ" not in permissions:
            raise PermissionError("Consent does not include TRANSACTIONS_READ permission.")

        purpose = consent.get("Purpose")

        logger.info(f"Retrieving external transactions for user {user_name} from {source_institution}")

        # Step 3: Query external transactions
        txn_query = self._build_transaction_query(user_name, source_institution)
        transactions = list(self.external_transactions_collection.find(txn_query))
        logger.info(f"Retrieved {len(transactions)} external transactions")

        # Step 4: Record data access for audit
        self.consent_validator.record_data_access(consent_id, "EXTERNAL_TRANSACTIONS")

        # Step 5: Consume one-time consents (matches retrieve_data_with_consent behavior)
        self.consent_validator.consume_if_one_time(consent)

        return {
            "transactions": transactions,
            "consent_id": consent_id,
            "source_institution": source_institution,
            "purpose": purpose or "GENERAL_ACCESS"
        }
