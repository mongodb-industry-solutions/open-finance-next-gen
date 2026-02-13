from typing import Dict, List, Optional
from datetime import datetime, timezone
from database.connection import MongoDBConnection
from services.consents.consent_state_machine import can_retrieve_data

import logging

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class CustomerDataService:
    """This class provides consent-gated data retrieval from external institutions."""

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        consents_collection_name: str,
        external_accounts_collection_name: str,
        external_products_collection_name: str,
        external_transactions_collection_name: str,
        external_repayment_history_collection_name: str,
        external_customer_identification_collection_name: str
    ):
        """Initialize the CustomerDataService with MongoDB connection and collection names.

        Args:
            connection (MongoDBConnection): The MongoDB connection instance.
            db_name (str): The name of the database.
            consents_collection_name (str): The name of the consents collection.
            external_accounts_collection_name (str): The name of the external accounts collection.
            external_products_collection_name (str): The name of the external products collection.
            external_transactions_collection_name (str): The name of the external transactions collection.
            external_repayment_history_collection_name (str): The name of the external repayment history collection.
            external_customer_identification_collection_name (str): The name of the external customer identification collection.

        Returns:
            None
        """
        self.consents_collection = connection.get_collection(db_name, consents_collection_name)
        self.external_accounts_collection = connection.get_collection(db_name, external_accounts_collection_name)
        self.external_products_collection = connection.get_collection(db_name, external_products_collection_name)
        self.external_transactions_collection = connection.get_collection(db_name, external_transactions_collection_name)
        self.external_repayment_history_collection = connection.get_collection(db_name, external_repayment_history_collection_name)
        self.external_customer_identification_collection = connection.get_collection(db_name, external_customer_identification_collection_name)

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
        # Step 1: Load consent
        consent = self.consents_collection.find_one({"ConsentId": consent_id})
        if not consent:
            raise ValueError(f"Consent '{consent_id}' not found.")

        # Step 2: Check if consent has expired (for duration-based consents)
        expiration_dt = consent.get("ExpirationDateTime")
        if expiration_dt:
            # Handle both naive and aware datetimes from MongoDB
            now = datetime.now(timezone.utc)
            if expiration_dt.tzinfo is None:
                # MongoDB stored as naive UTC, make it aware
                expiration_dt = expiration_dt.replace(tzinfo=timezone.utc)
            if expiration_dt < now:
                # Mark as EXPIRED before rejecting
                self._expire_consent(consent_id)
                raise ValueError(f"Consent '{consent_id}' has expired.")

        # Step 3: Verify consent status is AUTHORISED
        status = consent.get("Status")
        if not can_retrieve_data(status):
            if status == "CONSUMED":
                raise ValueError(f"Consent '{consent_id}' has already been used (one-time consent).")
            elif status == "AWAITING_AUTHORISATION":
                raise ValueError(f"Consent '{consent_id}' is not yet authorized. Please approve it first.")
            elif status in ("REJECTED", "REVOKED", "EXPIRED"):
                raise ValueError(f"Consent '{consent_id}' is no longer valid (status: {status}).")
            else:
                raise ValueError(f"Consent '{consent_id}' cannot be used for data retrieval (status: {status}).")

        # Step 4: Verify consent belongs to requesting user
        consent_user = consent.get("Consumer", {}).get("UserName")
        if consent_user != user_name:
            raise ValueError("Unauthorized: This consent does not belong to you.")

        # Step 5: Extract source institution, purpose, and permissions
        source_institution = consent.get("SourceInstitution", {}).get("InstitutionName")
        purpose = consent.get("Purpose")
        permissions = consent.get("Permissions", [])

        if not source_institution:
            raise ValueError("Consent is missing source institution information.")
        if not purpose:
            raise ValueError("Consent is missing purpose information.")

        logging.info(f"Retrieving data for user {user_name} from {source_institution} for purpose {purpose}")

        # Step 6: Query data based on purpose, gated by consent permissions
        result = self._query_data_by_purpose(user_name, source_institution, purpose, permissions)

        # Step 7: Mark as CONSUMED only for one-time consents
        consent_type = consent.get("ConsentType", "DURATION_BASED")
        if consent_type == "ONE_TIME":
            self._consume_consent(consent_id)
            result["consent_status"] = "CONSUMED"
        else:
            # Duration-based consents stay AUTHORISED until they expire
            result["consent_status"] = "AUTHORISED"

        # Step 8: Add consent info to result
        result["consent_id"] = consent_id
        result["consent_type"] = consent_type
        result["source_institution"] = source_institution
        result["purpose"] = purpose

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
            "repayment_history": None,
            "customer_identification": None
        }

        if purpose in ("PERSONAL_LOAN_PORTABILITY", "PAYROLL_LOAN_PORTABILITY", "VEHICLE_LOAN_PORTABILITY"):
            # Credit portability: retrieve loan data gated by permissions
            if "LOANS_READ" in permissions:
                products = list(self.external_products_collection.find({
                    "ProductCustomer.UserName": user_name,
                    "ProductBank": institution_name,
                    "ProductType": "Loan"
                }))
                result["products"] = products
                logging.info(f"{purpose}: Retrieved {len(products)} loan products")

            if "ACCOUNTS_READ" in permissions:
                accounts = list(self.external_accounts_collection.find({
                    "AccountUser.UserName": user_name,
                    "AccountBank": institution_name
                }))
                result["accounts"] = accounts
                logging.info(f"{purpose}: Retrieved {len(accounts)} accounts")

            if "TRANSACTIONS_READ" in permissions:
                transactions = list(self.external_transactions_collection.find({
                    "TransactionUser.UserName": user_name,
                    "TransactionBank": institution_name
                }))
                result["transactions"] = transactions
                logging.info(f"{purpose}: Retrieved {len(transactions)} transactions")

            if "REPAYMENT_HISTORY_READ" in permissions:
                repayment_history = list(self.external_repayment_history_collection.find({
                    "PaymentUser.UserName": user_name,
                    "ProductBank": institution_name
                }))
                result["repayment_history"] = repayment_history
                logging.info(f"{purpose}: Retrieved {len(repayment_history)} repayment records")

            if "CUSTOMER_IDENTIFICATION_READ" in permissions:
                customer_identification = list(self.external_customer_identification_collection.find({
                    "CustomerUser.UserName": user_name,
                    "CustomerBank": institution_name
                }))
                result["customer_identification"] = customer_identification
                logging.info(f"{purpose}: Retrieved {len(customer_identification)} customer identification records")

        elif purpose == "FINANCIAL_ADVICE":
            # Financial advice: transactions and accounts only
            if "TRANSACTIONS_READ" in permissions:
                transactions = list(self.external_transactions_collection.find({
                    "TransactionUser.UserName": user_name,
                    "TransactionBank": institution_name
                }))
                result["transactions"] = transactions
                logging.info(f"FINANCIAL_ADVICE: Retrieved {len(transactions)} transactions")

            if "ACCOUNTS_READ" in permissions:
                accounts = list(self.external_accounts_collection.find({
                    "AccountUser.UserName": user_name,
                    "AccountBank": institution_name
                }))
                result["accounts"] = accounts
                logging.info(f"FINANCIAL_ADVICE: Retrieved {len(accounts)} accounts")

        else:
            logging.warning(f"Unknown purpose: {purpose}")

        return result

    def _consume_consent(self, consent_id: str) -> None:
        """Mark a one-time consent as consumed after data retrieval.

        Args:
            consent_id (str): The ConsentId to consume.
        """
        now = datetime.now(timezone.utc)

        self.consents_collection.update_one(
            {"ConsentId": consent_id},
            {
                "$set": {
                    "Status": "CONSUMED",
                    "StatusUpdateDateTime": now
                },
                "$push": {
                    "StatusHistory": {
                        "Status": "CONSUMED",
                        "DateTime": now,
                        "Reason": "One-time consent used"
                    }
                }
            }
        )
        logging.info(f"One-time consent {consent_id} marked as CONSUMED")

    def _expire_consent(self, consent_id: str) -> None:
        """Mark a consent as expired when ExpirationDateTime has passed.

        Args:
            consent_id (str): The ConsentId to expire.
        """
        now = datetime.now(timezone.utc)

        self.consents_collection.update_one(
            {"ConsentId": consent_id},
            {
                "$set": {
                    "Status": "EXPIRED",
                    "StatusUpdateDateTime": now
                },
                "$push": {
                    "StatusHistory": {
                        "Status": "EXPIRED",
                        "DateTime": now,
                        "Reason": "Consent duration exceeded"
                    }
                }
            }
        )
        logging.info(f"Consent {consent_id} marked as EXPIRED")
