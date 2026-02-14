from database.connection import MongoDBConnection
from services.consents.consent_validator import ConsentValidator
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


class RepaymentHistoryService:
    """Consent-gated service for retrieving external loan repayment history."""

    def __init__(
        self,
        connection: MongoDBConnection,
        db_name: str,
        consents_collection_name: str,
        repayment_history_collection_name: str
    ):
        self.repayment_history_collection = connection.get_collection(
            db_name, repayment_history_collection_name
        )
        self.consent_validator = ConsentValidator(connection, db_name, consents_collection_name)

    def get_repayment_history(self, consent_id: str, user_name: str) -> dict:
        """Retrieve loan repayment history from an external bank via consent.

        Args:
            consent_id: The ConsentId authorizing data retrieval.
            user_name: The requesting user's UserName.

        Returns:
            dict: Repayment history records plus consent metadata.

        Raises:
            ValueError: If consent is invalid or missing required permission.
        """
        # Validate consent and get source institution
        consent, source_institution = self.consent_validator.validate_consent(
            consent_id, user_name, "REPAYMENT_HISTORY_READ"
        )

        # Query repayment history filtered by user + institution
        records = list(self.repayment_history_collection.find({
            "PaymentUser.UserName": user_name,
            "ProductBank": source_institution
        }))

        logging.info(
            f"Retrieved {len(records)} repayment history records "
            f"for {user_name} from {source_institution}"
        )

        # Record data access in StatusHistory (audit trail)
        self.consent_validator.record_data_access(consent_id, "REPAYMENT_HISTORY")

        # Consume if one-time consent
        consent_status = self.consent_validator.consume_if_one_time(consent)

        return {
            "repayment_history": records,
            "consent_id": consent_id,
            "consent_status": consent_status,
            "source_institution": source_institution
        }
